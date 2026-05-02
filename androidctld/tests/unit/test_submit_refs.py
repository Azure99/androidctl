from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

from androidctld.refs.service import RefRegistryBuilder
from androidctld.semantics.compiler import (
    CompiledScreen,
    SemanticCompiler,
    SemanticNode,
)
from androidctld.semantics.models import SemanticMeta
from androidctld.semantics.submit_refs import submit_relation_token
from androidctld.semantics.surface import (
    build_action_surface_fingerprint,
    semantic_relation_key,
    stable_screen_id,
)
from androidctld.snapshots.models import RawIme, RawNode

from .support.semantic_screen import make_contract_snapshot, make_raw_node


def _public_payload_for(*nodes, windowless: bool = True):
    snapshot = make_contract_snapshot(*nodes, windowless=windowless)
    compiled = SemanticCompiler().compile(1, snapshot)
    finalized = RefRegistryBuilder().finalize_compiled_screen(
        compiled_screen=compiled,
        snapshot_id=snapshot.snapshot_id,
        previous_registry=None,
    )
    return (
        finalized.compiled_screen.to_public_screen().model_dump(
            by_alias=True,
            mode="json",
        ),
        finalized.compiled_screen,
    )


def _scoped_payload_for(
    *children: RawNode,
    scope_rid: str = "form",
    scope_resource_id: str | None = "com.example:id/search_form",
    scope_class_name: str = "android.widget.LinearLayout",
    scope_bounds: tuple[int, int, int, int] = (0, 0, 800, 400),
    window_id: str = "w1",
    package_name: str | None = "com.android.settings",
):
    scoped_children = tuple(
        replace(
            child,
            parent_rid=scope_rid,
            window_id=window_id,
            package_name=package_name,
        )
        for child in children
    )
    root = make_raw_node(
        rid="root",
        window_id=window_id,
        class_name="com.android.internal.policy.DecorView",
        text=None,
        editable=False,
        focusable=False,
        visible_to_user=False,
        important_for_accessibility=False,
        actions=(),
        child_rids=(scope_rid,),
        bounds=(0, 0, 1080, 2400),
        package_name=package_name,
    )
    scope = make_raw_node(
        rid=scope_rid,
        window_id=window_id,
        parent_rid="root",
        child_rids=tuple(child.rid for child in scoped_children),
        class_name=scope_class_name,
        resource_id=scope_resource_id,
        text=None,
        editable=False,
        focusable=False,
        important_for_accessibility=False,
        actions=(),
        bounds=scope_bounds,
        package_name=package_name,
    )
    return _public_payload_for(root, scope, *scoped_children, windowless=False)


def _input_node(**overrides):
    defaults = {
        "rid": "input",
        "text": None,
        "hint_text": "Search settings",
        "focused": True,
        "actions": ("focus", "setText"),
        "bounds": (0, 0, 400, 80),
    }
    defaults.update(overrides)
    return make_raw_node(**defaults)


def _button_node(**overrides):
    defaults = {
        "rid": "submit",
        "class_name": "android.widget.Button",
        "text": "Search",
        "editable": False,
        "focusable": False,
        "actions": ("click",),
        "bounds": (420, 0, 560, 80),
    }
    defaults.update(overrides)
    return make_raw_node(**defaults)


def _target_nodes(payload: dict[str, object]) -> list[dict[str, object]]:
    return _group_nodes(payload, "targets")


def _group_nodes(
    payload: dict[str, object],
    group_name: str,
) -> list[dict[str, object]]:
    groups = payload["groups"]
    assert isinstance(groups, list)
    group = next(group for group in groups if group["name"] == group_name)
    return group["nodes"]


def test_single_input_and_submit_like_button_emit_input_submit_refs() -> None:
    payload, _ = _scoped_payload_for(_input_node(), _button_node())
    nodes = _target_nodes(payload)
    input_node = next(node for node in nodes if node["role"] == "input")
    button_node = next(node for node in nodes if node["role"] == "button")

    assert input_node["submitRefs"] == [button_node["ref"]]
    assert "submitsInputRefs" not in button_node
    assert "submitRefs" not in button_node


def test_submit_refs_emit_only_for_unique_focused_input_with_multiple_inputs() -> None:
    payload, _ = _scoped_payload_for(
        _input_node(
            rid="input-a",
            hint_text="Focused query",
            focused=True,
            bounds=(0, 0, 400, 80),
        ),
        _input_node(
            rid="input-b",
            hint_text="Other query",
            focused=False,
            bounds=(0, 90, 400, 170),
        ),
        _button_node(),
    )
    nodes = _target_nodes(payload)
    focused_input = next(node for node in nodes if node["label"] == "Focused query")
    other_input = next(node for node in nodes if node["label"] == "Other query")
    button_node = next(node for node in nodes if node["role"] == "button")

    assert focused_input["submitRefs"] == [button_node["ref"]]
    assert "submitRefs" not in other_input


def test_submit_refs_fail_closed_without_focused_input() -> None:
    payload, _ = _scoped_payload_for(
        _input_node(rid="input-a", focused=False, bounds=(0, 0, 400, 80)),
        _button_node(),
    )

    assert all("submitRefs" not in node for node in _target_nodes(payload))


def test_submit_refs_fail_closed_for_multiple_focused_inputs() -> None:
    payload, _ = _scoped_payload_for(
        _input_node(rid="input-a", focused=True, bounds=(0, 0, 400, 80)),
        _input_node(rid="input-b", focused=True, bounds=(0, 90, 400, 170)),
        _button_node(),
    )

    assert all("submitRefs" not in node for node in _target_nodes(payload))


def test_submit_refs_fail_closed_for_multiple_submit_controls() -> None:
    payload, _ = _scoped_payload_for(
        _input_node(),
        _button_node(rid="submit-a", text="Search", bounds=(420, 0, 560, 80)),
        _button_node(rid="submit-b", text="Send", bounds=(520, 0, 660, 80)),
    )

    assert all("submitRefs" not in node for node in _target_nodes(payload))


def test_submit_refs_geometry_filters_invalid_extra_submit_control() -> None:
    payload, _ = _scoped_payload_for(
        _input_node(),
        _button_node(rid="valid-submit", text="Search", bounds=(420, 0, 560, 80)),
        _button_node(rid="far-submit", text="Send", bounds=(900, 900, 1040, 980)),
        scope_bounds=(0, 0, 1080, 1200),
    )
    nodes = _target_nodes(payload)
    input_node = next(node for node in nodes if node["role"] == "input")
    valid_button = next(
        node
        for node in nodes
        if node["role"] == "button" and node["bounds"] == [420, 0, 560, 80]
    )

    assert input_node["submitRefs"] == [valid_button["ref"]]


def test_submit_refs_fail_closed_for_low_confidence_resource_only_label() -> None:
    payload, _ = _scoped_payload_for(
        _input_node(),
        _button_node(text=None, resource_id="com.example:id/search"),
    )

    assert all("submitRefs" not in node for node in _target_nodes(payload))


def test_submit_refs_fail_closed_for_blocking_group_mismatch() -> None:
    payload, compiled = _public_payload_for(
        _input_node(),
        _button_node(
            rid="system-submit",
            package_name="com.android.systemui",
            text="Search",
        ),
    )

    assert compiled.blocking_group == "system"
    assert all("submitRefs" not in node for node in _target_nodes(payload))


def test_submit_refs_emit_within_same_blocking_group() -> None:
    payload, compiled = _scoped_payload_for(
        _input_node(
            package_name="com.android.systemui",
            window_id="sys",
            rid="system-input",
        ),
        _button_node(
            package_name="com.android.systemui",
            window_id="sys",
            rid="system-submit",
            text="Search",
        ),
        window_id="sys",
        package_name="com.android.systemui",
    )
    system_nodes = _group_nodes(payload, "system")
    input_node = next(node for node in system_nodes if node["role"] == "input")
    button_node = next(node for node in system_nodes if node["role"] == "button")

    assert compiled.blocking_group == "system"
    assert input_node["submitRefs"] == [button_node["ref"]]


def test_submit_refs_fail_closed_for_disabled_or_actionless_controls() -> None:
    disabled_payload, _ = _scoped_payload_for(
        _input_node(),
        _button_node(enabled=False),
    )
    actionless_payload, _ = _scoped_payload_for(
        _input_node(),
        _button_node(actions=()),
    )

    assert all("submitRefs" not in node for node in _target_nodes(disabled_payload))
    assert all("submitRefs" not in node for node in _target_nodes(actionless_payload))


def test_submit_refs_create_page_relation_not_toolbar_relation() -> None:
    root = make_raw_node(
        rid="root",
        class_name="com.android.internal.policy.DecorView",
        text=None,
        editable=False,
        focusable=False,
        visible_to_user=False,
        important_for_accessibility=False,
        actions=(),
        child_rids=("toolbar", "page-form"),
        bounds=(0, 0, 1080, 2400),
    )
    toolbar = make_raw_node(
        rid="toolbar",
        parent_rid="root",
        child_rids=("url-input", "toolbar-search"),
        class_name="android.widget.LinearLayout",
        resource_id="com.android.chrome:id/toolbar",
        text=None,
        editable=False,
        focusable=False,
        important_for_accessibility=False,
        actions=(),
        bounds=(0, 0, 1080, 120),
    )
    page_form = make_raw_node(
        rid="page-form",
        parent_rid="root",
        child_rids=("page-input", "page-search"),
        class_name="android.webkit.WebView",
        resource_id="com.example:id/page_form",
        text=None,
        editable=False,
        focusable=False,
        important_for_accessibility=False,
        actions=(),
        bounds=(0, 160, 900, 600),
    )
    payload, _ = _public_payload_for(
        root,
        toolbar,
        page_form,
        replace(
            _input_node(
                rid="url-input",
                hint_text="Search or type URL",
                focused=False,
                bounds=(0, 20, 700, 100),
            ),
            parent_rid="toolbar",
        ),
        replace(
            _button_node(
                rid="toolbar-search",
                text="Search",
                bounds=(720, 20, 860, 100),
            ),
            parent_rid="toolbar",
        ),
        replace(
            _input_node(
                rid="page-input",
                hint_text="Page query",
                focused=True,
                bounds=(40, 220, 460, 300),
            ),
            parent_rid="page-form",
        ),
        replace(
            _button_node(
                rid="page-search",
                text="Search",
                bounds=(480, 220, 620, 300),
            ),
            parent_rid="page-form",
        ),
        windowless=False,
    )
    nodes = _target_nodes(payload)
    page_input = next(node for node in nodes if node["label"] == "Page query")
    page_button = next(
        node
        for node in nodes
        if node["role"] == "button" and node["bounds"] == [480, 220, 620, 300]
    )

    assert page_input["submitRefs"] == [page_button["ref"]]


def test_submit_refs_fail_closed_for_weak_window_root_scope() -> None:
    root = make_raw_node(
        rid="root",
        class_name="android.widget.FrameLayout",
        resource_id="android:id/content",
        text=None,
        editable=False,
        focusable=False,
        important_for_accessibility=False,
        actions=(),
        child_rids=("input", "submit"),
        bounds=(0, 0, 1080, 2400),
    )
    payload, _ = _public_payload_for(
        root,
        replace(_input_node(), parent_rid="root"),
        replace(_button_node(), parent_rid="root"),
        windowless=False,
    )

    assert all("submitRefs" not in node for node in _target_nodes(payload))


def test_submit_refs_fail_closed_when_candidate_is_far_away() -> None:
    payload, _ = _scoped_payload_for(
        _input_node(bounds=(0, 0, 400, 80)),
        _button_node(bounds=(900, 900, 1040, 980)),
        scope_bounds=(0, 0, 1080, 1200),
    )

    assert all("submitRefs" not in node for node in _target_nodes(payload))


def test_submit_refs_fail_closed_when_candidate_center_is_outside_scope() -> None:
    payload, _ = _scoped_payload_for(
        _input_node(bounds=(0, 0, 400, 80)),
        _button_node(bounds=(420, 0, 560, 80)),
        scope_bounds=(0, 0, 480, 400),
    )

    assert all("submitRefs" not in node for node in _target_nodes(payload))


def test_submit_refs_do_not_cross_attribute_repeated_scopes() -> None:
    root = make_raw_node(
        rid="root",
        class_name="com.android.internal.policy.DecorView",
        text=None,
        editable=False,
        focusable=False,
        visible_to_user=False,
        important_for_accessibility=False,
        actions=(),
        child_rids=("card-a", "card-b"),
        bounds=(0, 0, 1080, 2400),
    )
    card_a = make_raw_node(
        rid="card-a",
        parent_rid="root",
        child_rids=("input-a", "submit-a"),
        class_name="android.widget.LinearLayout",
        resource_id="com.example:id/search_card",
        text=None,
        editable=False,
        focusable=False,
        important_for_accessibility=False,
        actions=(),
        bounds=(0, 0, 800, 200),
    )
    card_b = replace(
        card_a,
        rid="card-b",
        child_rids=("input-b", "submit-b"),
        bounds=(0, 260, 800, 460),
    )
    payload, _ = _public_payload_for(
        root,
        card_a,
        card_b,
        replace(
            _input_node(rid="input-a", focused=True, bounds=(0, 0, 400, 80)),
            parent_rid="card-a",
        ),
        replace(
            _button_node(rid="submit-a", bounds=(420, 0, 560, 80)),
            parent_rid="card-a",
        ),
        replace(
            _input_node(rid="input-b", focused=False, bounds=(0, 260, 400, 340)),
            parent_rid="card-b",
        ),
        replace(
            _button_node(rid="submit-b", bounds=(420, 260, 560, 340)),
            parent_rid="card-b",
        ),
        windowless=False,
    )
    nodes = _target_nodes(payload)
    focused_input = next(
        node for node in nodes if node["role"] == "input" and "focused" in node["state"]
    )
    first_button = next(
        node
        for node in nodes
        if node["role"] == "button" and node["bounds"] == [420, 0, 560, 80]
    )

    assert focused_input["submitRefs"] == [first_button["ref"]]


def test_submit_refs_use_dispatch_anchor_for_promoted_action_targets() -> None:
    root = make_raw_node(
        rid="root",
        class_name="com.android.internal.policy.DecorView",
        text=None,
        editable=False,
        focusable=False,
        visible_to_user=False,
        important_for_accessibility=False,
        actions=(),
        child_rids=("form",),
        bounds=(0, 0, 1080, 2400),
    )
    form = make_raw_node(
        rid="form",
        parent_rid="root",
        child_rids=("input", "search-action"),
        class_name="android.widget.LinearLayout",
        resource_id="com.example:id/search_form",
        text=None,
        editable=False,
        focusable=False,
        important_for_accessibility=False,
        actions=(),
        bounds=(0, 0, 800, 400),
    )
    action_container = make_raw_node(
        rid="search-action",
        parent_rid="form",
        child_rids=("search-label",),
        class_name="android.widget.Button",
        text=None,
        editable=False,
        focusable=False,
        actions=("click",),
        bounds=(420, 0, 560, 80),
    )
    label_child = make_raw_node(
        rid="search-label",
        parent_rid="search-action",
        class_name="android.widget.TextView",
        text="Search",
        editable=False,
        focusable=False,
        actions=(),
        bounds=(440, 20, 540, 60),
    )

    payload, compiled = _public_payload_for(
        root,
        form,
        replace(_input_node(), parent_rid="form"),
        action_container,
        label_child,
        windowless=False,
    )
    promoted_target = next(node for node in compiled.targets if node.label == "Search")
    input_node = next(
        node for node in _target_nodes(payload) if node["role"] == "input"
    )
    button_node = next(
        node for node in _target_nodes(payload) if node["role"] == "button"
    )

    assert promoted_target.raw_rid == "search-action"
    assert promoted_target.relation_anchor_rid == "search-action"
    assert promoted_target.relation_parent_rid == "form"
    assert input_node["submitRefs"] == [button_node["ref"]]


def test_submit_refs_do_not_cross_group_to_keyboard_keys() -> None:
    snapshot = make_contract_snapshot(
        make_raw_node(
            rid="root",
            class_name="com.android.internal.policy.DecorView",
            text=None,
            editable=False,
            focusable=False,
            visible_to_user=False,
            important_for_accessibility=False,
            actions=(),
            child_rids=("form",),
            bounds=(0, 0, 1080, 1600),
        ),
        make_raw_node(
            rid="form",
            parent_rid="root",
            child_rids=("input",),
            class_name="android.widget.LinearLayout",
            resource_id="com.example:id/search_form",
            text=None,
            editable=False,
            focusable=False,
            important_for_accessibility=False,
            actions=(),
            bounds=(0, 0, 800, 400),
        ),
        replace(_input_node(), parent_rid="form"),
        make_raw_node(
            rid="keyboard-root",
            window_id="ime",
            class_name="android.inputmethodservice.KeyboardView",
            resource_id="com.example:id/keyboard",
            text=None,
            editable=False,
            focusable=False,
            important_for_accessibility=False,
            actions=(),
            child_rids=("keyboard-search",),
            bounds=(0, 400, 1080, 900),
        ),
        make_raw_node(
            rid="keyboard-search",
            window_id="ime",
            parent_rid="keyboard-root",
            class_name="android.inputmethodservice.Keyboard$Key",
            text="Search",
            editable=False,
            focusable=False,
            actions=("click",),
            bounds=(800, 760, 1040, 880),
        ),
        windowless=False,
    )
    snapshot = replace(snapshot, ime=RawIme(visible=True, window_id="ime"))
    compiled = SemanticCompiler().compile(1, snapshot)
    finalized = RefRegistryBuilder().finalize_compiled_screen(
        compiled_screen=compiled,
        snapshot_id=snapshot.snapshot_id,
        previous_registry=None,
    )
    payload = finalized.compiled_screen.to_public_screen().model_dump(
        by_alias=True,
        mode="json",
    )
    target_input = next(
        node for node in _target_nodes(payload) if node["role"] == "input"
    )
    keyboard_nodes = _group_nodes(payload, "keyboard")

    assert any(node["role"] == "keyboard-key" for node in keyboard_nodes)
    assert "submitRefs" not in target_input


def test_submit_refs_projection_omits_unresolved_target_public_ref() -> None:
    payload, compiled = _scoped_payload_for(_input_node(), _button_node())
    assert any("submitRefs" in node for node in _target_nodes(payload))
    target = next(node for node in compiled.targets if node.role == "button")
    target.ref = ""

    unresolved_payload = compiled.to_public_screen().model_dump(
        by_alias=True,
        mode="json",
    )

    assert all("submitRefs" not in node for node in _target_nodes(unresolved_payload))


def test_submit_refs_projection_uses_semantic_key_when_raw_rids_collide() -> None:
    source = _semantic_node(raw_rid="input", role="input", label="Query", ref="n1")
    first_target = _semantic_node(
        raw_rid="reused",
        role="button",
        label="Search",
        ref="n2",
    )
    second_target = _semantic_node(
        raw_rid="reused",
        role="button",
        label="Send",
        ref="n3",
    )
    source.submit_target_keys = [semantic_relation_key("targets", second_target)]
    compiled = _compiled_screen_for(source, first_target, second_target)

    payload = compiled.to_public_screen().model_dump(
        by_alias=True,
        mode="json",
    )

    input_node = next(
        node for node in _target_nodes(payload) if node["role"] == "input"
    )
    assert input_node["submitRefs"] == ["n3"]


def test_submit_ref_relation_changes_fingerprint_and_screen_id() -> None:
    root = make_raw_node(
        rid="root",
        class_name="com.android.internal.policy.DecorView",
        text=None,
        editable=False,
        focusable=False,
        visible_to_user=False,
        important_for_accessibility=False,
        actions=(),
        child_rids=("form",),
        bounds=(0, 0, 1080, 2400),
    )
    form = make_raw_node(
        rid="form",
        parent_rid="root",
        child_rids=("input", "submit"),
        class_name="android.widget.LinearLayout",
        resource_id="com.example:id/search_form",
        text=None,
        editable=False,
        focusable=False,
        important_for_accessibility=False,
        actions=(),
        bounds=(0, 0, 800, 400),
    )
    snapshot = make_contract_snapshot(
        root,
        form,
        replace(_input_node(), parent_rid="form"),
        replace(_button_node(), parent_rid="form"),
        windowless=False,
    )
    compiled = SemanticCompiler().compile(1, snapshot)
    without_relation = deepcopy(compiled)
    for node in without_relation.targets:
        node.submit_target_keys.clear()
        node.submit_relation_tokens.clear()

    without_relation_fingerprint = build_action_surface_fingerprint(
        snapshot=snapshot,
        grouped_nodes={
            "targets": without_relation.targets,
            "keyboard": without_relation.keyboard,
            "system": without_relation.system,
            "context": without_relation.context,
            "dialog": without_relation.dialog,
        },
        blocking_group=without_relation.blocking_group,
    )

    assert compiled.action_surface_fingerprint != without_relation_fingerprint
    assert compiled.screen_id != stable_screen_id(without_relation_fingerprint)


def test_submit_ref_fingerprint_ignores_raw_scope_rid_churn() -> None:
    def compiled_for_scope(scope_rid: str) -> CompiledScreen:
        root = make_raw_node(
            rid=f"{scope_rid}-root",
            class_name="com.android.internal.policy.DecorView",
            text=None,
            editable=False,
            focusable=False,
            visible_to_user=False,
            important_for_accessibility=False,
            actions=(),
            child_rids=(scope_rid,),
            bounds=(0, 0, 1080, 2400),
        )
        form = make_raw_node(
            rid=scope_rid,
            parent_rid=root.rid,
            child_rids=("input", "submit"),
            class_name="android.widget.LinearLayout",
            resource_id="com.example:id/search_form",
            text=None,
            editable=False,
            focusable=False,
            important_for_accessibility=False,
            actions=(),
            bounds=(0, 0, 800, 400),
        )
        snapshot = make_contract_snapshot(
            root,
            form,
            replace(_input_node(), parent_rid=scope_rid),
            replace(_button_node(), parent_rid=scope_rid),
            windowless=False,
        )
        return SemanticCompiler().compile(1, snapshot)

    first = compiled_for_scope("form-a")
    second = compiled_for_scope("form-b")

    assert first.action_surface_fingerprint == second.action_surface_fingerprint
    assert first.screen_id == second.screen_id


def test_submit_ref_relation_retargeting_changes_fingerprint() -> None:
    source = _semantic_node(raw_rid="input", role="input", label="Search")
    first_target = _semantic_node(raw_rid="search", role="button", label="Search")
    second_target = _semantic_node(raw_rid="send", role="button", label="Send")
    grouped_nodes = {
        "targets": [source, first_target, second_target],
        "keyboard": [],
        "system": [],
        "context": [],
        "dialog": [],
    }

    source.submit_relation_tokens = [
        submit_relation_token("targets", source, first_target)
    ]
    first_fingerprint = build_action_surface_fingerprint(
        snapshot=make_contract_snapshot(windowless=True),
        grouped_nodes=grouped_nodes,
        blocking_group=None,
    )
    source.submit_relation_tokens = [
        submit_relation_token("targets", source, second_target)
    ]
    second_fingerprint = build_action_surface_fingerprint(
        snapshot=make_contract_snapshot(windowless=True),
        grouped_nodes=grouped_nodes,
        blocking_group=None,
    )

    assert first_fingerprint != second_fingerprint


def _semantic_node(
    *,
    raw_rid: str,
    role: str,
    label: str,
    ref: str = "",
) -> SemanticNode:
    return SemanticNode(
        raw_rid=raw_rid,
        role=role,
        label=label,
        state=["focused"] if role == "input" else [],
        actions=["type"] if role == "input" else ["tap"],
        bounds=(0, 0, 100, 40),
        meta=SemanticMeta(
            resource_id=f"android:id/{raw_rid}",
            class_name=(
                "android.widget.EditText"
                if role == "input"
                else "android.widget.Button"
            ),
        ),
        targetable=True,
        score=100,
        group="targets",
        parent_role="container",
        parent_label="Root",
        sibling_labels=[],
        relative_bounds=(0, 0, 100, 40),
        label_quality=6,
        ref=ref,
    )


def _compiled_screen_for(*targets: SemanticNode) -> CompiledScreen:
    fingerprint = build_action_surface_fingerprint(
        snapshot=make_contract_snapshot(windowless=True),
        grouped_nodes={
            "targets": list(targets),
            "keyboard": [],
            "system": [],
            "context": [],
            "dialog": [],
        },
        blocking_group=None,
    )
    return CompiledScreen(
        screen_id=stable_screen_id(fingerprint),
        sequence=1,
        source_snapshot_id=42,
        captured_at="2026-04-08T00:00:00Z",
        package_name="com.android.settings",
        activity_name="SettingsActivity",
        keyboard_visible=False,
        action_surface_fingerprint=fingerprint,
        targets=list(targets),
        context=[],
        dialog=[],
        keyboard=[],
        system=[],
    )
