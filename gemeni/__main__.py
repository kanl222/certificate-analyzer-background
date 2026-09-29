"""Точка входа CLI для автономного запуска и управления фоновым сервисом Gemini.

Поддерживает команды:
- run: запуск процесса мониторинга в текущей консоли;
- status: просмотр состояния запущенного сервиса через локальный API;
- check: внеплановый запуск проверки сертификатов;
- report: внеплановая генерация еженедельного отчета;
- stop: остановка работающего фонового процесса;
- systemd: вывод готового шаблона службы systemd для Linux;
- init-config: создание стандартного файла конфигурации gemeni_config.json.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from certificate_analyzer.runtime.gemeni.client import GeminiApiClient
from certificate_analyzer.runtime.gemeni.config import DEFAULT_CONFIG_PATH, GeminiRuntimeConfig
from certificate_analyzer.runtime.gemeni.host import GeminiRuntimeHost


def main() -> None:
    """Точка входа командной строки."""
    parser = argparse.ArgumentParser(
        description="Фоновый сервис мониторинга сертификатов Gemini",
        prog="python -m certificate_analyzer.runtime.gemeni",
    )
    parser.add_argument(
        "--config",
        "-c",
        default=DEFAULT_CONFIG_PATH,
        help="Путь к файлу конфигурации JSON (default: gemeni_config.json)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Включить подробный вывод отладочных логов",
    )

    subparsers = parser.add_subparsers(dest="command", help="Команды управления")

    # run
    subparsers.add_parser("run", help="Запустить процесс мониторинга в текущей консоли")

    # status
    subparsers.add_parser("status", help="Запросить статус работающего процесса через API")

    # check
    subparsers.add_parser("check", help="Запустить внеплановую проверку сертификатов")

    # report
    subparsers.add_parser("report", help="Сформировать отчет по сертификатам прямо сейчас")

    # stop
    subparsers.add_parser("stop", help="Остановить работающий фоновый процесс через API")

    # systemd
    subparsers.add_parser("systemd", help="Сгенерировать unit-файл systemd для Linux")

    # init-config
    subparsers.add_parser("init-config", help="Создать файл конфигурации gemeni_config.json с настройками по умолчанию")

    args = parser.parse_args()

    # Настройка логирования
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    config = GeminiRuntimeConfig.load_from_file(args.config)
    client = GeminiApiClient(base_url=f"http://{config.api_host}:{config.api_port}", token=config.api_token)

    cmd = args.command or "run"

    if cmd == "run":
        host = GeminiRuntimeHost(config=config)
        host.start(block=True)

    elif cmd == "status":
        try:
            status = client.get_status()
            print(json.dumps(status, indent=2, ensure_ascii=False))
        except ConnectionError as err:
            print(f"Сервис не отвечает: {err}", file=sys.stderr)
            sys.exit(1)

    elif cmd == "check":
        try:
            res = client.trigger_check()
            print(f"Ответ сервиса: {res.get('message')}")
        except ConnectionError as err:
            print(f"Не удалось связаться с сервисом: {err}", file=sys.stderr)
            sys.exit(1)

    elif cmd == "report":
        try:
            res = client.trigger_report()
            print(f"Отчет создан: {res.get('report_path')}")
        except ConnectionError as err:
            print(f"Не удалось связаться с сервисом: {err}", file=sys.stderr)
            sys.exit(1)

    elif cmd == "stop":
        try:
            success = client.stop_service()
            print("Команда на остановку сервиса успешно отправлена." if success else "Ошибка остановки.")
        except ConnectionError as err:
            print(f"Сервис уже остановлен или недоступен: {err}", file=sys.stderr)

    elif cmd == "systemd":
        print(GeminiRuntimeHost.generate_systemd_unit())

    elif cmd == "init-config":
        default_cfg = GeminiRuntimeConfig()
        default_cfg.save_to_file(args.config)
        print(f"Файл конфигурации создан: {args.config}")


if __name__ == "__main__":
    main()
