from __future__ import annotations

from datetime import datetime, timedelta, timezone

from androidctld.semantics.compiler import (
    CompiledScreen,
    SemanticCompiler,
    SemanticNode,
)
from androidctld.semantics.models import SemanticMeta
from androidctld.semantics.public_models import (
    BlockingGroupName,
    PublicApp,
    PublicFocus,
    PublicNode,
    PublicScreen,
    PublicSurface,
    build_public_groups,
)
from androidctld.snapshots.models import RawIme, RawNode, RawSnapshot, RawWindow

from .runtime import build_screen_artifacts, install_screen_state

_CAPTURED_AT_BASE = datetime(2026, 4, 13, tzinfo=timezone.utc)
_CONTRACT_SCREEN_ID = "screen-00001"
_CONTRACT_SEQUENCE = 1
_CONTRACT_SNAPSHOT_ID = 42
_CONTRACT_CAPTURED_AT = "2026-04-08T00:00:00Z"
_CONTRACT_PACKAGE_NAME = "com.android.settings"
_CONTRACT_ACTIVITY_NAME = "SettingsActivity"
_DEFAULT_DISPLAY = {
    "widthPx": 1080,
    "heightPx": 2400,
    "densityDpi": 420,
    "rotation": 0,
}


def _captured_at_for(snapshot_id: int) -> str:
    return (_CAPTURED_AT_BASE + timedelta(seconds=snapshot_id)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def make_raw_node(
    *,
    rid: str = "root",
    window_id: str = "w1",
    parent_rid: str | None = None,
    child_rids: tuple[str, ...] = (),
    class_name: str = "android.widget.EditText",
    resource_id: str | None = None,
    text: str | None = "Wi-Fi",
    content_desc: str | None = None,
    hint_text: str | None = None,
    state_description: str | None = None,
    pane_title: str | None = None,
    package_name: str | None = "com.android.settings",
    bounds: tuple[int, int, int, int] = (0, 0, 100, 40),
    visible_to_user: bool = True,
    important_for_accessibility: bool = True,
    clickable: bool | None = None,
    enabled: bool = True,
    editable: bool = True,
    focusable: bool = True,
    focused: bool = False,
    checkable: bool = False,
    checked: bool = False,
    selected: bool = False,
    scrollable: bool = False,
    password: bool = False,
    actions: tuple[str, ...] = ("focus", "setText"),
) -> RawNode:
    resolved_clickable = "click" in actions if clickable is None else clickable
    return RawNode(
        rid=rid,
        window_id=window_id,
        parent_rid=parent_rid,
        child_rids=child_rids,
        class_name=class_name,
        resource_id=resource_id,
        text=text,
        content_desc=content_desc,
        hint_text=hint_text,
        state_description=state_description,
        pane_title=pane_title,
        package_name=package_name,
        bounds=bounds,
        visible_to_user=visible_to_user,
        important_for_accessibility=important_for_accessibility,
        clickable=resolved_clickable,
        enabled=enabled,
        editable=editable,
        focusable=focusable,
        focused=focused,
        checkable=checkable,
        checked=checked,
        selected=selected,
        scrollable=scrollable,
        password=password,
        actions=actions,
    )


def make_snapshot(
    *snapshot_nodes: RawNode,
    snapshot_id: int = 1,
    captured_at: str | None = None,
    package_name: str | None = "com.android.settings",
    activity_name: str = "SettingsActivity",
    label: str = "Wi-Fi",
    windows: tuple[RawWindow, ...] | None = None,
    nodes: tuple[RawNode, ...] | None = None,
    display: dict[str, int] | None = None,
) -> RawSnapshot:
    if snapshot_nodes and nodes is not None:
        raise TypeError("pass raw nodes either positionally or via nodes=, not both")
    if captured_at is None:
        captured_at = _captured_at_for(snapshot_id)
    if snapshot_nodes:
        nodes = snapshot_nodes
    if windows is None:
        first_node = None if nodes is None or not nodes else nodes[0]
        windows = (
            RawWindow(
                window_id="window-1" if first_node is None else first_node.window_id,
                type="application",
                layer=1,
                package_name=(
                    package_name if first_node is None else first_node.package_name
                ),
                bounds=(0, 0, 1080, 2400),
                root_rid="root" if first_node is None else first_node.rid,
            ),
        )
    if nodes is None:
        nodes = (
            make_raw_node(
                window_id="window-1",
                class_name="android.widget.TextView",
                resource_id="android:id/title",
                text=label,
                package_name=package_name,
                bounds=(0, 0, 400, 80),
                editable=False,
                focusable=False,
                actions=(),
            ),
        )
    return RawSnapshot(
        snapshot_id=snapshot_id,
        captured_at=captured_at,
        package_name=package_name,
        activity_name=activity_name,
        ime=RawIme(visible=False, window_id=None),
        windows=windows,
        nodes=nodes,
        display=dict(_DEFAULT_DISPLAY if display is None else display),
    )


def make_contract_snapshot(
    *snapshot_nodes: RawNode,
    snapshot_id: int = _CONTRACT_SNAPSHOT_ID,
    captured_at: str = _CONTRACT_CAPTURED_AT,
    package_name: str | None = _CONTRACT_PACKAGE_NAME,
    activity_name: str = _CONTRACT_ACTIVITY_NAME,
    windowless: bool = False,
    windows: tuple[RawWindow, ...] | None = None,
    nodes: tuple[RawNode, ...] | None = None,
    display: dict[str, int] | None = None,
) -> RawSnapshot:
    return make_snapshot(
        *snapshot_nodes,
        snapshot_id=snapshot_id,
        captured_at=captured_at,
        package_name=package_name,
        activity_name=activity_name,
        windows=() if windowless else windows,
        nodes=nodes,
        display=display,
    )


def make_public_node(
    *,
    ref: str | None = "n1",
    role: str = "button",
    label: str = "Node",
    state: tuple[str, ...] = (),
    actions: tuple[str, ...] = ("tap",),
) -> PublicNode:
    return PublicNode(
        ref=ref,
        role=role,
        label=label,
        state=state,
        actions=actions,
    )


def make_contract_screen(
    *,
    targets: tuple[PublicNode, ...] = (),
    dialog: tuple[PublicNode, ...] = (),
    input_ref: str | None = None,
    blocking_group: BlockingGroupName | None = None,
    keyboard_visible: bool = False,
    screen_id: str = _CONTRACT_SCREEN_ID,
    sequence: int = _CONTRACT_SEQUENCE,
    source_snapshot_id: int = _CONTRACT_SNAPSHOT_ID,
    captured_at: str = _CONTRACT_CAPTURED_AT,
    package_name: str = _CONTRACT_PACKAGE_NAME,
    activity_name: str = _CONTRACT_ACTIVITY_NAME,
) -> PublicScreen:
    del sequence, source_snapshot_id, captured_at
    group_order = (
        ("targets", "keyboard", "system", "context", "dialog")
        if blocking_group is None
        else (
            blocking_group,
            *(
                group_name
                for group_name in ("targets", "keyboard", "system", "context", "dialog")
                if group_name != blocking_group
            ),
        )
    )
    return PublicScreen(
        screen_id=screen_id,
        app=PublicApp(
            package_name=package_name,
            activity_name=activity_name,
        ),
        surface=PublicSurface(
            keyboard_visible=keyboard_visible,
            blocking_group=blocking_group,
            focus=PublicFocus(input_ref=input_ref),
        ),
        groups=build_public_groups(
            order=group_order,
            targets=targets,
            dialog=dialog,
        ),
        omitted=(),
        visible_windows=(),
        transient=(),
    )


def make_public_screen(
    screen_id: str,
    *,
    sequence: int = 1,
    source_snapshot_id: int = 1,
    captured_at: str = "2026-04-13T00:00:00Z",
    package_name: str = "com.android.settings",
    activity_name: str = "SettingsActivity",
    keyboard_visible: bool = False,
    refs: tuple[str, ...] = (),
    targets: tuple[PublicNode, ...] | None = None,
) -> PublicScreen:
    del sequence, source_snapshot_id, captured_at
    if targets is None:
        targets = tuple(make_public_node(ref=ref, label=f"Node {ref}") for ref in refs)
    return PublicScreen(
        screen_id=screen_id,
        app=PublicApp(
            package_name=package_name,
            activity_name=activity_name,
        ),
        surface=PublicSurface(
            keyboard_visible=keyboard_visible,
            focus=PublicFocus(),
        ),
        groups=build_public_groups(targets=targets),
        omitted=(),
        visible_windows=(),
        transient=(),
    )


def make_semantic_node(
    *,
    raw_rid: str = "screen:raw",
    ref: str = "n1",
    role: str = "button",
    label: str = "Node",
    group: str = "targets",
    parent_role: str = "container",
    parent_label: str = "Root",
    sibling_labels: list[str] | None = None,
) -> SemanticNode:
    return SemanticNode(
        raw_rid=raw_rid,
        role=role,
        label=label,
        state=[],
        actions=["tap"],
        bounds=(0, 0, 100, 20),
        meta=SemanticMeta(
            resource_id="android:id/button1",
            class_name="android.widget.Button",
        ),
        targetable=True,
        score=100,
        group=group,
        parent_role=parent_role,
        parent_label=parent_label,
        sibling_labels=[] if sibling_labels is None else sibling_labels,
        relative_bounds=(0, 0, 100, 20),
        ref=ref,
    )


def make_compiled_screen(
    screen_id: str,
    *,
    sequence: int = 1,
    source_snapshot_id: int = 1,
    captured_at: str = "2026-04-13T00:00:00Z",
    package_name: str = "com.android.settings",
    activity_name: str = "SettingsActivity",
    keyboard_visible: bool = False,
    fingerprint: str,
    targets: list[SemanticNode] | None = None,
    ref: str = "n1",
) -> CompiledScreen:
    if targets is None:
        targets = [
            make_semantic_node(
                raw_rid=f"{screen_id}:raw",
                ref=ref,
            )
        ]
    return CompiledScreen(
        screen_id=screen_id,
        sequence=sequence,
        source_snapshot_id=source_snapshot_id,
        captured_at=captured_at,
        package_name=package_name,
        activity_name=activity_name,
        keyboard_visible=keyboard_visible,
        action_surface_fingerprint=fingerprint,
        targets=targets,
        context=[],
        dialog=[],
        keyboard=[],
        system=[],
    )


def compile_screen(
    snapshot: RawSnapshot,
    *,
    sequence: int = 1,
) -> CompiledScreen:
    return SemanticCompiler().compile(sequence, snapshot)


def install_snapshot_screen(
    runtime: object,
    snapshot: RawSnapshot,
    *,
    sequence: int = 1,
    include_artifacts: bool = True,
) -> CompiledScreen:
    compiled_screen = compile_screen(snapshot, sequence=sequence)
    public_screen = compiled_screen.to_public_screen()
    artifacts = (
        build_screen_artifacts(runtime, screen_id=public_screen.screen_id)
        if include_artifacts
        else None
    )
    install_screen_state(
        runtime,
        snapshot=snapshot,
        public_screen=public_screen,
        compiled_screen=compiled_screen,
        artifacts=artifacts,
    )
    return compiled_screen
