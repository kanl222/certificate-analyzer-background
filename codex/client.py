"""Небольшой клиент для обращения основного приложения к локальному API."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any


class RuntimeClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8765",
        token: str | None = None,
        timeout: float = 3.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/v1/health")

    def status(self) -> dict[str, Any]:
        return self._request("GET", "/v1/status")

    def certificates(
        self, *, status: str | None = None, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        query: dict[str, Any] = {"limit": limit, "offset": offset}
        if status:
            query["status"] = status
        return self._request("GET", f"/v1/certificates?{urllib.parse.urlencode(query)}")

    def alerts(self, after_id: int = 0) -> dict[str, Any]:
        return self._request("GET", f"/v1/alerts?after_id={after_id}")

    def request_check(self) -> dict[str, Any]:
        return self._request("POST", "/v1/checks")

    def request_report(self) -> dict[str, Any]:
        return self._request("POST", "/v1/reports")

    def _request(self, method: str, path: str) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            f"{self.base_url}{path}", method=method, headers=headers
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))
