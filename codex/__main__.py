"""Командная строка автономного фонового процесса."""

from __future__ import annotations

import argparse
import logging
import signal

from certificate_analyzer.runtime.codex.config import load_config
from certificate_analyzer.runtime.codex.host import RuntimeHost


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Фоновый контроль сертификатов")
    parser.add_argument("--config", help="Путь к runtime.json")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    host = RuntimeHost(load_config(args.config))

    def stop(*_: object) -> None:
        # Signal handler только будит основной цикл; освобождение ресурсов
        # выполняется в finally метода run_forever.
        host.initiate_stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, stop)
    host.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
