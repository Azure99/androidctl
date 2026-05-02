"""Typed runtime open-target ADTs."""

from __future__ import annotations

from dataclasses import dataclass, field

from androidctld.errors import bad_request


@dataclass(frozen=True)
class OpenAppTarget:
    package_name: str
    kind: str = field(default="app", init=False)

    @property
    def value(self) -> str:
        return self.package_name

    @property
    def required_action_kind(self) -> str:
        return "launchApp"


@dataclass(frozen=True)
class OpenUrlTarget:
    url: str
    kind: str = field(default="url", init=False)

    @property
    def value(self) -> str:
        return self.url

    @property
    def required_action_kind(self) -> str:
        return "openUrl"


def validate_open_target(
    target: OpenAppTarget | OpenUrlTarget,
) -> OpenAppTarget | OpenUrlTarget:
    if isinstance(target, OpenAppTarget):
        if not target.package_name:
            raise bad_request("open requires target.kind app|url and target.value")
        return target
    if isinstance(target, OpenUrlTarget):
        if not target.url:
            raise bad_request("open requires target.kind app|url and target.value")
        return target
    raise bad_request("open requires target.kind app|url and target.value")


__all__ = [
    "OpenAppTarget",
    "OpenUrlTarget",
    "validate_open_target",
]
