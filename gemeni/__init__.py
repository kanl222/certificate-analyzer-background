"""Автономный кроссплатформенный фоновый сервис мониторинга сертификатов Gemini.

Предоставляет:
- Фоновый процесс проверки сроков действия сертификатов (Windows & Linux).
- Всплывающие уведомления при скором истечении или просрочке.
- Регулярную (еженедельную) отчетность в форматах Excel, HTML, JSON.
- Локальный REST API и клиент для взаимодействия с основным приложением.

Модуль изолирован и не изменяет существующие компоненты проекта.
"""

from certificate_analyzer.runtime.gemeni.api import GeminiApiServer
from certificate_analyzer.runtime.gemeni.client import GeminiApiClient
from certificate_analyzer.runtime.gemeni.config import GeminiRuntimeConfig
from certificate_analyzer.runtime.gemeni.host import GeminiRuntimeHost
from certificate_analyzer.runtime.gemeni.monitor import GeminiCertificateMonitor
from certificate_analyzer.runtime.gemeni.notifier import CrossPlatformNotifier
from certificate_analyzer.runtime.gemeni.reporter import CertificateReporter
from certificate_analyzer.runtime.gemeni.state import GeminiRuntimeState

__all__ = [
    "CertificateReporter",
    "CrossPlatformNotifier",
    "GeminiApiClient",
    "GeminiApiServer",
    "GeminiCertificateMonitor",
    "GeminiRuntimeConfig",
    "GeminiRuntimeHost",
    "GeminiRuntimeState",
]
