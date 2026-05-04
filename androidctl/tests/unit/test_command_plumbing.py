from __future__ import annotations

from pathlib import Path
from typing import Annotated

import click
import pytest
import typer
from pydantic import BaseModel, StringConstraints

from androidctl.cli_options import CliOptions
from androidctl.commands import run_pipeline
from androidctl.commands.plumbing import build_and_run_command
from androidctl_contracts.daemon_api import ObserveCommandPayload


def _ctx(options: CliOptions) -> typer.Context:
    return typer.Context(click.Command("test"), info_name="test", obj=options)


def test_build_and_run_command_uses_resolved_cli_options(
    monkeypatch,
    tmp_path: Path,
) -> None:
    seen: dict[str, object] = {}

    monkeypatch.setattr(
        "androidctl.commands.plumbing.run_and_render",
        lambda request, **kwargs: seen.update(request=request, kwargs=kwargs),
    )

    build_and_run_command(
        ctx=_ctx(
            CliOptions(
                workspace_root=tmp_path,
            )
        ),
        workspace_root=None,
        build_request=lambda options: run_pipeline.CliCommandRequest(
            public_command="observe",
            command=ObserveCommandPayload(kind="observe"),
            workspace_root=options.workspace_root,
        ),
    )

    assert seen["request"] == run_pipeline.CliCommandRequest(
        public_command="observe",
        command=ObserveCommandPayload(kind="observe"),
        workspace_root=tmp_path,
    )
    assert seen["kwargs"] == {"public_command": "test"}


def test_build_and_run_command_routes_builder_validation_errors_to_usage(
    monkeypatch,
    tmp_path: Path,
) -> None:
    class SampleModel(BaseModel):
        value: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

    class UsageRaised(RuntimeError):
        pass

    monkeypatch.setattr(
        "androidctl.commands.plumbing.run_and_render",
        lambda request, **kwargs: (_ for _ in ()).throw(
            AssertionError("run_and_render should not be called")
        ),
    )
    monkeypatch.setattr(
        "androidctl.commands.plumbing.emit_usage_error",
        lambda message, **kwargs: (_ for _ in ()).throw(UsageRaised(message)),
    )

    def build_request(options: CliOptions) -> run_pipeline.CliCommandRequest:
        SampleModel(value="   ")
        return run_pipeline.CliCommandRequest(
            public_command="observe",
            command=ObserveCommandPayload(kind="observe"),
            workspace_root=options.workspace_root,
        )

    with pytest.raises(
        UsageRaised, match="value: String should have at least 1 character"
    ):
        build_and_run_command(
            ctx=_ctx(CliOptions(workspace_root=tmp_path)),
            workspace_root=None,
            build_request=build_request,
        )
