"""Command-line entry point for running androidctld."""

from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path
from threading import Event
from types import FrameType

from androidctld.auth.token_store import DaemonTokenStore
from androidctld.config import DEFAULT_HOST, DaemonConfig
from androidctld.daemon.server import AndroidctldHttpServer
from androidctld.runtime_policy import MAIN_LOOP_SLEEP_SECONDS


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="androidctld")
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--owner-id", required=True)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    config = DaemonConfig(
        workspace_root=Path(args.workspace_root),
        owner_id=args.owner_id,
        host=args.host,
        port=args.port,
    )
    should_stop = Event()

    def request_shutdown() -> None:
        should_stop.set()

    server = AndroidctldHttpServer(
        config=config,
        token_store=DaemonTokenStore(config),
        shutdown_callback=request_shutdown,
    )
    server.start()

    def request_stop(signum: int, frame: FrameType | None) -> None:
        del signum, frame
        request_shutdown()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    try:
        while not should_stop.wait(MAIN_LOOP_SLEEP_SECONDS):
            pass
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
