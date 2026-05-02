"""Thin screenshot command handler."""

from __future__ import annotations

from contextlib import suppress

from androidctld.actions.capabilities import ensure_command_supported
from androidctld.artifacts.writer import ArtifactWriter
from androidctld.commands.command_models import ScreenshotCommand
from androidctld.commands.result_models import (
    build_projected_retained_failure_result_for_error,
    build_retained_success_result,
)
from androidctld.device.interfaces import DeviceClientFactory
from androidctld.device.parsing import (
    decode_screenshot_body_base64,
    validate_screenshot_png_bytes,
)
from androidctld.errors import DaemonError
from androidctld.runtime import RuntimeKernel
from androidctld.runtime_policy import DEVICE_RPC_REQUEST_ID_SCREENSHOT


class ScreenshotCommandHandler:
    def __init__(
        self,
        *,
        runtime_kernel: RuntimeKernel,
        device_client_factory: DeviceClientFactory,
        artifact_writer: ArtifactWriter,
    ) -> None:
        self._runtime_kernel = runtime_kernel
        self._device_client_factory = device_client_factory
        self._artifact_writer = artifact_writer

    def handle(
        self,
        *,
        command: ScreenshotCommand,
    ) -> dict[str, object]:
        runtime = self._runtime_kernel.ensure_runtime()
        query_lane_acquired = False
        try:
            lifecycle_lease = self._runtime_kernel.capture_lifecycle_lease(runtime)
            self._runtime_kernel.acquire_query_lane(runtime)
            query_lane_acquired = True
            client = None
            if (
                runtime.connection is not None
                and runtime.device_token is not None
                and runtime.transport is None
            ):
                client = self._device_client_factory(
                    runtime,
                    lifecycle_lease=lifecycle_lease,
                )
            ensure_command_supported(runtime, command)
            if client is None:
                client = self._device_client_factory(
                    runtime,
                    lifecycle_lease=lifecycle_lease,
                )
            payload = client.screenshot_capture(
                request_id=DEVICE_RPC_REQUEST_ID_SCREENSHOT,
            )
            with runtime.lock:
                if not lifecycle_lease.is_current(runtime):
                    raise RuntimeError("runtime lifecycle changed during screenshot")
                decoded_body = decode_screenshot_body_base64(
                    payload.body_base64,
                    field_name="result.bodyBase64",
                )
                validate_screenshot_png_bytes(
                    decoded_body,
                    field_name="result.bodyBase64",
                    expected_width_px=payload.width_px,
                    expected_height_px=payload.height_px,
                )
                output_path = self._artifact_writer.write_screenshot_png(
                    runtime,
                    decoded_body,
                )
                attachment = self._runtime_kernel.attach_screenshot_artifact(
                    runtime,
                    lifecycle_lease,
                    screenshot_png=output_path.as_posix(),
                )
                if attachment is None:
                    with suppress(OSError):
                        output_path.unlink()
                    raise RuntimeError("runtime lifecycle changed during screenshot")
                artifacts = attachment.artifacts
                return build_retained_success_result(
                    command="screenshot",
                    artifacts=artifacts,
                ).model_dump(by_alias=True, mode="json")
        except DaemonError as error:
            return build_projected_retained_failure_result_for_error(
                command="screenshot",
                error=error,
                artifacts=None,
            ).model_dump(by_alias=True, mode="json")
        finally:
            if query_lane_acquired:
                self._runtime_kernel.release_progress_lane(runtime)
