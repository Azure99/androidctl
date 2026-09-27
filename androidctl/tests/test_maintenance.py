import asyncio
import io
import json
import threading
import time
from contextlib import redirect_stdout
from types import SimpleNamespace

import pytest
import typer
from typer.main import get_command
from typer.testing import CliRunner

from androidctl import app as cli
from androidctl import coordinator as coordinator_module
from androidctl import local_call
from androidctl.cli_options import CliOptions
from androidctl.coordinator import Coordinator
from androidctl.file_jobs import FileJob
from androidctl.private_files import private_directory, write_json
from androidctl.protocol import (
    Fault,
    Result,
    completed,
)
from androidctl.state import HostState
from connection_test_support import connections
from file_test_support import Device as FileDevice
from pairing_test_support import (
    active_material,
    new_device_identity,
    paired,
    save_active,
)


async def test_ordinary_status_call_keeps_failed_checks_without_a_selected_device(
    tmp_path, monkeypatch
):
    async def unexpected(method, args):
        raise AssertionError("Unpaired status must not dispatch device work")

    async with connections(tmp_path, unexpected) as (coordinator, _, connect):
        await coordinator.forget("phone")
        monkeypatch.setattr(local_call, "connect_local", connect)

        def invoke():
            output = io.StringIO()
            with redirect_stdout(output), pytest.raises(cli.typer.Exit) as stopped:
                cli.device_call(
                    typer.Context(
                        get_command(cli.app).commands["status"],
                        info_name="status",
                        parent=typer.Context(get_command(cli.app)),
                        obj=CliOptions(None, "task", True),
                    ),
                    "status",
                )
            assert stopped.value.exit_code == 1
            return Result.model_validate(json.loads(output.getvalue()))

        result = await asyncio.to_thread(invoke)
        assert result.command == "status"
        assert result.result == "failed"
        assert [item["result"] for item in result.data["checks"]] == [
            "failed",
            "not checked",
            "not checked",
        ]


@pytest.mark.parametrize("worker", ["failed", "UNKNOWN", "refused", "lost"])
@pytest.mark.parametrize("reuse", [False, True])
async def test_status_attributes_worker_failures_without_rewriting_valid_results(
    tmp_path, worker, reuse
):
    calls = []
    observed_error = Fault("workerObservation", "Observed worker problem", "worker")

    async def dispatch(method, args):
        calls.append((method, args))
        if method == "status" and len(calls) == 1 and reuse:
            return completed(method).wire()
        if worker == "refused":
            raise Fault("workerUnavailable", "Worker is unavailable", "worker")
        if worker == "lost":
            raise asyncio.CancelledError()
        return Result(
            command=method,
            result=worker,
            data={"worker": "observed"},
            error=observed_error.problem,
        ).wire()

    async with connections(tmp_path, dispatch) as (coordinator, _, _):
        if reuse:
            warmup = await coordinator.dispatch(
                "call",
                {"command": "status", "args": {"timeoutMs": 2000}},
                deadline=time.monotonic() + 2,
            )
            assert warmup["result"] == "done"
        # Direct dispatch receives the deadline after LocalServer's reply reserve.
        result = await coordinator.dispatch(
            "call",
            {"command": "status", "args": {"timeoutMs": 2000}},
            deadline=time.monotonic() + 1.9,
        )
        assert result["result"] == ("UNKNOWN" if worker == "UNKNOWN" else "failed")
        assert [item["result"] for item in result["data"]["checks"]] == [
            "done",
            "done",
            "failed",
        ]
        assert len(calls) == (2 if reuse else 1)
        assert calls[-1][0] == "status"
        assert 0 < calls[-1][1]["timeoutMs"] < 2000
        assert result["data"]["checks"][1]["detail"] == (
            "reused authenticated connection" if reuse else "new connection"
        )
        assert ("deviceConnectMs" in result["data"]) is not reuse
        if worker in {"failed", "UNKNOWN"}:
            assert result["data"]["worker"] == "observed"
            assert result["error"] == observed_error.problem.wire()
            assert "reason" not in result["data"]["checks"][2]
            assert result["data"]["device"] == "phone"
        else:
            assert result["error"]
            assert result["data"]["checks"][2]["reason"]
            assert "device" not in result["data"]


async def test_status_pairing_lookup_timeout_stays_a_pre_dispatch_fault(
    tmp_path, monkeypatch
):
    private_directory(tmp_path / "state")
    coordinator = Coordinator(HostState(tmp_path / "state"), instance="test")

    async def late(_):
        await asyncio.sleep(1)

    monkeypatch.setattr(coordinator, "device", late)
    try:
        with pytest.raises(Fault, match="Local pairing lookup") as caught:
            await coordinator.dispatch(
                "call",
                {"command": "status", "args": {"timeoutMs": 20}},
                deadline=time.monotonic() + 0.02,
            )
        assert caught.value.problem.code == "timeout"
    finally:
        await coordinator.close()


async def test_status_tls_failure_stops_before_worker_check(tmp_path):
    async def unexpected(method, args):
        pytest.fail("Untrusted status dispatched device work")

    async with connections(tmp_path, unexpected) as (coordinator, saved, _):
        record = coordinator.state.record(saved["deviceId"])
        record.certificate = new_device_identity()["certificate"]
        coordinator.state.save_record(record)
        result = await coordinator.dispatch(
            "call", {"command": "status"}, deadline=time.monotonic() + 3
        )
        assert result["result"] == "failed"
        assert result["error"]["stage"] == "device.tls"
        assert result["data"]["deviceConnectMs"] >= 0
        assert [item["result"] for item in result["data"]["checks"]] == [
            "done",
            "failed",
            "not checked",
        ]


async def test_status_expired_before_dispatch_keeps_not_sent_without_checks(
    tmp_path, monkeypatch
):
    async def unexpected(method, args):
        pytest.fail("Expired status dispatched device work")

    async with connections(tmp_path, unexpected) as (coordinator, saved, connect):
        clock = [time.monotonic()]
        choose = coordinator.device

        async def expired_after_lookup(identifier):
            selected = await choose(identifier)
            # The CLI budget is 20 seconds; only the coordinator's clock advances.
            clock[0] += 30
            return selected

        monkeypatch.setattr(coordinator, "device", expired_after_lookup)
        monkeypatch.setattr(
            coordinator_module, "time", SimpleNamespace(monotonic=lambda: clock[0])
        )
        monkeypatch.setattr(local_call, "connect_local", connect)
        output = await asyncio.to_thread(
            CliRunner().invoke,
            cli.app,
            ["--json", "status"],
        )
        assert output.exit_code == 2, output.output
        result = Result.model_validate(json.loads(output.stdout))
        assert result.result == "not sent"
        assert result.error.code == "timeout"
        assert "before device dispatch" in result.reason
        assert "checks" not in result.data
        assert not coordinator.devices[saved["deviceId"]].in_use()


def test_forget_rejects_active_host_io_then_discards_ended_facts(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    write = FileJob.write

    def blocked(job, data):
        entered.set()
        assert release.wait(5)
        write(job, data)

    monkeypatch.setattr(FileJob, "write", blocked)

    async def run():
        private_directory(tmp_path / "state")
        state = HostState(tmp_path / "state")
        saved = paired(state, new_device_identity(), "phone")
        saved["pairingId"] = "pair"
        save_active(state, saved)
        write_json(
            state.selection, {"deviceId": state.resolve_selection("phone")[0].device_id}
        )
        private_directory(tmp_path / "files")
        coordinator = Coordinator(state, instance="test")
        file_device = FileDevice()

        device, _ = await coordinator.device(None)
        monkeypatch.setattr(device, "request", file_device.call)
        result = await coordinator.dispatch(
            "call",
            {
                "command": "file.start",
                "args": {
                    "timeoutMs": 15000,
                    "direction": "pull",
                    "local": str(tmp_path / "files/target"),
                    "remote": "/file",
                },
            },
            deadline=time.monotonic() + 15,
        )
        job = coordinator.files.items[result["data"]["job"]]
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            with pytest.raises(Fault, match="Finish local"):
                await coordinator.forget("phone")
            assert active_material(state, None) == saved
            assert not job.ended
            release.set()
            await job.task
            await asyncio.sleep(0)
            assert job.ended
            result = await coordinator.forget("phone")
            assert result.result == "done"
            assert result.data["discardedJobFacts"] == 1
            assert not coordinator.files.items
        finally:
            release.set()
            await coordinator.close()

    asyncio.run(run())
