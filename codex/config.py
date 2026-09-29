"""Конфигурация автономного фонового сервиса."""

from __future__ import annotations

import ipaddress
import json
import os
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_NAME = "runtime.json"


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Настройки процесса мониторинга и его локального API."""

    application_config_path: str | None = None
    check_interval_seconds: int = 3600
    report_interval_seconds: int = 7 * 24 * 60 * 60
    report_retry_seconds: int = 3600
    run_report_on_start: bool = False
    notification_cooldown_seconds: int = 24 * 60 * 60
    report_output_dir: str | None = None
    state_path: str = "runtime-state.json"
    api_host: str = "127.0.0.1"
    api_port: int = 8765
    api_token_env: str = "CERTIFICATE_ANALYZER_RUNTIME_TOKEN"
    api_max_page_size: int = 500
    alert_history_size: int = 100

    def __post_init__(self) -> None:
        positive = (
            "check_interval_seconds",
            "report_interval_seconds",
            "report_retry_seconds",
            "notification_cooldown_seconds",
            "api_max_page_size",
            "alert_history_size",
        )
        for name in positive:
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} должен быть положительным целым числом")
        if type(self.api_port) is not int or not 1 <= self.api_port <= 65535:
            raise ValueError("api_port должен находиться в диапазоне 1..65535")
        if not self.api_host.strip():
            raise ValueError("api_host не может быть пустым")
        if not self.state_path.strip():
            raise ValueError("state_path не может быть пустым")
        if not self.api_token_env.strip():
            raise ValueError("api_token_env не может быть пустым")

    @property
    def api_token(self) -> str | None:
        """Читает токен из окружения, не сохраняя секрет в JSON."""

        value = os.environ.get(self.api_token_env, "").strip()
        return value or None

    def validate_api_security(self) -> None:
        """Запрещает публиковать API в сеть без токена."""

        try:
            is_loopback = ipaddress.ip_address(self.api_host).is_loopback
        except ValueError:
            is_loopback = self.api_host.casefold() == "localhost"
        if not is_loopback and self.api_token is None:
            raise ValueError(
                "Для api_host вне loopback необходимо задать токен в переменной "
                f"окружения {self.api_token_env}"
            )


def _resolve_path(value: str | None, base: Path) -> str | None:
    if value is None:
        return None
    path = Path(value).expanduser()
    return str(path if path.is_absolute() else base / path)


def load_config(path: str | Path | None = None) -> RuntimeConfig:
    """Загружает JSON и разрешает относительные пути от каталога конфига."""

    config_path = Path(path or DEFAULT_CONFIG_NAME).expanduser().resolve()
    if config_path.exists():
        raw: Any = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Конфигурация фонового сервиса должна быть JSON-объектом")
        known = {item.name for item in fields(RuntimeConfig)}
        unknown = sorted(set(raw) - known)
        if unknown:
            raise ValueError(
                f"Неизвестные параметры конфигурации: {', '.join(unknown)}"
            )
        config = RuntimeConfig(**raw)
    else:
        config = RuntimeConfig()

    base = config_path.parent
    config = replace(
        config,
        application_config_path=_resolve_path(config.application_config_path, base),
        report_output_dir=_resolve_path(config.report_output_dir, base),
        state_path=_resolve_path(config.state_path, base)
        or str(base / "runtime-state.json"),
    )
    config.validate_api_security()
    return config
