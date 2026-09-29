"""Управление состоянием фонового процесса мониторинга Gemini.

Предоставляет потокобезопасное хранилище метрик, кэша сертификатов,
истории оповещений и персистентного состояния расписания отчетов.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
import logging
from pathlib import Path
import threading
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class DurableState:
    """Персистентная часть состояния, сохраняемая на диск."""

    last_report_at: str | None = None
    next_report_at: str | None = None
    latest_report_path: str | None = None
    alert_history: list[dict[str, Any]] = field(default_factory=list)


class GeminiRuntimeState:
    """Потокобезопасное состояние фонового сервиса."""

    def __init__(self, state_file_path: str | Path = "gemeni_runtime_state.json") -> None:
        """Инициализирует состояние сервиса и загружает сохраненные данные.

        Args:
            state_file_path: Путь к файлу сохранения персистентного состояния.
        """
        self.state_file = Path(state_file_path).resolve()
        self.lock = threading.RLock()

        self.started_at: datetime = datetime.now()
        self.is_running: bool = False

        self.last_check_at: datetime | None = None
        self.last_check_status: str = "never_run"
        self.last_check_error: str | None = None
        self.next_check_at: datetime | None = None

        self.durable = DurableState()
        self.counts: dict[str, int] = {
            "total": 0,
            "valid": 0,
            "expiring_soon": 0,
            "expired": 0,
            "error": 0,
        }
        self.certificates: list[dict[str, Any]] = []

        self._load_durable()

    def _load_durable(self) -> None:
        """Загружает персистентные данные из файла на диске."""
        if not self.state_file.is_file():
            return

        try:
            raw = json.loads(self.state_file.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self.durable = DurableState(
                    last_report_at=raw.get("last_report_at"),
                    next_report_at=raw.get("next_report_at"),
                    latest_report_path=raw.get("latest_report_path"),
                    alert_history=raw.get("alert_history", [])[-100:],
                )
                logger.info("Персистентное состояние загружено из %s", self.state_file)
        except Exception as exc:
            logger.warning("Не удалось прочитать файл состояния %s: %s", self.state_file, exc)

    def save_durable(self) -> None:
        """Сохраняет персистентную часть состояния на диск."""
        with self.lock:
            try:
                self.state_file.parent.mkdir(parents=True, exist_ok=True)
                data = asdict(self.durable)
                self.state_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            except Exception as exc:
                logger.error("Ошибка сохранения состояния в %s: %s", self.state_file, exc)

    def add_alert(self, title: str, message: str, urgency: str = "normal") -> None:
        """Добавляет запись в историю системных оповещений.

        Args:
            title: Заголовок уведомления.
            message: Текст.
            urgency: Срочность.
        """
        with self.lock:
            record = {
                "timestamp": datetime.now().isoformat(),
                "title": title,
                "message": message,
                "urgency": urgency,
            }
            self.durable.alert_history.append(record)
            if len(self.durable.alert_history) > 100:
                self.durable.alert_history = self.durable.alert_history[-100:]
            self.save_durable()

    def update_check_results(
        self,
        certificates: list[dict[str, Any]],
        counts: dict[str, int],
        error: str | None = None,
        next_check: datetime | None = None,
    ) -> None:
        """Обновляет результаты последней проверки сертификатов.

        Args:
            certificates: Полный список сертификатов.
            counts: Сводная статистика.
            error: Текст ошибки проверки, если возникло исключение.
            next_check: Время следующей запланированной проверки.
        """
        with self.lock:
            self.last_check_at = datetime.now()
            self.last_check_error = error
            self.last_check_status = "error" if error else "success"
            self.next_check_at = next_check
            if not error:
                self.certificates = certificates
                self.counts = counts

    def update_report_result(self, report_path: Path | None, next_report: datetime | None = None) -> None:
        """Обновляет статус генерации регулярного отчета.

        Args:
            report_path: Путь к созданному отчету.
            next_report: Планируемое время следующего отчета.
        """
        with self.lock:
            if report_path:
                self.durable.last_report_at = datetime.now().isoformat()
                self.durable.latest_report_path = str(report_path.resolve())
            if next_report:
                self.durable.next_report_at = next_report.isoformat()
            self.save_durable()

    def get_snapshot(self) -> dict[str, Any]:
        """Возвращает снимок состояния для передачи через API.

        Returns:
            dict[str, Any]: Словарь с полным срезом метрик сервиса.
        """
        with self.lock:
            uptime_seconds = int((datetime.now() - self.started_at).total_seconds())
            return {
                "service": "GeminiCertificateMonitor",
                "running": self.is_running,
                "started_at": self.started_at.isoformat(),
                "uptime_seconds": uptime_seconds,
                "last_check": {
                    "timestamp": self.last_check_at.isoformat() if self.last_check_at else None,
                    "status": self.last_check_status,
                    "error": self.last_check_error,
                    "next_scheduled": self.next_check_at.isoformat() if self.next_check_at else None,
                },
                "last_report": {
                    "timestamp": self.durable.last_report_at,
                    "latest_path": self.durable.latest_report_path,
                    "next_scheduled": self.durable.next_report_at,
                },
                "counts": dict(self.counts),
                "recent_alerts": list(self.durable.alert_history[-10:]),
            }
