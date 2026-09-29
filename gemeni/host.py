"""Хост фонового процесса мониторинга и управление жизненным циклом.

Оркестрирует фоновый цикл проверки сертификатов, запуск локального HTTP API,
обработку сигналов завершения ОС (SIGINT/SIGTERM) и генерацию шаблонов служб
для Linux (systemd) и Windows (Task Scheduler).
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
from typing import Any

from certificate_analyzer.runtime.gemeni.api import GeminiApiServer
from certificate_analyzer.runtime.gemeni.config import GeminiRuntimeConfig
from certificate_analyzer.runtime.gemeni.monitor import GeminiCertificateMonitor
from certificate_analyzer.runtime.gemeni.notifier import CrossPlatformNotifier
from certificate_analyzer.runtime.gemeni.reporter import CertificateReporter
from certificate_analyzer.runtime.gemeni.state import GeminiRuntimeState

logger = logging.getLogger(__name__)


class GeminiRuntimeHost:
    """Главный хост-процесс фонового сервиса."""

    def __init__(
        self,
        config: GeminiRuntimeConfig | None = None,
        application: Any = None,
    ) -> None:
        """Инициализирует хост фонового процесса.

        Args:
            config: Конфигурация процесса (если None, загружается дефолтная).
            application: Опциональный контейнер приложения с доменными сервисами.
        """
        self.config = config or GeminiRuntimeConfig.load_from_file()
        self.state = GeminiRuntimeState(self.config.state_file)
        self.notifier = CrossPlatformNotifier()
        self.reporter = CertificateReporter(self.config.report_output_dir)
        self.monitor = GeminiCertificateMonitor(
            config=self.config,
            state=self.state,
            notifier=self.notifier,
            reporter=self.reporter,
            application=application,
        )

        self.stop_event = threading.Event()
        self._api_server: GeminiApiServer | None = None
        self._worker_thread: threading.Thread | None = None
        self._api_thread: threading.Thread | None = None

    def start(self, block: bool = True) -> None:
        """Запускает фоновые потоки мониторинга и REST API.

        Args:
            block: Если True, блокирует текущий поток до получения сигнала остановки.
        """
        logger.info("Запуск Gemini Certificate Runtime Host на %s:%d...", self.config.api_host, self.config.api_port)
        self.state.is_running = True
        self.stop_event.clear()

        # 1. Запуск сервера API
        self._api_server = GeminiApiServer(
            config=self.config,
            state=self.state,
            monitor=self.monitor,
            stop_event=self.stop_event,
        )
        self._api_thread = threading.Thread(
            target=self._api_server.serve_forever,
            name="GeminiApiThread",
            daemon=True,
        )
        self._api_thread.start()
        logger.info("API сервер запущен на http://%s:%d", self.config.api_host, self.config.api_port)

        # 2. Запуск рабочего цикла мониторинга
        self._worker_thread = threading.Thread(
            target=self._monitor_loop,
            name="GeminiMonitorWorker",
            daemon=True,
        )
        self._worker_thread.start()

        # 3. Регистрация перехватчиков сигналов завершения
        self._register_signals()

        if block:
            try:
                while not self.stop_event.is_set():
                    time.sleep(0.5)
            except KeyboardInterrupt:
                logger.info("Получен сигнал прерывания KeyboardInterrupt")
            finally:
                self.stop()

    def stop(self) -> None:
        """Останавливает все фоновые компоненты сервиса."""
        if not self.state.is_running:
            return

        logger.info("Остановка Gemini Certificate Runtime Host...")
        self.state.is_running = False
        self.stop_event.set()

        if self._api_server:
            try:
                self._api_server.shutdown()
                self._api_server.server_close()
            except Exception as exc:
                logger.debug("Ошибка при закрытии API сервера: %s", exc)

        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=3.0)

        self.state.save_durable()
        logger.info("Сервис Gemini Certificate Runtime Host успешно остановлен.")

    def _monitor_loop(self) -> None:
        """Основной периодический цикл проверки сертификатов и расписания."""
        # Первичная проверка сразу при старте
        try:
            self.monitor.perform_check()
        except Exception as exc:
            logger.error("Ошибка при начальной проверке сертификатов: %s", exc)

        while not self.stop_event.is_set():
            # Ожидание следующего интервала с периодической проверкой флага stop_event
            sleep_chunk = 5  # проверка флага каждые 5 секунд
            elapsed = 0
            while elapsed < self.config.check_interval_seconds and not self.stop_event.is_set():
                time.sleep(sleep_chunk)
                elapsed += sleep_chunk

            if self.stop_event.is_set():
                break

            try:
                self.monitor.perform_check()
            except Exception as exc:
                logger.error("Ошибка в цикле мониторинга: %s", exc)

    def _register_signals(self) -> None:
        """Регистрирует обработчики системных сигналов завершения."""
        def handler(signum: int, frame: Any) -> None:
            logger.info("Получен системный сигнал завершения (%s)", signum)
            self.stop()

        try:
            signal.signal(signal.SIGINT, handler)
            if hasattr(signal, "SIGTERM"):
                signal.signal(signal.SIGTERM, handler)
        except (ValueError, AttributeError):
            # В не-главных потоках регистрация сигналов может быть недоступна
            pass

    @staticmethod
    def generate_systemd_unit(
        service_name: str = "certificate-monitor",
        user: str = "root",
        working_dir: str | None = None,
        python_bin: str | None = None,
    ) -> str:
        """Генерирует текст unit-файла systemd для развертывания службы в Linux.

        Args:
            service_name: Имя службы.
            user: Имя пользователя Linux для запуска.
            working_dir: Рабочая директория проекта.
            python_bin: Путь к интерпретатору Python или venv.

        Returns:
            str: Содержимое unit-файла .service.
        """
        py = python_bin or sys.executable
        cwd = working_dir or str(os.getcwd())

        return f"""[Unit]
Description=Certificate Analyzer Background Monitoring Service
After=network.target

[Service]
Type=simple
User={user}
WorkingDirectory={cwd}
ExecStart={py} -m certificate_analyzer.runtime.gemeni run
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""
