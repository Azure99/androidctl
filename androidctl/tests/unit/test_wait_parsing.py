import pytest
from androidctl_contracts.daemon_api import (
    AppPredicatePayload,
    GonePredicatePayload,
    IdlePredicatePayload,
    ScreenChangePredicatePayload,
    TextPresentPredicatePayload,
)

from androidctl.command_payloads import (
    LateBoundScreenRelativePredicate,
)
from androidctl.parsing.wait import parse_wait_predicate


def test_parse_screen_change_wait_uses_source_screen_id() -> None:
    predicate = parse_wait_predicate("screen-change", source_screen_id="screen-1")

    assert predicate.model_dump(exclude_none=True) == {
        "kind": "screen-change",
        "sourceScreenId": "screen-1",
    }
    assert predicate == ScreenChangePredicatePayload(
        kind="screen-change",
        source_screen_id="screen-1",
    )


def test_parse_gone_wait_requires_ref_and_source_screen_id() -> None:
    predicate = parse_wait_predicate(
        "gone",
        ref="n7",
        source_screen_id="screen-1",
    )

    assert predicate.model_dump(exclude_none=True) == {
        "kind": "gone",
        "ref": "n7",
        "sourceScreenId": "screen-1",
    }
    assert predicate == GonePredicatePayload(
        kind="gone",
        ref="n7",
        source_screen_id="screen-1",
    )


def test_parse_text_present_wait_requires_text() -> None:
    predicate = parse_wait_predicate("text-present", text="Wi-Fi")

    assert predicate == TextPresentPredicatePayload(kind="text-present", text="Wi-Fi")


def test_parse_app_wait_requires_package_name() -> None:
    predicate = parse_wait_predicate("app", package_name="com.android.settings")

    assert predicate == AppPredicatePayload(
        kind="app",
        package_name="com.android.settings",
    )


def test_parse_idle_wait_has_no_extra_payload() -> None:
    predicate = parse_wait_predicate("idle")

    assert predicate == IdlePredicatePayload(kind="idle")


def test_parse_wait_predicate_allows_deferred_screen_change_source_screen_id() -> None:
    predicate = parse_wait_predicate("screen-change")

    assert predicate == LateBoundScreenRelativePredicate(kind="screen-change")


@pytest.mark.parametrize(
    ("until", "kwargs"),
    [
        ("screen-change", {}),
        ("gone", {"ref": "n7"}),
    ],
)
def test_parse_wait_predicate_rejects_blank_source_screen_id(
    until: str,
    kwargs: dict[str, str],
) -> None:
    with pytest.raises(ValueError, match="--screen-id must be non-empty"):
        parse_wait_predicate(
            until,
            source_screen_id="   ",
            **kwargs,
        )


def test_parse_wait_predicate_allows_deferred_gone_source_screen_id() -> None:
    predicate = parse_wait_predicate(
        "gone",
        ref="n7",
    )

    assert predicate == LateBoundScreenRelativePredicate(kind="gone", ref="n7")


@pytest.mark.parametrize(
    ("until", "kwargs", "message"),
    [
        (
            "gone",
            {"source_screen_id": "screen-1"},
            "gone wait requires --ref",
        ),
        (
            "text-present",
            {},
            "text-present wait requires --text",
        ),
        (
            "app",
            {},
            "app wait requires --app",
        ),
    ],
)
def test_parse_wait_predicate_rejects_missing_required_arguments(
    until: str,
    kwargs: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        parse_wait_predicate(until, **kwargs)


def test_parse_wait_predicate_rejects_unknown_predicate() -> None:
    with pytest.raises(
        ValueError,
        match=(
            "wait predicate must be one of: app, gone, idle, "
            "screen-change, text-present"
        ),
    ):
        parse_wait_predicate("watch")
