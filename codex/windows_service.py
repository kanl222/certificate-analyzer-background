"""Обёртка pywin32 для запуска RuntimeHost как службы Windows.

Установка не выполняется автоматически. Команды приведены в README этого
каталога; модуль остаётся полностью изолированным от основного CLI проекта.
"""

from __future__ import annotations

import os
import sys

if sys.platform != "win32":
    raise RuntimeError("Модуль службы Windows можно использовать только в Windows")

import servicemanager
import win32event
import win32service
import win32serviceutil

from certificate_analyzer.runtime.codex.config import load_config
from certificate_analyzer.runtime.codex.host import RuntimeHost


class CertificateRuntimeWindowsService(win32serviceutil.ServiceFramework):
    _svc_name_ = "CertificateAnalyzerRuntimeCodex"
    _svc_display_name_ = "Certificate Analyzer Runtime (isolated)"
    _svc_description_ = "Проверка сроков сертификатов и недельная отчётность"

    def __init__(self, args: list[str]) -> None:
        super().__init__(args)
        self._stop_handle = win32event.CreateEvent(None, 0, 0, None)
        config_path = os.environ.get("CERTIFICATE_ANALYZER_RUNTIME_CONFIG")
        self._host = RuntimeHost(load_config(config_path))

    def SvcStop(self) -> None:  # noqa: N802 - контракт pywin32
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        self._host.stop()
        win32event.SetEvent(self._stop_handle)

    def SvcDoRun(self) -> None:  # noqa: N802 - контракт pywin32
        servicemanager.LogInfoMsg("Certificate Analyzer Runtime starting")
        self._host.start()
        win32event.WaitForSingleObject(self._stop_handle, win32event.INFINITE)
        servicemanager.LogInfoMsg("Certificate Analyzer Runtime stopped")


def main() -> None:
    win32serviceutil.HandleCommandLine(CertificateRuntimeWindowsService)


if __name__ == "__main__":
    main()
