from __future__ import annotations

from dataclasses import dataclass

from androidctl_contracts.command_catalog import (
    PUBLIC_COMMAND_NAMES,
    entry_for_public_command,
)


@dataclass(frozen=True)
class CommandView:
    public_name: str
    help_order: int
    pre_dispatch_execution_outcome: str | None


def _command_view(
    public_name: str,
    *,
    help_order: int,
    pre_dispatch_execution_outcome: str | None,
) -> CommandView:
    if entry_for_public_command(public_name) is None:
        raise RuntimeError(f"missing shared command catalog entry for {public_name!r}")
    return CommandView(
        public_name=public_name,
        help_order=help_order,
        pre_dispatch_execution_outcome=pre_dispatch_execution_outcome,
    )


_COMMAND_VIEWS = (
    _command_view(
        public_name="observe",
        help_order=0,
        pre_dispatch_execution_outcome="notApplicable",
    ),
    _command_view(
        public_name="list-apps",
        help_order=1,
        pre_dispatch_execution_outcome="notApplicable",
    ),
    _command_view(
        public_name="open",
        help_order=2,
        pre_dispatch_execution_outcome="notAttempted",
    ),
    _command_view(
        public_name="tap",
        help_order=3,
        pre_dispatch_execution_outcome="notAttempted",
    ),
    _command_view(
        public_name="long-tap",
        help_order=4,
        pre_dispatch_execution_outcome="notAttempted",
    ),
    _command_view(
        public_name="focus",
        help_order=5,
        pre_dispatch_execution_outcome="notAttempted",
    ),
    _command_view(
        public_name="type",
        help_order=6,
        pre_dispatch_execution_outcome="notAttempted",
    ),
    _command_view(
        public_name="submit",
        help_order=7,
        pre_dispatch_execution_outcome="notAttempted",
    ),
    _command_view(
        public_name="scroll",
        help_order=8,
        pre_dispatch_execution_outcome="notAttempted",
    ),
    _command_view(
        public_name="back",
        help_order=9,
        pre_dispatch_execution_outcome="notAttempted",
    ),
    _command_view(
        public_name="home",
        help_order=10,
        pre_dispatch_execution_outcome="notAttempted",
    ),
    _command_view(
        public_name="recents",
        help_order=11,
        pre_dispatch_execution_outcome="notAttempted",
    ),
    _command_view(
        public_name="notifications",
        help_order=12,
        pre_dispatch_execution_outcome="notAttempted",
    ),
    _command_view(
        public_name="wait",
        help_order=13,
        pre_dispatch_execution_outcome="notApplicable",
    ),
    _command_view(
        public_name="connect",
        help_order=14,
        pre_dispatch_execution_outcome="notApplicable",
    ),
    _command_view(
        public_name="screenshot",
        help_order=15,
        pre_dispatch_execution_outcome="notApplicable",
    ),
    _command_view(
        public_name="close",
        help_order=16,
        pre_dispatch_execution_outcome="notApplicable",
    ),
)

_command_view_names = [view.public_name for view in _COMMAND_VIEWS]
_help_orders = [view.help_order for view in _COMMAND_VIEWS]
if len(_command_view_names) != len(set(_command_view_names)):
    raise RuntimeError("duplicate public command in CLI command views")
if len(_help_orders) != len(set(_help_orders)):
    raise RuntimeError("duplicate help order in CLI command views")
if set(_command_view_names) != PUBLIC_COMMAND_NAMES:
    missing = sorted(PUBLIC_COMMAND_NAMES - set(_command_view_names))
    extra = sorted(set(_command_view_names) - PUBLIC_COMMAND_NAMES)
    raise RuntimeError(
        "CLI command views drifted from shared public catalog: "
        f"missing={missing}, extra={extra}"
    )

_COMMAND_VIEW_BY_PUBLIC_NAME = {view.public_name: view for view in _COMMAND_VIEWS}


def command_view_for_public_command(public_name: str) -> CommandView | None:
    return _COMMAND_VIEW_BY_PUBLIC_NAME.get(public_name)


def help_order_for_public_command(public_name: str) -> int:
    view = command_view_for_public_command(public_name)
    if view is None:
        return len(_COMMAND_VIEWS)
    return view.help_order


def pre_dispatch_execution_outcome_for_public_command(
    public_name: str | None,
) -> str | None:
    if public_name is None:
        return None
    view = command_view_for_public_command(public_name)
    if view is None:
        return None
    return view.pre_dispatch_execution_outcome
