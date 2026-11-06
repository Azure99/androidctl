import pytest

from androidctl import ui_view
from ui_test_support import node, tree


def consumed(ref, name, parent):
    value = node(ref, "text", name, clickable=False, actions=[], parent=parent)
    value["view"]["nameConsumed"] = True
    return value


def test_local_subtree_restores_names_consumed_outside_it():
    value = tree()
    value["nodes"] = [
        node("a", name="Account"),
        node("b", "text", "", clickable=False, actions=[], parent="a"),
        consumed("c", "Account", "b"),
    ]
    projected = ui_view.project(value, ref="b")
    assert [e.get("name") for root in projected for e in root.iter()] == ["Account"]


@pytest.mark.parametrize(
    ("name", "detail", "children"),
    [
        ("A | B", "A | B", ["A | B", "A", "B"]),
        ("Account", "Alpha | Beta", ["Account", "Alpha | Beta"]),
    ],
)
def test_action_subtree_keeps_aggregated_names_and_independent_action(
    name, detail, children
):
    value = tree()
    action = node("a", name=name)
    action["view"]["detail"] = detail
    other = node(
        "other", "text", name, clickable=False, actions=["dismiss"], parent="a"
    )
    value["nodes"] = [
        action,
        *(consumed(str(i), text, "a") for i, text in enumerate(children)),
        other,
    ]
    whole = ui_view.project(value)
    local = ui_view.project(value, ref="a")
    assert [ui_view.render(e) for e in local] == [ui_view.render(e) for e in whole]
    assert [
        (e.get("ref"), e.get("name"))
        for root in local
        for e in root.iter()
        if e.get("ref")
    ] == [
        ("a", ui_view.clean(name)),
        ("other", ui_view.clean(name)),
    ]
