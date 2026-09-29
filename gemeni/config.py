"""Конфигурация фонового процесса мониторинга сертификатов Gemini.

Модуль определяет параметры периодической проверки сроков действия сертификатов,
график формирования еженедельных отчетов, настройки системных уведомлений для
Windows и Linux, а также параметры локального REST API.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = "gemeni_config.json"


@dataclass
class GeminiRuntimeConfig:
    """Настройки фонового процесса мониторинга и его встроенного API.

    Attributes:
        check_interval_seconds: Периодичность проверки сертификатов в секундах (по умолчанию 3600 = 1 час).
        warning_days: Порог дней до окончания срока для статуса 'скоро истекает' (по умолчанию 30).
        report_interval_days: Интервал генерации регулярных отчетов в днях (по умолчанию 7 = раз в неделю).
        report_day_of_week: Предпочтительный день недели для еженедельного отчета (0 - Пн, 6 - Вс, default 0).
        report_time: Предпочтительное время суток для отчета в формате 'ЧЧ:ММ' (default '09:00').
        report_format: Формат экспорта отчетов ('xlsx', 'json', 'html', default 'xlsx').
        report_output_dir: Каталог для сохранения сформированных отчетов.
        notifications_enabled: Флаг включения системных всплывающих уведомлений.
        notification_cooldown_hours: Минимальный интервал в часах между повторными уведомлениями по одному сертификату.
        api_host: Хост для привязки встроенного HTTP API (default '127.0.0.1').
        api_port: Порт для встроенного HTTP API (default 8766).
        api_token: Опциональный токен безопасности (Bearer) для доступа к API.
        state_file: Путь к файлу сохранения состояния (метки проверок, расписание).
        certificate_folders: Список директорий для поиска сертификатов при автономном режиме.
        database_path: Опциональный путь к SQLite базе данных приложения.
    """

    check_interval_seconds: int = 3600
    warning_days: int = 30
    report_interval_days: int = 7
    report_day_of_week: int = 0
    report_time: str = "09:00"
    report_format: str = "xlsx"
    report_output_dir: str = "reports"
    notifications_enabled: bool = True
    notification_cooldown_hours: int = 24
    api_host: str = "127.0.0.1"
    api_port: int = 8766
    api_token: str | None = None
    state_file: str = "gemeni_runtime_state.json"
    certificate_folders: list[str] = field(default_factory=list)
    database_path: str | None = None

    def __post_init__(self) -> None:
        """Валидирует значения параметров конфигурации.

        Raises:
            ValueError: При некорректных или отрицательных числовых параметрах.
        """
        if self.check_interval_seconds < 10:
            raise ValueError("check_interval_seconds должен быть не менее 10 секунд")
        if self.warning_days < 1:
            raise ValueError("warning_days должен быть положительным числом")
        if self.report_interval_days < 1:
            raise ValueError("report_interval_days должен быть положительным числом")
        if not (0 <= self.report_day_of_week <= 6):
            raise ValueError("report_day_of_week должен быть в диапазоне 0..6 (0 - Пн, 6 - Вс)")
        if not (1 <= self.api_port <= 65535):
            raise ValueError("api_port должен быть в диапазоне 1..65535")
        if self.report_format.lower() not in ("xlsx", "json", "html"):
            raise ValueError("report_format должен быть одним из: 'xlsx', 'json', 'html'")

    def to_dict(self) -> dict[str, Any]:
        """Преобразует конфигурацию в словарь.

        Returns:
            dict[str, Any]: Словарь с параметрами конфигурации.
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GeminiRuntimeConfig:
        """Создает объект конфигурации из словаря с фильтрацией неизвестных полей.

        Args:
            data: Словарь с параметрами.

        Returns:
            GeminiRuntimeConfig: Сконфигурированный объект.
        """
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)

    def save_to_file(self, path: str | Path) -> None:
        """Сохраняет конфигурацию в JSON-файл.

        Args:
            path: Путь к целевому файлу.
        """
        p = Path(path).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("Конфигурация GeminiRuntime сохранена в: %s", p)

    @classmethod
    def load_from_file(cls, path: str | Path | None = None) -> GeminiRuntimeConfig:
        """Загружает конфигурацию из JSON-файла или возвращает дефолтную при отсутствии.

        Args:
            path: Путь к файлу конфигурации (если None, используется gemeni_config.json).

        Returns:
            GeminiRuntimeConfig: Загруженная конфигурация.

        Raises:
            ValueError: При ошибке синтаксиса JSON.
        """
        target = Path(path or DEFAULT_CONFIG_PATH).resolve()
        if not target.is_file():
            logger.info("Файл конфигурации %s не найден, используются параметры по умолчанию", target)
            return cls()

        try:
            content = target.read_text(encoding="utf-8")
            data = json.loads(content)
            if not isinstance(data, dict):
                raise ValueError("Корневой элемент конфигурации должен быть JSON-объектом")
            return cls.from_dict(data)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Ошибка синтаксиса в файле конфигурации {target}: {exc}") from exc
