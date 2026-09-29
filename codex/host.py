"""Жизненный цикл фонового процесса и координация его потоков."""

from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime
from typing import Any, Callable

from certificate_analyzer.bootstrap import create_application
from certificate_analyzer.runtime.codex.api import RuntimeApiServer
from certificate_analyzer.runtime.codex.config import RuntimeConfig
from certificate_analyzer.runtime.codex.monitor import CertificateMonitor, iso
from certificate_analyzer.runtime.codex.state import RuntimeState, StateStore

logger = logging.getLogger(__name__)


class RuntimeHost:
    """Запускает мониторинг, отчётность и API в одном системном процессе."""

    def __init__(
        self,
        config: RuntimeConfig,
        *,
        application_factory: Callable[..., Any] = create_application,
    ) -> None:
        self.config = config
        self._application_factory = application_factory
        self._application: Any | None = None
        self._store = StateStore(config.state_path)
        self.state = RuntimeState(self._store.load())
        self.monitor: CertificateMonitor | None = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._lifecycle_lock = threading.RLock()
        self._closed = False
        self._scheduler_thread: threading.Thread | None = None
        self._api: RuntimeApiServer | None = None
        self._api_thread: threading.Thread | None = None

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._scheduler_thread is not None and self._scheduler_thread.is_alive():
                return
            self.config.validate_api_security()
            self._closed = False
            self._stop.clear()
            self._wake.clear()
            self._application = self._application_factory(
                config_path=self.config.application_config_path
            )
            try:
                self.monitor = CertificateMonitor(
                    self._application, self.config, self.state, self._store
                )
                self.monitor.initialize_schedule()
                with self.state.lock:
                    self.state.started_at = iso(datetime.now(UTC))

                self._api = RuntimeApiServer(self.config, self.state, self)
                self._api_thread = threading.Thread(
                    target=self._api.serve_forever,
                    name="certificate-runtime-api",
                    daemon=True,
                )
                self._scheduler_thread = threading.Thread(
                    target=self._scheduler_loop,
                    name="certificate-runtime-scheduler",
                    daemon=True,
                )
                self._api_thread.start()
                self._scheduler_thread.start()
            except Exception:
                self._application.close()
                self._application = None
                raise
        logger.info(
            "Фоновый сервис запущен; API: http://%s:%s",
            self.config.api_host,
            self.config.api_port,
        )

    def run_forever(self) -> None:
        self.start()
        try:
            while not self._stop.wait(1.0):
                scheduler = self._scheduler_thread
                api = self._api_thread
                if scheduler is not None and not scheduler.is_alive():
                    raise RuntimeError("Поток планировщика неожиданно завершился")
                if api is not None and not api.is_alive():
                    raise RuntimeError("Поток API неожиданно завершился")
        finally:
            self.stop()

    def stop(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            self._stop.set()
            self._wake.set()
            api = self._api
            scheduler_thread = self._scheduler_thread
            api_thread = self._api_thread
            application = self._application
            self._api = None
            self._application = None
        if api is not None:
            api.shutdown()
            api.server_close()
        current = threading.current_thread()
        for thread in (scheduler_thread, api_thread):
            if thread is not None and thread is not current:
                thread.join(timeout=5)
        if application is not None:
            application.close()
        logger.info("Фоновый сервис остановлен")

    def initiate_stop(self) -> None:
        """Неблокирующе просит основной цикл корректно завершить процесс."""

        self._stop.set()
        self._wake.set()

    def request_check(self) -> bool:
        with self.state.lock:
            accepted = not self.state.check_requested
            self.state.check_requested = True
        self._wake.set()
        return accepted

    def request_report(self) -> bool:
        with self.state.lock:
            accepted = not self.state.report_requested
            self.state.report_requested = True
        self._wake.set()
        return accepted

    def _scheduler_loop(self) -> None:
        assert self.monitor is not None
        next_check = time.monotonic()
        while not self._stop.is_set():
            with self.state.lock:
                check_requested = self.state.check_requested
                report_requested = self.state.report_requested
                self.state.check_requested = False
                self.state.report_requested = False

            records = None
            if check_requested or time.monotonic() >= next_check:
                try:
                    records = self.monitor.check()
                except Exception:
                    pass
                next_check = time.monotonic() + self.config.check_interval_seconds

            if report_requested or self.monitor.report_is_due():
                try:
                    self.monitor.report(records)
                except Exception:
                    pass

            wait_seconds = min(
                max(0.0, next_check - time.monotonic()),
                self.monitor.seconds_until_report(),
            )
            self._wake.wait(timeout=max(0.05, wait_seconds))
            self._wake.clear()
