"""Typed artifact models."""

from __future__ import annotations

from pydantic import ConfigDict

from androidctld.schema import ApiModel


class ScreenArtifacts(ApiModel):
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        alias_generator=ApiModel.model_config["alias_generator"],
        validate_by_alias=True,
        validate_by_name=True,
        use_enum_values=False,
        frozen=True,
    )

    screen_json: str | None = None
    screen_xml: str | None = None
    screenshot_png: str | None = None

    def with_screenshot(self, screenshot_png: str) -> ScreenArtifacts:
        return self.model_copy(update={"screenshot_png": screenshot_png})
