"""Движок регулярного мониторинга сертификатов, уведомлений и формирования отчетов.

Выполняет периодическое сканирование хранилищ, расчет дней до истечения срока,
отправку кроссплатформенных уведомлений и создание еженедельных отчетов.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
from typing import Any

from certificate_analyzer.domain.enums.certificate_status import CertificateStatus
from certificate_analyzer.infrastructure.certificates.scanner import scan_files
from certificate_analyzer.infrastructure.certificates.x509_parser import X509Parser
from certificate_analyzer.runtime.gemeni.config import GeminiRuntimeConfig
from certificate_analyzer.runtime.gemeni.notifier import CrossPlatformNotifier
from certificate_analyzer.runtime.gemeni.reporter import CertificateReporter
from certificate_analyzer.runtime.gemeni.state import GeminiRuntimeState

logger = logging.getLogger(__name__)


class GeminiCertificateMonitor:
    """Монитор сертификатов, управляющий циклами проверки и регулярной отчетностью."""

    def __init__(
        self,
        config: GeminiRuntimeConfig,
        state: GeminiRuntimeState | None = None,
        notifier: CrossPlatformNotifier | None = None,
        reporter: CertificateReporter | None = None,
        application: Any = None,
    ) -> None:
        """Инициализирует монитор сертификатов.

        Args:
            config: Конфигурация фонового процесса.
            state: Хранилище состояния (если None, создается новое).
            notifier: Менеджер системных уведомлений.
            reporter: Генератор отчетов.
            application: Опциональный контейнер сервисов основного приложения (DI).
        """
        self.config = config
        self.state = state or GeminiRuntimeState(config.state_file)
        self.notifier = notifier or CrossPlatformNotifier()
        self.reporter = reporter or CertificateReporter(config.report_output_dir)
        self.app = application

    def perform_check(self) -> list[dict[str, Any]]:
        """Выполняет полный цикл проверки сертификатов.

        Считывает сертификаты, определяет их статус, отправляет всплывающие
        уведомления при необходимости, формирует еженедельный отчет по расписанию
        и обновляет состояние для REST API.

        Returns:
            list[dict[str, Any]]: Список сериализованных сертификатов.

        Raises:
            Exception: При критической ошибке сканирования (ошибка сохраняется в state).
        """
        now = datetime.now()
        logger.info("Запуск проверки сертификатов (порог предупреждения: %d дн.)...", self.config.warning_days)

        try:
            certificates = self._collect_certificates()
            counts = {
                "total": len(certificates),
                "valid": 0,
                "expiring_soon": 0,
                "expired": 0,
                "error": 0,
            }

            expiring: list[dict[str, Any]] = []
            expired: list[dict[str, Any]] = []

            for cert in certificates:
                status = cert.get("status")
                if status == CertificateStatus.EXPIRED.value:
                    counts["expired"] += 1
                    expired.append(cert)
                elif status == CertificateStatus.EXPIRING_SOON.value:
                    counts["expiring_soon"] += 1
                    expiring.append(cert)
                elif status == CertificateStatus.VALID.value:
                    counts["valid"] += 1
                else:
                    counts["error"] += 1

            # Отправка системных уведомлений (Windows / Linux)
            if self.config.notifications_enabled and (expiring or expired):
                sent = self.notifier.notify_expirations(
                    expiring=expiring,
                    expired=expired,
                    cooldown_hours=self.config.notification_cooldown_hours,
                )
                if sent > 0:
                    title = "Обнаружены истекающие сертификаты"
                    msg = f"Просрочено: {len(expired)}, истекают: {len(expiring)}"
                    self.state.add_alert(title, msg, urgency="critical" if expired else "normal")

            # Проверка необходимости формирования еженедельного отчета
            last_report_dt = None
            if self.state.durable.last_report_at:
                try:
                    last_report_dt = datetime.fromisoformat(self.state.durable.last_report_at)
                except ValueError:
                    last_report_dt = None

            if self.reporter.is_report_due(last_report_dt, self.config, now):
                logger.info("Наступил интервал еженедельной отчетности. Формирование отчета...")
                self.perform_report(certificates=certificates)

            next_check = now.timestamp() + self.config.check_interval_seconds
            self.state.update_check_results(
                certificates=certificates,
                counts=counts,
                error=None,
                next_check=datetime.fromtimestamp(next_check),
            )

            logger.info(
                "Проверка успешно завершена. Всего: %d, действуют: %d, истекают: %d, просрочено: %d",
                counts["total"], counts["valid"], counts["expiring_soon"], counts["expired"]
            )
            return certificates

        except Exception as exc:
            logger.exception("Ошибка при проверке сертификатов: %s", exc)
            self.state.update_check_results(
                certificates=[],
                counts={"total": 0, "valid": 0, "expiring_soon": 0, "expired": 0, "error": 1},
                error=str(exc),
            )
            raise

    def perform_report(self, certificates: list[dict[str, Any]] | None = None) -> Path:
        """Принудительно формирует отчет по текущим сертификатам.

        Args:
            certificates: Опциональный готовый список сертификатов.

        Returns:
            Path: Путь к созданному файлу отчета.
        """
        now = datetime.now()
        data = certificates if certificates is not None else self._collect_certificates()

        report_path = self.reporter.generate_report(
            certificates=data,
            report_format=self.config.report_format,
            timestamp=now,
        )

        next_report = now + timedelta(days=self.config.report_interval_days)
        self.state.update_report_result(report_path=report_path, next_report=next_report)
        logger.info("Отчет успешно сформирован: %s. Следующий запланирован на %s", report_path, next_report)
        return report_path

    def _collect_certificates(self) -> list[dict[str, Any]]:
        """Собирает сертификаты через контейнер приложения либо через прямое сканирование папок.

        Returns:
            list[dict[str, Any]]: Список словарей с данными сертификатов.
        """
        # Вариант 1: Использование контейнера приложения (если передан)
        if self.app is not None and hasattr(self.app, "certificates"):
            try:
                from certificate_analyzer.application.dto.certificate_query import CertificateQuery

                records = self.app.certificates.list(CertificateQuery(limit=None), now=datetime.now())
                return [self._serialize_app_certificate(r) for r in records]
            except Exception as exc:
                logger.warning("Ошибка получения сертификатов через ApplicationContainer: %s. Переход на прямое сканирование.", exc)

        # Вариант 2: Прямое сканирование локальных каталогов
        folders = list(self.config.certificate_folders)
        if not folders:
            # Дефолтные каталоги
            project_test_certs = Path("test_certs").resolve()
            if project_test_certs.is_dir():
                folders.append(str(project_test_certs))
            user_certs = Path.home() / "Certs"
            if user_certs.is_dir():
                folders.append(str(user_certs))

        results: list[dict[str, Any]] = []
        extensions = (".crt", ".cer", ".pem", ".der")
        scanned_paths: set[Path] = set()

        for f_str in folders:
            folder = Path(f_str).resolve()
            if not folder.is_dir():
                continue
            for cert_file in scan_files(folder, extensions):
                if cert_file in scanned_paths:
                    continue
                scanned_paths.add(cert_file)
                item = self._parse_and_serialize_file(cert_file)
                if item:
                    results.append(item)

        return results

    def _parse_and_serialize_file(self, cert_path: Path) -> dict[str, Any] | None:
        """Парсит файл сертификата и преобразует его в плоский словарь.

        Args:
            cert_path: Путь к файлу сертификата.

        Returns:
            dict[str, Any] | None: Сериализованные атрибуты сертификата.
        """
        try:
            model = X509Parser.parse(cert_path)
            now = datetime.now()

            valid_to = model.valid_to
            if valid_to.tzinfo:
                now_cmp = datetime.now(valid_to.tzinfo)
            else:
                now_cmp = now

            days_left = (valid_to.date() - now_cmp.date()).days

            if days_left < 0:
                status = CertificateStatus.EXPIRED.value
                status_text = "Просрочен"
            elif days_left <= self.config.warning_days:
                status = CertificateStatus.EXPIRING_SOON.value
                status_text = f"Истекает ({days_left} дн.)"
            else:
                status = CertificateStatus.VALID.value
                status_text = "Действует"

            return {
                "id": model.fingerprint_sha256 or str(cert_path),
                "common_name": getattr(model, "owner_name", None) or getattr(model, "subject", "") or cert_path.stem,
                "subject": model.subject,
                "issuer": model.issuer,
                "serial_number": model.serial_number,
                "valid_from": model.valid_from.strftime("%d.%m.%Y"),
                "valid_to": model.valid_to.strftime("%d.%m.%Y"),
                "days_left": days_left,
                "status": status,
                "status_text": status_text,
                "source_path": str(cert_path.resolve()),
            }
        except Exception as exc:
            logger.debug("Не удалось распарсить файл как X.509 (%s): %s", cert_path, exc)
            return None

    def _serialize_app_certificate(self, record: Any) -> dict[str, Any]:
        """Сериализует объект Certificate из доменного слоя в словарь.

        Args:
            record: Доменная модель сертификата.

        Returns:
            dict[str, Any]: Словарь реквизитов.
        """
        now = datetime.now(timezone.utc)
        valid_to = record.valid_to
        if valid_to.tzinfo is None:
            valid_to = valid_to.replace(tzinfo=timezone.utc)

        days_left = (valid_to.date() - now.date()).days
        status_val = record.status.value if hasattr(record.status, "value") else str(record.status)

        status_text = "Действует"
        if status_val == CertificateStatus.EXPIRED.value:
            status_text = "Просрочен"
        elif status_val == CertificateStatus.EXPIRING_SOON.value:
            status_text = f"Истекает ({days_left} дн.)"

        return {
            "id": getattr(record, "fingerprint_sha256", None) or getattr(record, "serial_number", ""),
            "common_name": getattr(record, "subject_cn", None) or getattr(record, "subject", ""),
            "subject": getattr(record, "subject", ""),
            "issuer": getattr(record, "issuer", ""),
            "serial_number": getattr(record, "serial_number", ""),
            "valid_from": record.valid_from.strftime("%d.%m.%Y"),
            "valid_to": record.valid_to.strftime("%d.%m.%Y"),
            "days_left": days_left,
            "status": status_val,
            "status_text": status_text,
            "source_path": str(getattr(record, "source_path", "") or ""),
        }
