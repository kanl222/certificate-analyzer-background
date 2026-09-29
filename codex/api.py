"""Минимальный JSON API для связи приложения с фоновым процессом."""

from __future__ import annotations

import hmac
import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from certificate_analyzer.runtime.codex.config import RuntimeConfig
from certificate_analyzer.runtime.codex.state import RuntimeState

logger = logging.getLogger(__name__)


class RuntimeApiServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, config: RuntimeConfig, state: RuntimeState, host: Any) -> None:
        self.runtime_config = config
        self.runtime_state = state
        self.runtime_host = host
        super().__init__((config.api_host, config.api_port), RuntimeApiHandler)


class RuntimeApiHandler(BaseHTTPRequestHandler):
    server: RuntimeApiServer
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 - имя определено BaseHTTPRequestHandler
        if not self._authorized():
            return
        parsed = urlsplit(self.path)
        if parsed.path == "/v1/health":
            snapshot = self.server.runtime_state.public_snapshot()
            self._json(
                HTTPStatus.OK,
                {
                    "status": "degraded"
                    if snapshot["last_check_error"] or snapshot["last_report_error"]
                    else "ok",
                    "started_at": snapshot["started_at"],
                },
            )
            return
        if parsed.path == "/v1/status":
            self._json(HTTPStatus.OK, self.server.runtime_state.public_snapshot())
            return
        if parsed.path == "/v1/certificates":
            self._certificates(parse_qs(parsed.query))
            return
        if parsed.path == "/v1/alerts":
            self._alerts(parse_qs(parsed.query))
            return
        if parsed.path == "/v1/reports/latest":
            snapshot = self.server.runtime_state.public_snapshot()
            self._json(
                HTTPStatus.OK,
                {
                    "created_at": snapshot["last_report_at"],
                    "path": snapshot["latest_report_path"],
                    "error": snapshot["last_report_error"],
                },
            )
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "Маршрут не найден"})

    def do_POST(self) -> None:  # noqa: N802 - имя определено BaseHTTPRequestHandler
        if not self._authorized():
            return
        path = urlsplit(self.path).path
        if path == "/v1/checks":
            accepted = self.server.runtime_host.request_check()
            self._json(
                HTTPStatus.ACCEPTED,
                {"accepted": accepted, "message": "Проверка поставлена в очередь"},
            )
            return
        if path == "/v1/reports":
            accepted = self.server.runtime_host.request_report()
            self._json(
                HTTPStatus.ACCEPTED,
                {"accepted": accepted, "message": "Отчёт поставлен в очередь"},
            )
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "Маршрут не найден"})

    def _certificates(self, query: dict[str, list[str]]) -> None:
        records = self.server.runtime_state.certificate_snapshot()
        statuses = {value.upper() for value in query.get("status", [])}
        if statuses:
            records = [item for item in records if item["status"] in statuses]
        limit = self._bounded_int(
            query, "limit", 100, 1, self.server.runtime_config.api_max_page_size
        )
        offset = self._bounded_int(query, "offset", 0, 0, 2**31 - 1)
        if limit is None or offset is None:
            return
        self._json(
            HTTPStatus.OK,
            {
                "total": len(records),
                "offset": offset,
                "items": records[offset : offset + limit],
            },
        )

    def _alerts(self, query: dict[str, list[str]]) -> None:
        after_id = self._bounded_int(query, "after_id", 0, 0, 2**63 - 1)
        if after_id is None:
            return
        records = [
            item
            for item in self.server.runtime_state.alert_snapshot()
            if int(item.get("id", 0)) > after_id
        ]
        self._json(HTTPStatus.OK, {"items": records})

    def _bounded_int(
        self,
        query: dict[str, list[str]],
        name: str,
        default: int,
        minimum: int,
        maximum: int,
    ) -> int | None:
        raw = query.get(name, [str(default)])[0]
        try:
            value = int(raw)
        except ValueError:
            self._json(
                HTTPStatus.BAD_REQUEST, {"error": f"{name} должен быть целым числом"}
            )
            return None
        if not minimum <= value <= maximum:
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": f"{name} должен находиться в диапазоне {minimum}..{maximum}"},
            )
            return None
        return value

    def _authorized(self) -> bool:
        expected = self.server.runtime_config.api_token
        if expected is None:
            return True
        supplied = self.headers.get("Authorization", "")
        prefix = "Bearer "
        valid = supplied.startswith(prefix) and hmac.compare_digest(
            supplied[len(prefix) :], expected
        )
        if not valid:
            self._json(
                HTTPStatus.UNAUTHORIZED,
                {"error": "Требуется корректный Bearer-токен"},
                extra_headers={"WWW-Authenticate": "Bearer"},
            )
        return valid

    def _json(
        self,
        status: HTTPStatus,
        value: Any,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, header_value in (extra_headers or {}).items():
            self.send_header(name, header_value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, message: str, *args: Any) -> None:
        logger.info("API %s - %s", self.address_string(), message % args)
