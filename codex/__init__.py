"""Изолированный фоновый сервис контроля сроков сертификатов.

Модуль намеренно не подключён к основному CLI или bootstrap проекта. Его можно
запускать отдельно через ``python -m certificate_analyzer.runtime.codex``.
"""

from certificate_analyzer.runtime.codex.config import RuntimeConfig, load_config
from certificate_analyzer.runtime.codex.host import RuntimeHost

__all__ = ["RuntimeConfig", "RuntimeHost", "load_config"]
