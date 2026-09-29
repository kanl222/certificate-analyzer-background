"""Проверка сроков, уведомления и формирование отчётности."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from certificate_analyzer.application.dto.certificate_query import CertificateQuery
from certificate_analyzer.domain.enums.certificate_status import CertificateStatus
from certificate_analyzer.runtime.codex.config import RuntimeConfig
from certificate_analyzer.runtime.codex.state import RuntimeState, StateStore

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


class CertificateMonitor:
    """Выполняет атомарные операции фонового процесса."""

    def __init__(
        self,
        application: Any,
        config: RuntimeConfig,
        state: RuntimeState,
        store: StateStore,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.application = application
        self.config = config
        self.state = state
        self.store = store
        self.clock = clock

    def initialize_schedule(self) -> None:
        now = self.clock()
        with self.state.lock:
            if self.state.durable.next_report_at is None:
                delay = (
                    0
                    if self.config.run_report_on_start
                    else self.config.report_interval_seconds
                )
                self.state.durable.next_report_at = iso(now + timedelta(seconds=delay))
                self.store.save(self.state.durable)

    def check(self) -> list[Any]:
        """Пересчитывает статусы, обновляет API-снимок и оповещает пользователя."""

        now = self.clock()
        try:
            # CertificateService сам запрашивает репозиторий, который пересчитывает
            # статус относительно текущего времени при каждом чтении.
            records = self.application.certificates.list(CertificateQuery(limit=None))
            counts = {"total": len(records)}
            for status in CertificateStatus:
                counts[status.value] = 0
            for record in records:
                counts[record.status.value] = counts.get(record.status.value, 0) + 1

            errors: dict[str, str] = {}
            for record in records:
                source = record.source_path
                if source and not Path(source).is_file():
                    errors[source] = "Файл сертификата отсутствует"

            snapshots = [self._serialize_certificate(record, now) for record in records]
            with self.state.lock:
                self.state.certificates = snapshots
                self.state.counts = counts
                self.state.errors = errors
                self.state.last_check_at = iso(now)
                self.state.last_check_error = None
            self._notify_if_needed(counts, now)
            return records
        except Exception as exc:
            with self.state.lock:
                self.state.last_check_at = iso(now)
                self.state.last_check_error = str(exc)
            logger.exception("Ошибка проверки сертификатов")
            raise

    def report(self, records: list[Any] | None = None) -> Path:
        """Создаёт очередной Excel-отчёт и переносит недельный дедлайн."""

        now = self.clock()
        try:
            if records is None:
                records = self.application.certificates.list(
                    CertificateQuery(limit=None)
                )
            target_dir = Path(
                self.config.report_output_dir or self.application.settings.export_folder
            )
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"certificate-monitoring-{now:%Y%m%d-%H%M%S}.xlsx"
            result = Path(self.application.reports.export("xlsx", records, [], target))
            with self.state.lock:
                self.state.durable.last_report_at = iso(now)
                self.state.durable.next_report_at = iso(
                    now + timedelta(seconds=self.config.report_interval_seconds)
                )
                self.state.durable.latest_report_path = str(result)
                self.state.last_report_error = None
                self.store.save(self.state.durable)
            return result
        except Exception as exc:
            with self.state.lock:
                self.state.durable.next_report_at = iso(
                    now + timedelta(seconds=self.config.report_retry_seconds)
                )
                self.state.last_report_error = str(exc)
                self.store.save(self.state.durable)
            logger.exception("Ошибка формирования отчёта")
            raise

    def report_is_due(self) -> bool:
        with self.state.lock:
            due = parse_iso(self.state.durable.next_report_at)
        return due is not None and self.clock() >= due

    def seconds_until_report(self) -> float:
        with self.state.lock:
            due = parse_iso(self.state.durable.next_report_at)
        if due is None:
            return 0.0
        return max(0.0, (due - self.clock()).total_seconds())

    def _notify_if_needed(self, counts: dict[str, int], now: datetime) -> None:
        expired = counts.get(CertificateStatus.EXPIRED.value, 0)
        warning = counts.get(CertificateStatus.EXPIRING_SOON.value, 0)
        if expired == 0 and warning == 0:
            return

        with self.state.lock:
            durable = self.state.durable
            previous_at = parse_iso(durable.last_notification_at)
            cooldown_elapsed = (
                previous_at is None
                or (now - previous_at).total_seconds()
                >= self.config.notification_cooldown_seconds
            )
            worsened = (
                expired > durable.last_notified_expired
                or warning > durable.last_notified_warning
            )
            if not (cooldown_elapsed or worsened):
                return

        severity = "error" if expired else "warning"
        message = (
            f"Истекли: {expired}. Истекают в ближайшее время: {warning}. "
            f"Всего сертификатов: {counts.get('total', 0)}."
        )
        notifier = getattr(self.application, "notifications", None)
        if notifier is not None:
            try:
                notifier.send_notification(
                    "Срок действия сертификатов",
                    message,
                    notification_type=severity,
                    expired_count=expired,
                    warning_count=warning,
                    total_count=counts.get("total", 0),
                )
            except Exception:
                # API-оповещение всё равно сохраняется: оно является надёжным
                # каналом для приложения, когда системная служба не видит desktop.
                logger.exception("Системное уведомление не отправлено")

        with self.state.lock:
            durable = self.state.durable
            durable.last_alert_id += 1
            durable.alerts.append(
                {
                    "id": durable.last_alert_id,
                    "created_at": iso(now),
                    "severity": severity,
                    "title": "Срок действия сертификатов",
                    "message": message,
                    "expired": expired,
                    "expiring_soon": warning,
                }
            )
            durable.alerts = durable.alerts[-self.config.alert_history_size :]
            durable.last_notification_at = iso(now)
            durable.last_notified_expired = expired
            durable.last_notified_warning = warning
            self.store.save(durable)

    @staticmethod
    def _serialize_certificate(record: Any, now: datetime) -> dict[str, Any]:
        valid_from = record.valid_from
        if valid_from.tzinfo is None:
            valid_from = valid_from.replace(tzinfo=UTC)
        else:
            valid_from = valid_from.astimezone(UTC)
        valid_to = record.valid_to
        if valid_to.tzinfo is None:
            valid_to = valid_to.replace(tzinfo=UTC)
        else:
            valid_to = valid_to.astimezone(UTC)
        return {
            "fingerprint_sha256": record.fingerprint_sha256,
            "subject": record.subject,
            "issuer": record.issuer,
            "owner_name": record.owner_name,
            "valid_from": iso(valid_from),
            "valid_to": iso(valid_to),
            "days_remaining": (valid_to.date() - now.date()).days,
            "status": record.status.value,
            "source_path": record.source_path,
        }
