"""Кроссплатформенная отправка системных уведомлений для Windows и Linux.

Обеспечивает всплывающие toast-оповещения в Windows (через Win10Toast или нативный PowerShell)
и Desktop Notifications в Linux (через notify-send) с защитой от спама (cooldown).
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
import platform
import subprocess
import sys
from typing import Any

logger = logging.getLogger(__name__)


class CrossPlatformNotifier:
    """Кроссплатформенный диспетчер уведомлений для рабочего стола."""

    def __init__(self, app_title: str = "Certificate Analyzer") -> None:
        """Инициализирует диспетчер уведомлений.

        Args:
            app_title: Название приложения для заголовков системных сообщений.
        """
        self.app_title = app_title
        self.system = platform.system()
        # Хранилище времени последнего уведомления: (cert_identifier, status) -> datetime
        self._notified_cache: dict[tuple[str, str], datetime] = {}

    def send_notification(self, title: str, message: str, urgency: str = "normal") -> bool:
        """Отправляет всплывающее системное уведомление пользователю.

        Args:
            title: Заголовок уведомления.
            message: Текст сообщения.
            urgency: Срочность ('low', 'normal', 'critical').

        Returns:
            bool: True, если уведомление было успешно отправлено ОС, иначе False.
        """
        full_title = f"{self.app_title}: {title}" if self.app_title else title

        if self.system == "Windows":
            return self._send_windows(full_title, message)
        if self.system == "Linux":
            return self._send_linux(full_title, message, urgency)

        # Fallback для остальных платформ (macOS / BSD)
        logger.info("[NOTIFY %s] %s: %s", urgency.upper(), full_title, message)
        return True

    def _send_windows(self, title: str, message: str) -> bool:
        """Отправляет уведомление в Windows.

        Сначала пытается использовать библиотеку win10toast, при её отсутствии
        вызывает встроенный PowerShell Toast API.

        Args:
            title: Заголовок.
            message: Тело сообщения.

        Returns:
            bool: Успешность отправки.
        """
        # Попытка 1: win10toast, если установлен
        try:
            import warnings

            with warnings.catch_warnings():
                warnings.filterwarnings("ignore")
                from win10toast import ToastNotifier

            toaster = ToastNotifier()
            toaster.show_toast(title, message, duration=7, threaded=True)
            return True
        except (ImportError, Exception) as exc:
            logger.debug("Win10Toast недоступен (%s), используется PowerShell fallback", exc)

        # Попытка 2: Нативный PowerShell Toast Notification
        try:
            escaped_title = title.replace('"', '`"').replace("'", "''")
            escaped_msg = message.replace('"', '`"').replace("'", "''")

            ps_script = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$textNodes = $template.GetElementsByTagName("text")
$textNodes.Item(0).AppendChild($template.CreateTextNode("{escaped_title}")) > $null
$textNodes.Item(1).AppendChild($template.CreateTextNode("{escaped_msg}")) > $null
$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("{self.app_title}")
$notification = [Windows.UI.Notifications.ToastNotification]::new($template)
$notifier.Show($notification)
"""
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=5,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception as ps_exc:
            logger.warning("Ошибка отправки через PowerShell toast: %s", ps_exc)

        # Fallback: логирование
        logger.warning("[WINDOWS ALERT] %s: %s", title, message)
        return False

    def _send_linux(self, title: str, message: str, urgency: str) -> bool:
        """Отправляет уведомление в Linux с помощью notify-send.

        Args:
            title: Заголовок.
            message: Текст.
            urgency: Срочность ('low', 'normal', 'critical').

        Returns:
            bool: Успешность отправки.
        """
        urgency_level = urgency if urgency in ("low", "normal", "critical") else "normal"
        try:
            subprocess.run(
                ["notify-send", "-u", urgency_level, "-a", self.app_title, title, message],
                timeout=5,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except FileNotFoundError:
            logger.warning("Утилита notify-send не найдена в системе. [LINUX ALERT] %s: %s", title, message)
        except Exception as exc:
            logger.warning("Ошибка при вызове notify-send: %s", exc)

        logger.info("[LINUX FALLBACK] %s: %s", title, message)
        return False

    def should_notify(
        self,
        cert_id: str,
        status: str,
        cooldown_hours: int = 24,
        now: datetime | None = None,
    ) -> bool:
        """Проверяет, прошло ли достаточно времени с момента последнего уведомления.

        Args:
            cert_id: Уникальный идентификатор сертификата (отпечаток или серийный номер).
            status: Текущий статус ('expired' или 'expiring_soon').
            cooldown_hours: Количество часов до повторного оповещения.
            now: Текущее время (если None, берется datetime.now()).

        Returns:
            bool: True, если необходимо отправить уведомление.
        """
        current_time = now or datetime.now()
        key = (cert_id, status)
        last_sent = self._notified_cache.get(key)

        if last_sent is None:
            return True

        return (current_time - last_sent) >= timedelta(hours=cooldown_hours)

    def mark_notified(
        self,
        cert_id: str,
        status: str,
        now: datetime | None = None,
    ) -> None:
        """Фиксирует факт отправки уведомления для предотвращения дублирования.

        Args:
            cert_id: Идентификатор сертификата.
            status: Статус сертификата.
            now: Время отправки.
        """
        self._notified_cache[(cert_id, status)] = now or datetime.now()

    def notify_expirations(
        self,
        expiring: list[dict[str, Any]],
        expired: list[dict[str, Any]],
        cooldown_hours: int = 24,
    ) -> int:
        """Анализирует списки проблемных сертификатов и отправляет сводные или точечные уведомления.

        Args:
            expiring: Список словарей сертификатов, срок которых скоро истекает.
            expired: Список словарей уже просроченных сертификатов.
            cooldown_hours: Период тишины в часах.

        Returns:
            int: Количество отправленных уведомлений.
        """
        now = datetime.now()
        sent_count = 0

        # Фильтруем те, о которых еще не сообщали в пределах периода cooldown
        new_expired = [
            c for c in expired
            if self.should_notify(c.get("id") or c.get("serial_number", ""), "expired", cooldown_hours, now)
        ]
        new_expiring = [
            c for c in expiring
            if self.should_notify(c.get("id") or c.get("serial_number", ""), "expiring_soon", cooldown_hours, now)
        ]

        if not new_expired and not new_expiring:
            return 0

        # Если сертификатов много, формируем сводное уведомление
        if len(new_expired) + len(new_expiring) > 3:
            summary_parts = []
            if new_expired:
                summary_parts.append(f"Просрочено: {len(new_expired)}")
            if new_expiring:
                summary_parts.append(f"Истекает: {len(new_expiring)}")

            title = "Внимание: проблемы с сертификатами"
            msg = ", ".join(summary_parts) + ". Проверьте приложение Certificate Analyzer."
            self.send_notification(title, msg, urgency="critical" if new_expired else "normal")
            sent_count += 1

            for c in new_expired:
                self.mark_notified(c.get("id") or c.get("serial_number", ""), "expired", now)
            for c in new_expiring:
                self.mark_notified(c.get("id") or c.get("serial_number", ""), "expiring_soon", now)

            return sent_count

        # Иначе отправляем детальные уведомления по каждому
        for c in new_expired:
            subject = c.get("common_name") or c.get("subject", "Сертификат")
            title = "Сертификат просрочен!"
            msg = f"{subject} истек {c.get('valid_to', '')}. Требуется перевыпуск."
            self.send_notification(title, msg, urgency="critical")
            self.mark_notified(c.get("id") or c.get("serial_number", ""), "expired", now)
            sent_count += 1

        for c in new_expiring:
            subject = c.get("common_name") or c.get("subject", "Сертификат")
            days = c.get("days_left", "?")
            title = "Срок действия сертификата истекает"
            msg = f"{subject} истекает через {days} дн. ({c.get('valid_to', '')})."
            self.send_notification(title, msg, urgency="normal")
            self.mark_notified(c.get("id") or c.get("serial_number", ""), "expiring_soon", now)
            sent_count += 1

        return sent_count
