import json
import socket
import threading
import urllib.request

from certificate_analyzer.runtime.codex.api import RuntimeApiServer
from certificate_analyzer.runtime.codex.config import RuntimeConfig
from certificate_analyzer.runtime.codex.state import DurableState, RuntimeState


class FakeHost:
    def __init__(self):
        self.checks = 0

    def request_check(self):
        self.checks += 1
        return True

    def request_report(self):
        return True


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def request(url, method="GET", token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    value = urllib.request.Request(url, method=method, headers=headers)
    with urllib.request.urlopen(value, timeout=2) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def test_api_exposes_state_and_queues_check(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_RUNTIME_TOKEN", "secret")
    config = RuntimeConfig(
        state_path=str(tmp_path / "state.json"),
        api_port=free_port(),
        api_token_env="TEST_RUNTIME_TOKEN",
    )
    state = RuntimeState(DurableState())
    state.started_at = "2026-09-30T00:00:00Z"
    host = FakeHost()
    server = RuntimeApiServer(config, state, host)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, health = request(
            f"http://127.0.0.1:{config.api_port}/v1/health", token="secret"
        )
        accepted_status, accepted = request(
            f"http://127.0.0.1:{config.api_port}/v1/checks",
            method="POST",
            token="secret",
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert status == 200
    assert health["status"] == "ok"
    assert accepted_status == 202
    assert accepted["accepted"] is True
    assert host.checks == 1
