import json
import xml.etree.ElementTree as ET

import pytest

from androidctl import ui_view
from ui_test_support import node, tree


def test_xml_keeps_body_context_raw_states_and_readable_entities():
    tree = {
        "scope": {"window": 12, "package": "example.shop"},
        "windows": [{"id": 12, "title": "Cart"}],
        "keyboard": "shown",
        "nodes": [
            node("e15"),
            node("e16", "text", "Order <19 > 2", clickable=False),
            node("e17", "checkbox", "Options", checkable=True, checked="mixed"),
            node(
                "e18",
                "textbox",
                "Password",
                editable=True,
                password=True,
                text="exposed",
                focused=True,
            ),
        ],
    }
    rendered = ui_view.render(ui_view.screen(tree, ui_view.project(tree)))
    screen = ET.fromstring(rendered)
    assert screen.attrib == {
        "app": "example.shop",
        "title": "Cart",
        "keyboard": "shown",
    }
    assert screen.find("button").attrib == {"ref": "e15", "name": "Pay & finish"}
    assert screen.find("text").attrib == {"name": "Order <19 > 2"}
    assert screen.find("checkbox").attrib["checked"] == "mixed"
    assert screen.find("textbox").attrib["value"] == "exposed"
    assert "&lt;19 >" in rendered
    assert len(ui_view.find(ui_view.project(tree), "Pay &amp; finish")) == 1


@pytest.mark.parametrize("checked", ["97", None])
def test_unknown_checked_value_stays_unreadable(checked):
    value = node("e1", "checkbox", "Option", checkable=True, checked=checked)
    attrs = ui_view.attributes(value, all_nodes=False, boxes=False)
    assert attrs["unreadable"] == "true"
    assert "checked" not in attrs


@pytest.mark.parametrize("model", ["binary", "triState", "other", None])
def test_checkable_model_omits_only_reported_tri_state_default(model):
    value = node(
        "e1", "checkbox", "Option", checkable=True, checked="mixed", checkedModel=model
    )
    attrs = ui_view.attributes(value, all_nodes=False, boxes=False)
    assert attrs.get("checkedModel") == (None if model == "triState" else model)
    assert attrs["checked"] == "mixed"


def test_deep_xml_limits_whitespace_without_removing_facts():
    root = ET.Element("screen", {"unreadable": "true"})
    current = root
    for index in range(40):
        current = ET.SubElement(
            current, "item", {"name": f"row {index}", "ref-unavailable": "incomplete"}
        )
    rendered = ui_view.render(root)
    decoded = ET.fromstring(rendered)
    assert [(element.tag, element.attrib) for element in decoded.iter()] == [
        (element.tag, element.attrib) for element in root.iter()
    ]
    assert max(len(line) - len(line.lstrip()) for line in rendered.splitlines()) == 16


def test_projection_keeps_named_target_when_only_details_are_incomplete():
    target = node("e1")
    target["view"]["detailComplete"] = False
    attrs = ui_view.attributes(target, all_nodes=False, boxes=False)
    assert attrs == {"ref": "e1", "name": "Pay & finish", "unreadable": "true"}
    target["refAvailable"] = False
    target["refReason"] = "Selection identity is not unique"
    attrs = ui_view.attributes(target, all_nodes=True, boxes=False)
    assert "ref" not in attrs
    assert attrs["ref-unavailable"] == "Selection identity is not unique"
    assert attrs["name"] == "Pay & finish"


@pytest.mark.parametrize("clickable", [False, True])
def test_named_platform_actions_expose_targets_even_when_clickable(clickable):
    target = node(
        "e1",
        "text",
        "",
        clickable=clickable,
        enabled=False,
        resourceId="example:id/panel",
        actions=["expand", "dismiss", "action-2000001"],
    )
    attrs = ui_view.attributes(target, all_nodes=False, boxes=False)
    assert attrs == {
        "ref": "e1",
        "actions": "expand dismiss",
        "id": "panel",
        "disabled": "true",
    }
    target.update(refAvailable=False, refReason="incomplete")
    attrs = ui_view.attributes(target, all_nodes=False, boxes=False)
    assert attrs["ref-unavailable"] == "incomplete"
    assert "ref" not in attrs
    target.update(clickable=False, actions=["action-2000001"])
    assert ui_view.attributes(target, all_nodes=False, boxes=False) == {}


@pytest.mark.parametrize(
    ("editable", "clickable", "expected"),
    [
        (False, False, "long-click collapse"),
        (False, True, "collapse"),
        (True, False, "collapse long-click fill"),
    ],
)
def test_long_click_discovery_keeps_its_narrow_rule(editable, clickable, expected):
    target = node(
        "e1",
        editable=editable,
        clickable=clickable,
        longClickable=True,
        actions=["collapse", "long-click", "fill"],
    )
    attrs = ui_view.attributes(target, all_nodes=False, boxes=False)
    assert attrs["actions"] == expected


@pytest.mark.parametrize("focused", [False, True])
@pytest.mark.parametrize("actions", [["focus", "fill", "ime"], [], None])
def test_editable_actions_show_current_facts_without_inventing_capability(
    focused, actions
):
    source = node("e1", "textbox", "Search", editable=True, focused=focused)
    source["actions"] = actions
    attrs = ui_view.attributes(source, all_nodes=False, boxes=False)
    if actions is None:
        assert "actions" not in attrs
    else:
        assert attrs["actions"] == " ".join(actions)
    del source["actions"]
    source["unavailable"] = [{"field": "actions", "reason": "unavailable"}]
    attrs = ui_view.attributes(source, all_nodes=False, boxes=False)
    assert "actions" not in attrs
    assert attrs["unreadable"] == "true"


def test_read_only_matching_keeps_original_values_and_scroll_facts():
    source = node(
        "e1",
        "checkbox",
        "\uff26\uff2f\uff2f\u00a0-\tStra\u00dfe",
        checkable=True,
        checked="false",
        checkedModel="binary",
        stateDescription="Partly selected",
        actions=["scroll-left", "scroll-down", "page-left", "page-up"],
    )
    elements = ui_view.project({"nodes": [source]})
    assert ui_view.find(elements, "foo strasse") == elements
    assert elements[0].attrib["name"] == source["view"]["name"]
    assert elements[0].attrib["checked"] == "false"
    assert elements[0].attrib["checkedModel"] == "binary"
    assert elements[0].attrib["stateDescription"] == "Partly selected"
    assert "detail" not in elements[0].attrib
    assert ui_view.find(elements, "partly selected") == elements
    assert elements[0].attrib["scroll"] == "down left page-up page-left"


def test_noncheckable_state_description_remains_searchable_detail():
    source = node("e1", "text", "Progress", stateDescription="Almost complete")
    elements = ui_view.project({"nodes": [source]})
    assert elements[0].attrib["detail"] == "Almost complete"
    assert ui_view.find(elements, "almost complete") == elements


def test_render_keeps_visible_children_and_targets_after_long_attributes():
    tree = {
        "scope": {"window": 1},
        "windows": [{"id": 1}],
        "keyboard": "hidden",
        "nodes": [
            node("e1", name="Pay", visible=False),
            node("e2", name="Pay", parent="e1"),
            node("e3", name="Long & < " * 2000),
            node("e4", name="Pay", parent="e3"),
            node("e5", name="Pay"),
        ],
    }
    text = ui_view.render(ui_view.screen(tree, ui_view.project(tree)))
    root = ET.fromstring(text)
    assert {item.get("ref") for item in root.iter("button")} == {"e2", "e3", "e4", "e5"}
    assert (
        ET.fromstring(text).find("./button[@ref='e3']").get("name")
        == "Long & < " * 2000
    )


def test_platform_details_survive_absorption_without_retaining_empty_layouts():
    data = tree()
    item = data["nodes"][0]
    item.update(
        error="Address is incomplete",
        contentInvalid=True,
        rangeInfo={"type": 0, "min": 0, "max": 100, "current": 35},
        collectionItemInfo={"rowIndex": 1, "rowSpan": 1, "columnIndex": -1},
    )
    item["view"]["nameConsumed"] = True
    item["unavailable"] = [{"field": "error", "reason": "truncated"}]
    element = ui_view.project(data)[0]
    assert element.attrib["error"] == "Address is incomplete"
    assert element.attrib["contentInvalid"] == "true"
    assert element.attrib["rangeCurrent"] == "35"
    assert element.attrib["rangeMax"] == "100"
    assert element.attrib["rowIndex"] == "1"
    assert element.attrib["columnIndex"] == "-1"
    assert "rowSpan" not in element.attrib
    assert element.attrib["unreadable"] == "true"
    assert element.attrib == ET.fromstring(ui_view.render(element)).attrib
    item.update(
        error=None, contentInvalid=False, rangeInfo=None, collectionItemInfo=None
    )
    item["unavailable"] = []
    attrs = ui_view.attributes(item, all_nodes=False, boxes=False)
    assert "error" not in attrs
    assert "contentInvalid" not in attrs


@pytest.mark.parametrize(
    ("collection", "item", "role", "expected"),
    [
        (
            {"rows": -1, "columns": 1, "selectionMode": 0},
            {"rowIndex": 0, "rowSpan": 1, "columnIndex": -1, "columnSpan": 1},
            "textbox",
            {"rows": "-1", "columns": "1", "rowIndex": "0", "columnIndex": "-1"},
        ),
        (
            {"rows": 0, "columns": 0, "selectionMode": 2},
            {"rowIndex": -2, "rowSpan": 0, "columnIndex": 0, "columnSpan": -3},
            "button",
            {
                "rows": "0",
                "columns": "0",
                "selectionMode": "2",
                "rowIndex": "-2",
                "rowSpan": "0",
                "columnIndex": "0",
                "columnSpan": "-3",
            },
        ),
        (
            None,
            {"rowIndex": 2, "rowSpan": 3, "columnSpan": 2},
            "item",
            {"rowIndex": "2", "rowSpan": "3", "columnSpan": "2"},
        ),
        (
            {"selectionMode": 0},
            {"rowSpan": 1, "columnSpan": 1},
            "text",
            {"selectionMode": "0", "rowSpan": "1", "columnSpan": "1"},
        ),
        (
            {"rows": None, "columns": None},
            {"rowIndex": None, "rowSpan": None},
            "list",
            {},
        ),
        (
            {"columns": -1},
            {"columnIndex": 0},
            "text",
            {"columns": "-1", "columnIndex": "0"},
        ),
        (None, None, "button", {}),
    ],
)
def test_collection_values_preserve_signed_facts_and_applicable_defaults(
    collection, item, role, expected
):
    value = node(
        "e1",
        role,
        "",
        clickable=False,
        collectionInfo=collection,
        collectionItemInfo=item,
    )
    elements = ui_view.project({"nodes": [value]})
    if expected:
        assert len(elements) == 1
        assert elements[0].tag == role
        assert elements[0].attrib == expected
    else:
        assert elements == []


@pytest.mark.parametrize(
    ("title", "truncated", "incomplete", "root_available"),
    [
        ("Cart", False, False, True),
        ("Cart", True, True, None),
        ("Cart\x00", False, True, None),
        (None, False, False, None),
        ("", False, False, None),
        ("Cart", False, False, False),
    ],
)
def test_window_title_and_type_are_honest_local_facts(
    title, truncated, incomplete, root_available
):
    raw = tree()
    raw["windows"][0].update(title=title, titleTruncated=truncated, type="application")
    if root_available is not None:
        raw["windows"][0]["rootAvailable"] = root_available
    raw["windows"].append(
        {"id": 13, "type": "97", "title": "Other", "packageName": "known.package"}
    )
    screen = ui_view.screen(raw, ui_view.project(raw))
    decoded = ET.fromstring(ui_view.render(screen))
    main = decoded.find("window[@id='12']")
    other = decoded.find("window[@id='13']")
    assert (decoded.get("titleIncomplete") == "true") is incomplete
    assert (main.get("titleIncomplete") == "true") is incomplete
    assert main.get("type") == "application"
    assert other.get("type") == "97"
    assert "packageName" not in main.attrib
    assert other.get("packageName") == "known.package"
    assert decoded.get("title") == ui_view.clean(title or "")
    assert main.get("title") == ("" if title == "" else None)
    assert main.get("rootAvailable") == (
        None if root_available is None else str(root_available).lower()
    )
    assert not raw.get("partial")


def test_selected_title_uses_scope_and_preserves_other_window_facts():
    raw = tree()
    raw["scope"]["window"] = 13
    raw["windows"].append(
        {
            "id": 13,
            "title": "Selected",
            "active": False,
            "focused": False,
            "packageName": "example.shop",
            "rootAvailable": False,
        }
    )
    screen = ui_view.screen(raw, [])
    assert screen.get("title") == "Selected"
    assert screen.find("window[@id='12']").get("title") == "Cart"
    selected = screen.find("window[@id='13']")
    assert selected.attrib == {
        "id": "13",
        "packageName": "example.shop",
        "rootAvailable": "false",
    }
    del raw["windows"][1]["title"]
    assert "title" not in ui_view.screen(raw, []).find("window[@id='13']").attrib


@pytest.mark.parametrize(
    "value",
    [
        "汉字",
        "café",
        "😀",
        "?>",
        "\0",
        "\x01",
        "\n\r\t",
        chr(0xD800),
        chr(0xDFFF),
        "\ufffe",
        "\uffff",
    ],
)
def test_pi_is_valid_utf8_xml_and_preserves_all_invocation_characters(value):
    invocation = {"command": "click", "args": {"name": "输入" + value}}
    header = ui_view.header(invocation)
    ET.fromstring((header + "<screen />").encode("utf-8"))
    encoded = header.removeprefix("<?androidctl command=").removesuffix("?>\n")
    assert json.loads(encoded) == invocation
    assert "输入" in header
    assert ": " not in encoded
