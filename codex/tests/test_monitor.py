from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from certificate_analyzer.domain.enums.certificate_status import CertificateStatus
from certificate_analyzer.runtime.codex.config import RuntimeConfig
from certificate_analyzer.runtime.codex.monitor import CertificateMonitor
from certificate_analyzer.runtime.codex.state import (
    DurableState,
    RuntimeState,
    StateStore,
)


class FakeCertificates:
    def __init__(self, records):
        self.records = records

    def list(self, query):
        return self.records


class FakeReports:
    def export(self, format, records, mchds, target):
        Path(target).write_text("report", encoding="utf-8")
        return target


class FakeNotifications:
    def __init__(self):
        self.messages = []

    def send_notification(self, title, message, **kwargs):
        self.messages.append((title, message, kwargs))


def certificate(status, valid_to):
    return SimpleNamespace(
        fingerprint_sha256="a" * 64,
        subject="CN=Test",
        issuer="CN=Issuer",
        owner_name="Test User",
        valid_from=datetime(2025, 1, 1, tzinfo=UTC),
        valid_to=valid_to,
        status=status,
        source_path="",
    )


def test_check_creates_api_alert_and_sends_notification(tmp_path):
    now = datetime(2026, 9, 30, tzinfo=UTC)
    notifier = FakeNotifications()
    app = SimpleNamespace(
        certificates=FakeCertificates(
            [certificate(CertificateStatus.EXPIRED, datetime(2026, 9, 1, tzinfo=UTC))]
        ),
        reports=FakeReports(),
        notifications=notifier,
        settings=SimpleNamespace(export_folder=str(tmp_path / "reports")),
    )
    config = RuntimeConfig(state_path=str(tmp_path / "state.json"))
    store = StateStore(config.state_path)
    state = RuntimeState(DurableState())
    monitor = CertificateMonitor(app, config, state, store, clock=lambda: now)

    monitor.check()

    assert state.counts["EXPIRED"] == 1
    assert state.alert_snapshot()[0]["severity"] == "error"
    assert len(notifier.messages) == 1


def test_report_updates_next_deadline(tmp_path):
    now = datetime(2026, 9, 30, tzinfo=UTC)
    app = SimpleNamespace(
        certificates=FakeCertificates([]),
        reports=FakeReports(),
        notifications=FakeNotifications(),
        settings=SimpleNamespace(export_folder=str(tmp_path / "reports")),
    )
    config = RuntimeConfig(
        state_path=str(tmp_path / "state.json"), report_interval_seconds=604800
    )
    state = RuntimeState(DurableState())
    monitor = CertificateMonitor(
        app, config, state, StateStore(config.state_path), clock=lambda: now
    )

    result = monitor.report([])

    assert result.exists()
    assert state.durable.last_report_at == "2026-09-30T00:00:00Z"
    assert state.durable.next_report_at == "2026-10-07T00:00:00Z"
    assert state.durable.latest_report_path == str(result)
