from __future__ import annotations

import re
import sys
from importlib import import_module
from pathlib import Path
from xml.etree import ElementTree

import pytest

from androidctl_contracts.public_screen import PUBLIC_NODE_ROLE_VALUES
from androidctld.rendering.screen_xml import render_screen_xml
from androidctld.semantics.public_models import (
    OmittedEntry,
    PublicApp,
    PublicFocus,
    PublicNode,
    PublicScreen,
    PublicSurface,
    TransientItem,
    VisibleWindow,
    build_public_groups,
    dump_public_screen,
)

ANDROIDCTL_SRC = Path(__file__).resolve().parents[3] / "androidctl" / "src"
sys.path.insert(0, ANDROIDCTL_SRC.as_posix())

render_xml = import_module("androidctl.renderers.xml").render_xml


def _representative_screen() -> PublicScreen:
    return PublicScreen(
        screen_id="screen-00013",
        app=PublicApp(
            package_name="com.android.settings",
            activity_name="com.android.settings.Settings",
            requested_package_name="settings",
            resolved_package_name="com.android.settings",
            match_type="alias",
        ),
        surface=PublicSurface(
            keyboard_visible=False,
            blocking_group="dialog",
            focus=PublicFocus(input_ref="n2"),
        ),
        groups=build_public_groups(
            order=("dialog", "targets", "keyboard", "system", "context"),
            targets=(
                PublicNode(
                    kind="container",
                    ref="n1",
                    role="scroll-container",
                    label="Networks",
                    state=("expanded",),
                    actions=("scroll",),
                    scroll_directions=("down",),
                    window_ref="w1",
                    children=(
                        PublicNode(
                            ref="n2",
                            role="input",
                            label="Network search",
                            state=("selected",),
                            actions=("type",),
                            submit_refs=("n4",),
                            within="Networks",
                            value="Connected",
                            window_ref="w1",
                        ),
                        PublicNode(
                            ref="n4",
                            role="switch",
                            label="Bluetooth",
                            actions=("tap",),
                            within="",
                            value="",
                            window_ref="w1",
                        ),
                        PublicNode(
                            kind="text",
                            text="Signal strong",
                            within="Wi-Fi",
                            value="Connected",
                            window_ref="w1",
                        ),
                        PublicNode(
                            ref="n5",
                            role="text",
                            label="Network status",
                            window_ref="w1",
                        ),
                    ),
                ),
            ),
            dialog=(
                PublicNode(
                    ref="n7",
                    role="button",
                    label="Allow",
                    actions=("tap",),
                    window_ref="w2",
                ),
            ),
            system=(
                PublicNode(
                    kind="text",
                    text="Battery 100 percent.",
                    window_ref="w3",
                ),
            ),
        ),
        omitted=(OmittedEntry(group="context", reason="offscreen", count=2),),
        visible_windows=(
            VisibleWindow(
                window_ref="w1",
                role="main",
                focused=True,
                blocking=False,
            ),
            VisibleWindow(
                window_ref="w2",
                role="dialog",
                focused=False,
                blocking=True,
            ),
            VisibleWindow(
                window_ref="w3",
                role="system",
                focused=False,
                blocking=False,
            ),
        ),
        transient=(TransientItem(text="Saved", kind="toast"),),
    )


def test_daemon_standalone_screen_xml_matches_cli_inline_screen() -> None:
    screen = _representative_screen()
    daemon_screen = ElementTree.fromstring(render_screen_xml(screen))
    cli_result = ElementTree.fromstring(
        render_xml(
            {
                "ok": True,
                "command": "observe",
                "category": "observe",
                "payloadMode": "full",
                "sourceScreenId": screen.screen_id,
                "nextScreenId": screen.screen_id,
                "truth": {
                    "executionOutcome": "notApplicable",
                    "continuityStatus": "stable",
                    "observationQuality": "authoritative",
                },
                "screen": dump_public_screen(screen),
                "uncertainty": [],
                "warnings": [],
                "artifacts": {},
            }
        )
    )
    cli_screen = cli_result.find("./screen")
    assert cli_screen is not None

    _assert_elements_equal(daemon_screen, cli_screen)
    container = daemon_screen.find("./groups/targets/scroll-container")
    input_node = daemon_screen.find(".//*[@ref='n2']")
    switch_node = daemon_screen.find(".//*[@ref='n4']")
    empty_node = daemon_screen.find(".//*[@ref='n5']")
    dialog_node = daemon_screen.find(".//*[@ref='n7']")
    literal = daemon_screen.find("./groups/targets/scroll-container/literal")
    window = daemon_screen.find("./visibleWindows/window[@windowRef='w2']")
    assert container is not None
    assert "role" not in container.attrib
    assert input_node is not None
    assert input_node.tag == "input"
    assert input_node.attrib["actions"] == "type"
    assert input_node.attrib["state"] == "selected"
    assert input_node.attrib["submitRefs"] == "n4"
    assert "role" not in input_node.attrib
    assert switch_node is not None
    assert switch_node.tag == "switch"
    assert switch_node.attrib["actions"] == "tap"
    assert "state" not in switch_node.attrib
    assert "submitRefs" not in switch_node.attrib
    assert "role" not in switch_node.attrib
    assert empty_node is not None
    assert empty_node.tag == "text"
    assert "actions" not in empty_node.attrib
    assert "state" not in empty_node.attrib
    assert "role" not in empty_node.attrib
    assert dialog_node is not None
    assert dialog_node.tag == "button"
    assert dialog_node.attrib["actions"] == "tap"
    assert "state" not in dialog_node.attrib
    assert "role" not in dialog_node.attrib
    assert literal is not None
    assert literal.text == "Signal strong"
    assert window is not None
    assert window.attrib["role"] == "dialog"
    assert "submitsInputRefs" not in ElementTree.tostring(
        daemon_screen,
        encoding="unicode",
    )


def test_daemon_standalone_screen_xml_omits_empty_submit_refs() -> None:
    screen = _representative_screen().model_copy(
        update={
            "surface": PublicSurface(
                keyboard_visible=False,
                blocking_group="dialog",
                focus=PublicFocus(input_ref="n1"),
            ),
            "groups": build_public_groups(
                order=("dialog", "targets", "keyboard", "system", "context"),
                targets=(
                    PublicNode(
                        ref="n1",
                        role="input",
                        label="Search",
                        actions=("type",),
                    ),
                    PublicNode(
                        ref="n2",
                        role="button",
                        label="Search",
                        actions=("tap",),
                    ),
                ),
                dialog=(
                    PublicNode(
                        ref="n7",
                        role="button",
                        label="Allow",
                        actions=("tap",),
                        window_ref="w2",
                    ),
                ),
            ),
        }
    )

    root = ElementTree.fromstring(render_screen_xml(screen))
    input_node = root.find(".//*[@ref='n1']")
    assert input_node is not None
    assert input_node.attrib["actions"] == "type"
    assert "state" not in input_node.attrib
    assert "submitRefs" not in input_node.attrib


@pytest.mark.parametrize("role", PUBLIC_NODE_ROLE_VALUES)
def test_daemon_standalone_screen_xml_renders_public_node_roles_as_tags(
    role: str,
) -> None:
    screen = PublicScreen(
        screen_id="screen-role-tag",
        app=PublicApp(package_name="com.android.settings"),
        surface=PublicSurface(keyboard_visible=False, focus=PublicFocus()),
        groups=build_public_groups(
            context=(
                PublicNode(
                    ref="n1",
                    role=role,
                    label=f"{role} item",
                ),
            ),
        ),
        omitted=(),
        visible_windows=(),
        transient=(),
    )

    root = ElementTree.fromstring(render_screen_xml(screen))
    items = list(root.findall("./groups/context/*"))
    assert [item.tag for item in items] == [role]
    assert items[0].attrib["label"] == f"{role} item"
    assert "role" not in items[0].attrib


def test_androidctld_production_does_not_import_androidctl() -> None:
    src_root = Path(__file__).resolve().parents[2] / "src" / "androidctld"
    import_re = re.compile(r"^\s*(?:from|import)\s+androidctl(?:[\s.]|$)")
    offenders: list[str] = []
    for path in src_root.rglob("*.py"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if import_re.search(line):
                offenders.append(f"{path.relative_to(src_root)}:{lineno}:{line}")

    assert offenders == []


def _assert_elements_equal(
    left: ElementTree.Element,
    right: ElementTree.Element,
) -> None:
    assert left.tag == right.tag
    assert left.attrib == right.attrib
    assert (left.text or "").strip() == (right.text or "").strip()
    left_children = list(left)
    right_children = list(right)
    assert [child.tag for child in left_children] == [
        child.tag for child in right_children
    ]
    assert len(left_children) == len(right_children)
    for left_child, right_child in zip(left_children, right_children, strict=True):
        _assert_elements_equal(left_child, right_child)
