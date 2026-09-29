"""Встроенный легковесный REST API для взаимодействия приложения с фоновым процессом.

Использует встроенный ThreadingHTTPServer стандартной библиотеки Python,
не требуя сторонних веб-фреймворков и зависимостей.
"""

from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
import threading
from typing import Any
from urllib.parse import parse_qs, urlsplit

from certificate_analyzer.runtime.gemeni.config import GeminiRuntimeConfig
from certificate_analyzer.runtime.gemeni.monitor import GeminiCertificateMonitor
from certificate_analyzer.runtime.gemeni.state import GeminiRuntimeState

logger = logging.getLogger(__name__)


class GeminiApiServer(ThreadingHTTPServer):
    """Многопоточный HTTP-сервер для локального API сервиса мониторинга."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        config: GeminiRuntimeConfig,
        state: GeminiRuntimeState,
        monitor: GeminiCertificateMonitor,
        stop_event: threading.Event,
    ) -> None:
        """Инициализирует сервер API.

        Args:
            config: Конфигурация процесса.
            state: Состояние сервиса.
            monitor: Монитор сертификатов.
            stop_event: Событие завершения работы процесса.
        """
        self.config = config
        self.state = state
        self.monitor = monitor
        self.stop_event = stop_event
        super().__init__((config.api_host, config.api_port), GeminiApiHandler)


class GeminiApiHandler(BaseHTTPRequestHandler):
    """Обработчик HTTP-запросов к API мониторинга."""

    server: GeminiApiServer

    def do_GET(self) -> None:  # noqa: N802
        """Обрабатывает входящие GET-запросы."""
        if not self._check_auth():
            return

        parsed = urlsplit(self.path)
        path = parsed.path.rstrip("/")
        query = parse_qs(parsed.query)

        # 1. Health check
        if path in ("/health", "/api/v1/health"):
            snapshot = self.server.state.get_snapshot()
            status_val = "error" if snapshot["last_check"]["status"] == "error" else "ok"
            self._send_json(HTTPStatus.OK, {
                "status": status_val,
                "running": snapshot["running"],
                "uptime_seconds": snapshot["uptime_seconds"],
            })
            return

        # 2. Полный статус процесса
        if path in ("/status", "/api/v1/status"):
            self._send_json(HTTPStatus.OK, self.server.state.get_snapshot())
            return

        # 3. Список сертификатов с фильтрацией
        if path in ("/certificates", "/api/v1/certificates"):
            self._handle_get_certificates(query)
            return

        # 4. Список сформированных отчетов
        if path in ("/reports", "/api/v1/reports"):
            self._handle_get_reports()
            return

        # 5. Текущая конфигурация
        if path in ("/config", "/api/v1/config"):
            self._send_json(HTTPStatus.OK, self.server.config.to_dict())
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Маршрут не найден", "path": self.path})

    def do_POST(self) -> None:  # noqa: N802
        """Обрабатывает входящие POST-запросы команд."""
        if not self._check_auth():
            return

        parsed = urlsplit(self.path)
        path = parsed.path.rstrip("/")

        # 1. Внеплановая проверка сертификатов
        if path in ("/check", "/api/v1/check"):
            threading.Thread(target=self._run_async_check, daemon=True).start()
            self._send_json(HTTPStatus.ACCEPTED, {
                "message": "Внеплановая проверка сертификатов запущена в фоновом потоке",
                "timestamp": self.server.state.get_snapshot()["last_check"]["timestamp"],
            })
            return

        # 2. Внеплановое формирование отчета
        if path in ("/report", "/api/v1/report"):
            try:
                report_path = self.server.monitor.perform_report()
                self._send_json(HTTPStatus.CREATED, {
                    "message": "Отчет успешно сформирован",
                    "report_path": str(report_path),
                })
            except Exception as exc:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                    "error": f"Ошибка генерации отчета: {exc}"
                })
            return

        # 3. Завершение работы фонового процесса
        if path in ("/stop", "/api/v1/stop", "/shutdown"):
            self._send_json(HTTPStatus.OK, {"message": "Инициирована остановка фонового сервиса..."})
            self.server.stop_event.set()
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Маршрут не найден", "path": self.path})

    def _run_async_check(self) -> None:
        """Асинхронно запускает проверку сертификатов."""
        try:
            self.server.monitor.perform_check()
        except Exception as exc:
            logger.error("Ошибка асинхронной проверки через API: %s", exc)

    def _handle_get_certificates(self, query: dict[str, list[str]]) -> None:
        """Фильтрует и возвращает список сертификатов.

        Args:
            query: Параметры строки запроса.
        """
        status_filter = query.get("status", [None])[0]
        search_filter = query.get("search", [None])[0]

        items = list(self.server.state.certificates)

        if status_filter:
            status_val = status_filter.lower()
            items = [c for c in items if str(c.get("status", "")).lower() == status_val]

        if search_filter:
            search_val = search_filter.lower()
            items = [
                c for c in items
                if search_val in str(c.get("common_name", "")).lower()
                or search_val in str(c.get("serial_number", "")).lower()
                or search_val in str(c.get("issuer", "")).lower()
            ]

        # Пагинация
        try:
            limit = int(query.get("limit", [100])[0])
            offset = int(query.get("offset", [0])[0])
        except ValueError:
            limit, offset = 100, 0

        paged_items = items[offset : offset + limit]

        self._send_json(HTTPStatus.OK, {
            "total_count": len(items),
            "offset": offset,
            "limit": limit,
            "certificates": paged_items,
        })

    def _handle_get_reports(self) -> None:
        """Возвращает список существующих сформированных файлов отчетов."""
        rep_dir = Path(self.server.config.report_output_dir).resolve()
        reports = []

        if rep_dir.is_dir():
            for p in sorted(rep_dir.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True):
                if p.is_file() and p.suffix in (".xlsx", ".html", ".json"):
                    st = p.stat()
                    reports.append({
                        "filename": p.name,
                        "size_bytes": st.st_size,
                        "created_at": p.stat().st_ctime,
                        "path": str(p),
                    })

        self._send_json(HTTPStatus.OK, {
            "output_directory": str(rep_dir),
            "count": len(reports),
            "reports": reports,
        })

    def _check_auth(self) -> bool:
        """Проверяет токен авторизации при его наличии в конфигурации.

        Returns:
            bool: True, если авторизация пройдена или не требуется.
        """
        required_token = self.server.config.api_token
        if not required_token:
            return True

        auth_header = self.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
            if token == required_token:
                return True

        self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "Неверный или отсутствующий токен авторизации (Bearer)"})
        return False

    def _send_json(self, status: HTTPStatus, data: Any) -> None:
        """Отправляет клиенту JSON-ответ.

        Args:
            status: HTTP статус ответа.
            data: Данные для сериализации в JSON.
        """
        payload = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:
        """Перенаправляет стандартные логи HTTP-сервера в модуль logging."""
        logger.debug("API: " + format, *args)
