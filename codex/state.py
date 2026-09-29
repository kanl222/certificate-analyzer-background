"""Потокобезопасное состояние и его компактное JSON-хранилище."""

from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class DurableState:
    next_report_at: str | None = None
    last_report_at: str | None = None
    latest_report_path: str | None = None
    last_notification_at: str | None = None
    last_notified_expired: int = 0
    last_notified_warning: int = 0
    last_alert_id: int = 0
    alerts: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "DurableState":
        known = cls.__dataclass_fields__
        return cls(**{key: item for key, item in value.items() if key in known})

    def to_dict(self) -> dict[str, Any]:
        return {
            "next_report_at": self.next_report_at,
            "last_report_at": self.last_report_at,
            "latest_report_path": self.latest_report_path,
            "last_notification_at": self.last_notification_at,
            "last_notified_expired": self.last_notified_expired,
            "last_notified_warning": self.last_notified_warning,
            "last_alert_id": self.last_alert_id,
            "alerts": self.alerts,
        }


class StateStore:
    """Атомарно сохраняет только данные, нужные после перезапуска."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> DurableState:
        if not self.path.exists():
            return DurableState()
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Файл состояния должен содержать JSON-объект")
        return DurableState.from_dict(value)

    def save(self, state: DurableState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)


class RuntimeState:
    """Снимок состояния, который безопасно читает HTTP API."""

    def __init__(self, durable: DurableState) -> None:
        self.lock = threading.RLock()
        self.durable = durable
        self.started_at: str | None = None
        self.last_check_at: str | None = None
        self.last_check_error: str | None = None
        self.last_report_error: str | None = None
        self.counts: dict[str, int] = {}
        self.errors: dict[str, str] = {}
        self.certificates: list[dict[str, Any]] = []
        self.check_requested = False
        self.report_requested = False

    def public_snapshot(self) -> dict[str, Any]:
        with self.lock:
            return deepcopy(
                {
                    "started_at": self.started_at,
                    "last_check_at": self.last_check_at,
                    "last_check_error": self.last_check_error,
                    "last_report_at": self.durable.last_report_at,
                    "next_report_at": self.durable.next_report_at,
                    "last_report_error": self.last_report_error,
                    "latest_report_path": self.durable.latest_report_path,
                    "counts": self.counts,
                    "errors": self.errors,
                    "check_requested": self.check_requested,
                    "report_requested": self.report_requested,
                }
            )

    def certificate_snapshot(self) -> list[dict[str, Any]]:
        with self.lock:
            return deepcopy(self.certificates)

    def alert_snapshot(self) -> list[dict[str, Any]]:
        with self.lock:
            return deepcopy(self.durable.alerts)
