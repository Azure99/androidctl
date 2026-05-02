from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import typer

from androidctl.exit_codes import ExitCode
from androidctl.setup import adb as setup_adb

_WORKSPACE_ROOT_HELP = "Accepted for CLI consistency; ignored by wireless ADB helpers."


@dataclass(frozen=True)
class WirelessAdbCommandError(RuntimeError):
    code: str
    message: str
    exit_code: ExitCode = ExitCode.ENVIRONMENT

    def __str__(self) -> str:
        return self.message


def register(app: typer.Typer) -> None:
    @app.command(
        "adb-pair",
        help="Auxiliary helper for Android wireless debugging pairing.",
    )
    def adb_pair(
        pair: str | None = typer.Option(
            None,
            "--pair",
            help="Wireless debugging pair endpoint as HOST:PORT.",
        ),
        code: str | None = typer.Option(
            None,
            "--code",
            help="Pairing code shown on the Android device.",
        ),
        workspace_root: Path | None = typer.Option(
            None,
            "--workspace-root",
            help=_WORKSPACE_ROOT_HELP,
        ),
    ) -> None:
        _ignore_workspace_root(workspace_root)
        try:
            _run_adb_pair(pair=pair, code=code)
        except WirelessAdbCommandError as error:
            _emit_failure("adb-pair", error)

    @app.command(
        "adb-connect",
        help="Auxiliary helper for connecting an already paired wireless ADB device.",
    )
    def adb_connect(
        endpoint: str = typer.Argument(
            ...,
            help="Wireless debugging connect endpoint as HOST:PORT.",
        ),
        workspace_root: Path | None = typer.Option(
            None,
            "--workspace-root",
            help=_WORKSPACE_ROOT_HELP,
        ),
    ) -> None:
        _ignore_workspace_root(workspace_root)
        try:
            _run_adb_connect(endpoint=endpoint)
        except WirelessAdbCommandError as error:
            _emit_failure("adb-connect", error)


def _run_adb_pair(*, pair: str | None, code: str | None) -> None:
    pair_endpoint = _required_value(
        pair,
        code="ADB_PAIR_ENDPOINT_REQUIRED",
        message="pair endpoint is required; pass --pair HOST:PAIR_PORT",
    )
    pairing_code = _required_value(
        code,
        code="ADB_PAIR_CODE_REQUIRED",
        message=(
            "pairing code is required; open Android Wireless debugging and pass "
            "--code"
        ),
    )
    wireless_error: WirelessAdbCommandError | None = None
    try:
        setup_adb.pair_wireless_device(
            pair_endpoint=pair_endpoint,
            code=pairing_code,
        )
    except setup_adb.SetupAdbError as error:
        wireless_error = _wireless_error_from_adb(error)
    if wireless_error is not None:
        raise wireless_error
    _emit_progress("wireless ADB: paired device")


def _run_adb_connect(*, endpoint: str) -> None:
    connect_endpoint: str | None = None
    wireless_error: WirelessAdbCommandError | None = None
    try:
        connect_endpoint = setup_adb.validate_wireless_endpoint(
            endpoint,
            label="connect endpoint",
        )
        setup_adb.connect_wireless_device(connect_endpoint=connect_endpoint)
    except setup_adb.SetupAdbError as error:
        wireless_error = _wireless_error_from_adb(error)
    if wireless_error is not None:
        raise wireless_error
    assert connect_endpoint is not None
    _emit_progress("wireless ADB: connected device")
    _emit_progress(f"wireless ADB: run setup with --serial {connect_endpoint}")


def _required_value(value: str | None, *, code: str, message: str) -> str:
    normalized = value.strip() if value is not None else ""
    if not normalized:
        raise WirelessAdbCommandError(
            code=code,
            message=message,
            exit_code=ExitCode.USAGE,
        )
    return normalized


def _wireless_error_from_adb(error: setup_adb.SetupAdbError) -> WirelessAdbCommandError:
    exit_code = (
        ExitCode.USAGE
        if error.code in {"ADB_PAIR_CODE_REQUIRED", "ADB_INVALID_WIRELESS_ENDPOINT"}
        else ExitCode.ENVIRONMENT
    )
    return WirelessAdbCommandError(
        code=error.code,
        message=error.message,
        exit_code=exit_code,
    )


def _ignore_workspace_root(workspace_root: Path | None) -> None:
    del workspace_root


def _emit_progress(message: str) -> None:
    typer.echo(message, err=True)


def _emit_failure(command: str, error: WirelessAdbCommandError) -> None:
    typer.echo(f"androidctl {command} failed [{error.code}]: {error.message}", err=True)
    raise typer.Exit(code=int(error.exit_code))
