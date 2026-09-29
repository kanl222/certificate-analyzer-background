"""Клиент для обращения основного приложения к фоновому процессу мониторинга.

Использует стандартную библиотеку urllib.request для выполнения HTTP-запросов
без необходимости установки внешних зависимостей (requests, httpx).
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


class GeminiApiClient:
    """Клиент локального API фонового процесса Gemini."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8766",
        token: str | None = None,
        timeout: float = 5.0,
    ) -> None:
        """Инициализирует клиент API.

        Args:
            base_url: Базовый URL сервиса (default 'http://127.0.0.1:8766').
            token: Опциональный Bearer-токен авторизации.
            timeout: Таймаут запросов в секундах.
        """
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Выполняет HTTP-запрос к API сервиса.

        Args:
            method: Метод HTTP ('GET', 'POST').
            endpoint: Путь к эндпоинту (например, '/api/v1/status').
            params: Параметры query-строки.
            body: Тело запроса в виде словаря (для POST).

        Returns:
            dict[str, Any]: Распарсенный ответ сервера.

        Raises:
            ConnectionError: Если сервис недоступен или не запущен.
            RuntimeError: При получении ошибки HTTP от сервера.
        """
        url = f"{self.base_url}{endpoint}"
        if params:
            query_str = urlencode({k: v for k, v in params.items() if v is not None})
            if query_str:
                url = f"{url}?{query_str}"

        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        data_bytes = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data_bytes = json.dumps(body).encode("utf-8")

        req = Request(url=url, data=data_bytes, headers=headers, method=method)

        try:
            with urlopen(req, timeout=self.timeout) as resp:
                resp_bytes = resp.read()
                if not resp_bytes:
                    return {}
                return json.loads(resp_bytes.decode("utf-8"))
        except HTTPError as http_err:
            error_text = http_err.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"API Error {http_err.code}: {error_text}") from http_err
        except (URLError, OSError) as conn_err:
            raise ConnectionError(
                f"Фоновый процесс недоступен по адресу {self.base_url}: {conn_err}"
            ) from conn_err

    def is_alive(self) -> bool:
        """Проверяет, запущен ли и отвечает ли фоновый процесс.

        Returns:
            bool: True, если сервис активен, иначе False.
        """
        try:
            res = self._request("GET", "/api/v1/health")
            return res.get("status") in ("ok", "degraded")
        except (ConnectionError, RuntimeError):
            return False

    def get_status(self) -> dict[str, Any]:
        """Получает полный снимок состояния фонового процесса.

        Returns:
            dict[str, Any]: Словарь с метриками, расписанием и счетчиками.
        """
        return self._request("GET", "/api/v1/status")

    def get_certificates(
        self,
        status: str | None = None,
        search: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Запрашивает список сертификатов с фильтрацией и пагинацией.

        Args:
            status: Опциональный фильтр статуса ('valid', 'expiring_soon', 'expired').
            search: Поисковый запрос (по CN, серийному номеру, издателю).
            limit: Количество записей на странице.
            offset: Смещение выборки.

        Returns:
            dict[str, Any]: Словарь со списком сертификатов и счетчиком total_count.
        """
        params = {
            "status": status,
            "search": search,
            "limit": limit,
            "offset": offset,
        }
        return self._request("GET", "/api/v1/certificates", params=params)

    def trigger_check(self) -> dict[str, Any]:
        """Отправляет команду на немедленный запуск проверки сертификатов.

        Returns:
            dict[str, Any]: Ответ сервера с подтверждением запуска задачи.
        """
        return self._request("POST", "/api/v1/check")

    def trigger_report(self) -> dict[str, Any]:
        """Отправляет команду на немедленную генерацию еженедельного отчета.

        Returns:
            dict[str, Any]: Результат с путем к созданному файлу отчета.
        """
        return self._request("POST", "/api/v1/report")

    def get_reports(self) -> dict[str, Any]:
        """Получает список всех созданных отчетов в каталоге архива.

        Returns:
            dict[str, Any]: Список файлов отчетов и директория сохранения.
        """
        return self._request("GET", "/api/v1/reports")

    def stop_service(self) -> bool:
        """Отправляет команду на корректное завершение работы фонового процесса.

        Returns:
            bool: True при успешной отправке команды.
        """
        try:
            self._request("POST", "/api/v1/stop")
            return True
        except (ConnectionError, RuntimeError):
            return False
