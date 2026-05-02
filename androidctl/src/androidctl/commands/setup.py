from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

import typer

from androidctl import __version__
from androidctl.cli_options import command_cli_options
from androidctl.exit_codes import ExitCode
from androidctl.setup import accessibility as setup_accessibility
from androidctl.setup import adb as setup_adb
from androidctl.setup import pairing as setup_pairing
from androidctl.setup import verify as setup_verify
from androidctl.setup.apk_resource import (
    packaged_agent_apk_name,
    packaged_agent_apk_path,
)


@dataclass(frozen=True)
class SetupError(RuntimeError):
    code: str
    layer: str
    message: str
    exit_code: ExitCode = ExitCode.ERROR

    def __str__(self) -> str:
        return self.message


def register(app: typer.Typer) -> None:
    @app.command(
        "setup",
        help="Onboarding helper for preparing an authorized ADB device.",
    )
    def setup(
        ctx: typer.Context,
        adb: bool = typer.Option(False, "--adb", help="Use authorized ADB transport."),
        serial: str | None = typer.Option(None, "--serial", help="ADB serial."),
        apk: Path | None = typer.Option(
            None,
            "--apk",
            help="Override Android Device Agent APK path.",
        ),
        dry_run: bool = typer.Option(
            False,
            "--dry-run",
            help="Print the setup plan without running ADB or mutating a device.",
        ),
        skip_install: bool = typer.Option(
            False,
            "--skip-install",
            help="Skip the APK install step.",
        ),
        manual_accessibility: bool = typer.Option(
            False,
            "--manual-accessibility",
            help="Skip ADB Accessibility writes and guide manual enablement.",
        ),
        workspace_root: Path | None = typer.Option(None, "--workspace-root"),
    ) -> None:
        options = command_cli_options(ctx, workspace_root=workspace_root)
        try:
            run_setup(
                adb=adb,
                serial=serial,
                apk=apk,
                dry_run=dry_run,
                skip_install=skip_install,
                manual_accessibility=manual_accessibility,
                workspace_root=options.workspace_root,
            )
        except SetupError as error:
            _emit_failure(error)


def run_setup(
    *,
    adb: bool,
    serial: str | None,
    apk: Path | None,
    dry_run: bool,
    skip_install: bool,
    manual_accessibility: bool,
    workspace_root: Path | None,
) -> None:
    if not adb:
        raise SetupError(
            code="SETUP_REQUIRES_ADB",
            layer="usage",
            message="setup currently requires --adb",
            exit_code=ExitCode.USAGE,
        )

    _emit_progress("androidctl setup: authorized ADB onboarding")
    if dry_run:
        _emit_dry_run_plan(
            serial=serial,
            apk=apk,
            skip_install=skip_install,
            manual_accessibility=manual_accessibility,
            workspace_root=workspace_root,
        )
        return

    try:
        devices = setup_adb.list_adb_devices()
        selected_device = setup_adb.select_eligible_device(devices, serial=serial)
    except setup_adb.SetupAdbError as error:
        raise SetupError(
            code=error.code,
            layer=error.layer,
            message=error.message,
            exit_code=ExitCode.ENVIRONMENT,
        ) from error

    if serial is None:
        _emit_progress("ADB: selected the only authorized device")
    else:
        _emit_progress("ADB: selected requested authorized device")

    if skip_install:
        _emit_progress("install: skipped by --skip-install")
    else:
        _emit_progress(_apk_plan_line(apk, dry_run=False))
        _install_agent_apk(apk=apk, serial=selected_device.serial)

    token = _start_setup_activity_with_token(serial=selected_device.serial)
    _enable_accessibility(
        serial=selected_device.serial,
        manual_accessibility=manual_accessibility,
    )
    _verify_setup_readiness(
        serial=selected_device.serial,
        token=token,
        workspace_root=workspace_root,
    )
    _emit_progress("status: setup complete")


def _emit_dry_run_plan(
    *,
    serial: str | None,
    apk: Path | None,
    skip_install: bool,
    manual_accessibility: bool,
    workspace_root: Path | None,
) -> None:
    _emit_progress("mode: dry-run; no ADB command or device mutation will run")
    if workspace_root is not None:
        _emit_progress("workspace: command override provided")
    if serial is None:
        _emit_progress("ADB: would select the only authorized device")
    else:
        _emit_progress("ADB: would select the requested authorized device")
    if skip_install:
        _emit_progress("install: skipped by --skip-install")
    else:
        _emit_progress(_apk_plan_line(apk, dry_run=True))
    _emit_progress("launch: would open AndroidCtl and start the foreground server")
    _emit_progress("token: would provision a host-generated device token")
    if manual_accessibility:
        _emit_progress("accessibility: would enter manual enablement fallback")
    else:
        _emit_progress("accessibility: would try ADB enable, then fallback if needed")
    _emit_progress("verify: would connect daemon runtime and run readiness checks")
    _emit_progress("status: dry-run complete")


def _apk_plan_line(
    apk: Path | None,
    *,
    dry_run: bool,
) -> str:
    prefix = "would use" if dry_run else "using"
    if apk is not None:
        return f"install: {prefix} override APK path"
    apk_name = packaged_agent_apk_name(__version__)
    return f"install: {prefix} packaged APK {apk_name}"


def _install_agent_apk(
    *,
    apk: Path | None,
    serial: str,
) -> None:
    try:
        with _apk_path_context(apk) as apk_path:
            setup_adb.install_apk(apk_path, serial=serial)
    except FileNotFoundError as error:
        raise SetupError(
            code="APK_NOT_FOUND",
            layer="install",
            message=str(error),
        ) from error
    except setup_adb.SetupAdbError as error:
        raise SetupError(
            code=error.code,
            layer="install",
            message=error.message,
        ) from error
    _emit_progress("install: APK installed")


def _apk_path_context(apk: Path | None) -> AbstractContextManager[Path]:
    if apk is not None:
        return nullcontext(apk)
    return packaged_agent_apk_path(__version__)


def _start_setup_activity_with_token(*, serial: str) -> str:
    try:
        setup_adb.force_stop_app(serial=serial)
        token = setup_pairing.generate_host_token()
        setup_adb.start_setup_activity(
            serial=serial,
            string_extras={setup_pairing.SETUP_DEVICE_TOKEN_EXTRA: token},
        )
    except setup_pairing.SetupPairingError as error:
        raise SetupError(
            code=error.code,
            layer=error.layer,
            message=error.message,
        ) from error
    except setup_adb.SetupAdbError as error:
        raise SetupError(
            code=error.code,
            layer="launch",
            message=error.message,
        ) from error
    _emit_progress("launch: existing app process stopped")
    _emit_progress("launch: setup activity started")
    _emit_progress("token: provisioned host-generated device token")
    return token


def _enable_accessibility(
    *,
    serial: str,
    manual_accessibility: bool,
) -> None:
    if manual_accessibility:
        _emit_accessibility_fallback("manual enablement requested")
        return
    try:
        result = setup_accessibility.enable_agent_accessibility(serial=serial)
    except setup_accessibility.SetupAccessibilityError as error:
        _emit_progress(
            f"accessibility: ADB enable not confirmed ({error.code}); "
            "using manual fallback"
        )
        _emit_accessibility_fallback(error.message)
        return
    if result.changed_service_list:
        _emit_progress("accessibility: AndroidCtl service added via ADB settings")
    else:
        _emit_progress("accessibility: AndroidCtl service already in ADB settings")
    _emit_progress("accessibility: ADB settings write confirmed")


def _emit_accessibility_fallback(reason: str) -> None:
    _emit_progress(f"accessibility: manual fallback required: {reason}")
    _emit_progress(
        f"accessibility: {setup_accessibility.MANUAL_ACCESSIBILITY_FALLBACK}"
    )


def _verify_setup_readiness(
    *,
    serial: str,
    token: str,
    workspace_root: Path | None,
) -> None:
    try:
        setup_verify.verify_setup_readiness(
            serial=serial,
            token=token,
            workspace_root=workspace_root,
        )
    except setup_verify.SetupVerificationError as error:
        raise SetupError(
            code=error.code,
            layer=error.layer,
            message=error.message,
        ) from error
    _emit_progress("verify: daemon connect/readiness check succeeded")


def _emit_progress(message: str) -> None:
    typer.echo(message, err=True)


def _emit_failure(error: SetupError) -> NoReturn:
    typer.echo(
        f"androidctl setup failed [{error.layer}/{error.code}]: {error.message}",
        err=True,
    )
    raise typer.Exit(code=int(error.exit_code))
