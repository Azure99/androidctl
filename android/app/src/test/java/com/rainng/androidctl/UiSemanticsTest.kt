package com.rainng.androidctl

import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class UiSemanticsTest {
    @Test fun gapsAfterTheNameKeepItUsableButMissingParentsDoNot() {
        val button = node("button").put("clickable", true).put("childrenIncomplete", true).put("missingChildIndex", 1)
        val label = node("label", "Open", "button").put("childIndex", 0)
        UiSemantics.project(listOf(button, label))
        assertTrue(button.getJSONObject("view").getBoolean("nameComplete"))
        assertFalse(button.getJSONObject("view").getBoolean("detailComplete"))
        assertEquals("button", label.getJSONObject("view").getString("selectionTarget"))
        button.put("missingChildIndex", 0)
        UiSemantics.project(listOf(button, label))
        assertFalse(button.getJSONObject("view").getBoolean("nameComplete"))
        val orphan = node("orphan", "Target", "uncollected")
        UiSemantics.project(listOf(orphan))
        assertNull(UiSemantics.identity(orphan))
    }

    @Test fun actionAdmissionKeepsAliasesAndExcludesObservationOnlyActions() {
        val supported =
            mapOf(
                "click" to AccessibilityNodeInfo.ACTION_CLICK,
                "check" to AccessibilityNodeInfo.ACTION_CLICK,
                "uncheck" to AccessibilityNodeInfo.ACTION_CLICK,
                "fill" to AccessibilityNodeInfo.ACTION_SET_TEXT,
                "focus" to AccessibilityNodeInfo.ACTION_FOCUS,
                "long-click" to AccessibilityNodeInfo.ACTION_LONG_CLICK,
                "expand" to AccessibilityNodeInfo.ACTION_EXPAND,
                "collapse" to AccessibilityNodeInfo.ACTION_COLLAPSE,
                "dismiss" to AccessibilityNodeInfo.ACTION_DISMISS,
                "ime" to android.R.id.accessibilityActionImeEnter,
                "scroll-forward" to AccessibilityNodeInfo.ACTION_SCROLL_FORWARD,
                "scroll-backward" to AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD,
                "scroll-up" to android.R.id.accessibilityActionScrollUp,
                "scroll-down" to android.R.id.accessibilityActionScrollDown,
                "scroll-left" to android.R.id.accessibilityActionScrollLeft,
                "scroll-right" to android.R.id.accessibilityActionScrollRight,
            )
        for ((name, id) in supported) assertEquals(name, id, UiAction.parse(name).platformId)
        val pages =
            mapOf(
                android.R.id.accessibilityActionPageUp to "page-up",
                android.R.id.accessibilityActionPageDown to "page-down",
                android.R.id.accessibilityActionPageLeft to "page-left",
                android.R.id.accessibilityActionPageRight to "page-right",
            )
        for ((id, name) in pages) assertEquals(name, UiSemantics.actionName(id))
        for (name in listOf("copy", "cut", "paste", "select", "clear-focus", "set-selection", "unknown") + pages.values) {
            assertEquals("action", assertThrows(Rejected::class.java) { UiAction.parse(name).platformId }.code)
        }
    }

    @Test fun onlyNamedActionChildrenStopNameAbsorption() {
        for (action in listOf("expand", "collapse", "dismiss", "select")) {
            val parent = node("parent").put("clickable", true)
            val child = node("child", "Panel", "parent")
            val nodes = listOf(parent, child)
            UiSemantics.project(nodes)
            val key = UiSemantics.identity(parent)
            child.put("actions", JSONArray(listOf(action)))
            UiSemantics.project(nodes)
            val named = action in listOf("expand", "collapse", "dismiss")
            assertEquals(if (named) "" else "Panel", parent.getJSONObject("view").getString("name"))
            assertEquals(named, child.getJSONObject("view").isNull("selectionTarget"))
            if (named) {
                assertNotEquals(key, UiSemantics.identity(parent))
                assertTrue(action in RpcMethods.UI_COMMANDS)
                assertFalse(action in RpcMethods.UI_READ_ONLY)
                assertTrue(UiAction.parse(action).ready(child))
                child.put("actions", JSONArray())
                assertFalse(UiAction.parse(action).ready(child))
            } else {
                assertEquals(key, UiSemantics.identity(parent))
            }
        }
    }

    @Test fun namedBoundaryDoesNotExpandRootNamingOrAbsorbItsGrandchildren() {
        val parent = node("parent").put("clickable", true)
        val child = node("child", parent = "parent").put("actions", JSONArray(listOf("expand")))
        val grandchild = node("grandchild", "Detail", "child")
        UiSemantics.project(listOf(parent, child, grandchild))
        assertEquals("", parent.getJSONObject("view").getString("name"))
        assertEquals("", child.getJSONObject("view").getString("name"))
        assertTrue(grandchild.getJSONObject("view").isNull("selectionTarget"))
        child.put("parent", JSONObject.NULL)
        UiSemantics.project(listOf(child, grandchild))
        assertEquals("", child.getJSONObject("view").getString("name"))
    }

    @Test fun rowIdentityIgnoresInputsButKeepsTheAcceptedLeadingSubtotalBoundary() {
        val row = node("e1").put("collectionItemInfo", json("rowIndex" to 0, "columnIndex" to 0))
        val label = node("e2", "Alpha", "e1").put("clickable", true)
        val quantity = node("e3", "1", "e1").put("editable", true).put("hint", "Quantity")
        val subtotal = node("e4", "Subtotal 19", "e1")
        val delete = node("e5", "Delete", "e1").put("clickable", true).put("className", "android.widget.Button")
        val nodes = listOf(row, label, quantity, subtotal, delete)
        UiSemantics.project(nodes)
        val original = UiSemantics.identity(delete)
        quantity.put("text", "2")
        subtotal.put("text", "Subtotal 38")
        UiSemantics.project(nodes)
        assertEquals(original, UiSemantics.identity(delete))
        label.put("text", "Bravo")
        UiSemantics.project(nodes)
        assertNotEquals(original, UiSemantics.identity(delete))
        UiSemantics.project(listOf(row, subtotal, label, quantity, delete))
        val front = UiSemantics.identity(delete)
        subtotal.put("text", "Subtotal 57")
        UiSemantics.project(listOf(row, subtotal, label, quantity, delete))
        assertNotEquals(front, UiSemantics.identity(delete))
    }

    @Test fun emptyWebViewRequiresKnownDescendants() {
        val web = node("e1").put("className", "android.webkit.WebView")
        UiSemantics.project(listOf(web))
        assertTrue(web.getJSONObject("view").getBoolean("webEmpty"))
        web.put("childrenIncomplete", true)
        UiSemantics.project(listOf(web))
        assertFalse(web.getJSONObject("view").getBoolean("webEmpty"))
    }

    @Test fun deepRowLabelsKeepTheSixtyFourNodeLimit() {
        for (count in listOf(10, 64, 65)) {
            val nodes = (0 until count).map { node("n$it", parent = if (it == 0) null else "n${it - 1}") }
            nodes.first().put("collectionItemInfo", json("rowIndex" to 0, "columnIndex" to 0))
            nodes.last().put("text", "Record A")
            val target = node("button", "Run", "n0").put("className", "android.widget.Button").put("clickable", true)
            UiSemantics.project(nodes + target)
            assertEquals(count <= 64, UiSemantics.identity(target) != null)
            if (count <= 64) assertEquals("Record A", target.getJSONObject("row").getString("label"))
        }
    }

    @Test fun deepRowLabelsStillRespectMissingAndExcludedEvidence() {
        val row = node("row").put("collectionItemInfo", json("rowIndex" to 0, "columnIndex" to 0))
        val chain = (1..10).map { node("n$it", parent = if (it == 1) "row" else "n${it - 1}") }
        chain.last().put("text", "Record A")
        val target = node("button", "Run", "row").put("className", "android.widget.Button").put("clickable", true)
        val nodes = listOf(row) + chain + target
        chain[8].put("unavailable", JSONArray().put(json("field" to "text")))
        UiSemantics.project(nodes)
        assertNull(UiSemantics.identity(target))
        chain[8].put("unavailable", JSONArray()).put("childrenIncomplete", true).put("missingChildIndex", 0)
        UiSemantics.project(nodes)
        assertNull(UiSemantics.identity(target))
        chain[8].put("childrenIncomplete", false).put("collectionItemInfo", json("rowIndex" to 1, "columnIndex" to 0))
        UiSemantics.project(nodes)
        assertTrue(target.getJSONObject("row").isNull("label"))
        chain[8].put("collectionItemInfo", JSONObject.NULL)
        chain.last().put("visible", false)
        UiSemantics.project(nodes)
        assertTrue(target.getJSONObject("row").isNull("label"))
        chain.last().put("visible", true)
        row.put("unavailable", JSONArray().put(json("field" to "description")))
        UiSemantics.project(nodes)
        assertEquals("Record A", target.getJSONObject("row").getString("label"))
        assertNotNull(UiSemantics.identity(target))
    }

    @Test fun deepUnnamedRowsRemainAmbiguousWithSharedDescriptions() {
        val rows =
            (0..1).map { index ->
                node("r$index")
                    .put("clickable", true)
                    .put("description", "Shared route label")
                    .put("collectionItemInfo", json("rowIndex" to index, "columnIndex" to 0))
            }
        val nodes =
            rows.flatMap { row ->
                val prefix = row.getString("ref")
                listOf(row) + (1..10).map { node("$prefix-$it", parent = if (it == 1) prefix else "$prefix-${it - 1}") }
            }
        UiSemantics.project(nodes)
        assertTrue(rows.all { UiSemantics.identity(it) != null })
        for (selector in listOf(json("name" to "Shared route label"), json("role" to "item", "name" to "Shared route label"))) {
            assertEquals(listOf(true, true), rows.map(UiSelector(selector)::matches))
        }
        val first = rows.first()
        val original = UiSemantics.identity(first)
        first.getJSONObject("collectionItemInfo").put("rowIndex", 2)
        UiSemantics.project(nodes)
        assertNotEquals(original, UiSemantics.identity(first))
        first.put("description", "Another description")
        UiSemantics.project(nodes)
        assertEquals("Another description", first.getJSONObject("row").getString("label"))
        first.put("unavailable", JSONArray().put(json("field" to "description")))
        UiSemantics.project(nodes)
        assertNull(UiSemantics.identity(first))
    }

    @Test fun callbackFactsDoNotUpgradeConflictingOrMissingEvidence() {
        assertEquals("done", ActionReceipt(ActionAcceptance.Accepted(true), true).classify())
        assertEquals("failed", ActionReceipt(ActionAcceptance.Accepted(false), null).classify())
        assertEquals("UNKNOWN", ActionReceipt(ActionAcceptance.Accepted(false), true).classify())
        assertEquals("UNKNOWN", ActionReceipt(ActionAcceptance.Accepted(true), null).classify())
        assertEquals("UNKNOWN", ActionReceipt(ActionAcceptance.Failed(json("message" to "binder")), true).classify())
        assertEquals("failed", ActionReceipt(ActionAcceptance.Accepted(true), false).classify())
        val failed = ActionReceipt(ActionAcceptance.Failed(json("code" to "binder", "message" to "Unavailable")), null)
        assertEquals("UNKNOWN", failed.classify())
        assertFalse(failed.json().has("accepted"))
        assertTrue(failed.json().isNull("callback"))
        assertEquals("binder", failed.json().getJSONObject("acceptedError").getString("code"))
    }

    @Test fun selectedNameSurvivesMissingDetailsButNotAnEarlierUnknown() {
        val parent = node("p").put("clickable", true)
        val children = (0..70).map { node("c$it", if (it == 0) "Ready" else "Detail $it", "p") }
        UiSemantics.project(listOf(parent) + children)
        val view = parent.getJSONObject("view")
        assertEquals("Ready", view.getString("name"))
        assertTrue(view.getBoolean("nameComplete"))
        assertFalse(view.getBoolean("detailComplete"))
        assertEquals("p", children.first().getJSONObject("view").getString("selectionTarget"))
        assertTrue(children[1].getJSONObject("view").isNull("selectionTarget"))
        assertEquals(true, UiSelector(json("name" to "Ready")).matches(parent))
        children.first().getJSONArray("unavailable").put(json("field" to "text"))
        UiSemantics.project(listOf(parent) + children)
        assertFalse(parent.getJSONObject("view").getBoolean("nameComplete"))
        assertNull(UiSemantics.identity(parent))
    }

    @Test fun absorptionDoesNotCombineSelectorFieldsOrCrossItemBoundaries() {
        val parent = node("p").put("clickable", true).put("className", "android.widget.Button")
        val label = node("label", "Open", "p")
        UiSemantics.project(listOf(parent, label))
        val both = UiSelector(json("text" to "Open", "role" to "button"))
        assertEquals(false, both.matches(parent))
        assertEquals(false, both.matches(label))
        assertEquals("p", label.getJSONObject("view").getString("selectionTarget"))
        assertTrue(label.getJSONObject("view").getBoolean("nameConsumed"))
        label.put("collectionItemInfo", json("rowIndex" to 0, "columnIndex" to 0))
        UiSemantics.project(listOf(parent, label))
        assertEquals("Open", parent.getJSONObject("view").getString("name"))
        assertTrue(label.getJSONObject("view").isNull("selectionTarget"))
        assertFalse(label.getJSONObject("view").getBoolean("nameConsumed"))
        label.put("collectionItemInfo", JSONObject.NULL).put("windowId", 2)
        UiSemantics.project(listOf(parent, label))
        assertEquals("", parent.getJSONObject("view").getString("name"))
    }

    @Test fun crossItemBodyRemainsVisibleWithoutChangingParentNameOrKey() {
        val parent = node("p").put("clickable", true)
        val item = node("row", parent = "p").put("collectionItemInfo", json("rowIndex" to 0, "columnIndex" to 0))
        val label = node("label", "Record A", "row")
        val detail = node("detail", "Record detail", "row")
        UiSemantics.project(listOf(parent, item, label, detail))
        assertEquals("Record A", parent.getJSONObject("view").getString("name"))
        assertEquals("Record detail", parent.getJSONObject("view").getString("detail"))
        assertFalse(label.getJSONObject("view").getBoolean("nameConsumed"))
        assertFalse(detail.getJSONObject("view").getBoolean("nameConsumed"))
        assertTrue(label.getJSONObject("view").isNull("selectionTarget"))
        val identity = UiSemantics.identity(parent)
        detail.put("text", "Changed detail")
        UiSemantics.project(listOf(parent, item, label, detail))
        assertEquals(identity, UiSemantics.identity(parent))
        label.put("text", "Record B")
        UiSemantics.project(listOf(parent, item, label, detail))
        assertNotEquals(identity, UiSemantics.identity(parent))
    }

    @Test fun unreadablePrefixesAndVisibleChildrenRemainInTheProjection() {
        val truncated = node("prefix", "Readable prefix")
        truncated.getJSONArray("unavailable").put(json("field" to "text", "reason" to "truncated"))
        val parent = node("parent").put("clickable", true).put("visible", false)
        val child = node("child", "Visible child", "parent")
        UiSemantics.project(listOf(truncated, parent, child))
        assertEquals("Readable prefix", truncated.getJSONObject("view").getString("name"))
        assertFalse(truncated.getJSONObject("view").getBoolean("nameComplete"))
        assertNull(UiSemantics.identity(truncated))
        assertEquals("Visible child", child.getJSONObject("view").getString("name"))
        assertFalse(child.getJSONObject("view").getBoolean("nameConsumed"))
    }

    @Test fun outerCollectionIndicesStayInFullIdentityBeyondThirtyTwoAncestors() {
        val outer = node("outer", "Outer").put("collectionItemInfo", json("rowIndex" to -1, "columnIndex" to 0))
        val ancestors = (1..41).map { node("a$it", parent = if (it == 1) "outer" else "a${it - 1}") }
        val inner = node("inner", "Inner", "a41").put("collectionItemInfo", json("rowIndex" to 0, "columnIndex" to 0))
        val target = node("target", parent = "inner").put("editable", true).put("hint", "Quantity")
        val nodes = listOf(outer) + ancestors + listOf(inner, target)
        UiSemantics.project(nodes)
        val original = requireNotNull(UiSemantics.identity(target))
        assertEquals(2, target.getJSONObject("row").getJSONArray("chain").length())
        val unchanged = copy(target)
        target
            .getJSONObject("row")
            .getJSONArray("chain")
            .getJSONObject(0)
            .put("row", 7)
        assertEquals(original, UiSemantics.identity(unchanged))
        assertNotEquals(original, UiSemantics.identity(target))
        outer.getJSONObject("collectionItemInfo").put("columnIndex", 1)
        UiSemantics.project(nodes)
        assertNotEquals(original, UiSemantics.identity(target))
    }

    @Test fun unnamedRowsMayUseIndicesWhileDescriptionsRemainOrderedEvidence() {
        val row = node("row").put("collectionItemInfo", json("rowIndex" to 0, "columnIndex" to 0))
        val button = node("button", "Delete", "row").put("className", "android.widget.Button").put("clickable", true)
        UiSemantics.project(listOf(row, button))
        assertTrue(button.getJSONObject("row").getBoolean("complete"))
        assertTrue(button.getJSONObject("row").isNull("label"))
        row.put("description", "Record A")
        UiSemantics.project(listOf(row, button))
        assertEquals("Record A", button.getJSONObject("row").getString("label"))
        row.getJSONArray("unavailable").put(json("field" to "text"))
        UiSemantics.project(listOf(row, button))
        assertFalse(button.getJSONObject("row").getBoolean("complete"))
    }

    @Test fun rowIdentityRequiresKnownClassUnlessFactsExcludeTheSubtree() {
        val row = node("row").put("collectionItemInfo", json("rowIndex" to 0, "columnIndex" to 0))
        val first = node("first", parent = "row")
        val label = node("label", "Record A", "row")
        val target = node("target", "Run", "row").put("className", "android.widget.Button").put("clickable", true)
        val nodes = listOf(row, first, label, target)
        UiSemantics.project(nodes)
        val key = UiSemantics.identity(target)
        assertNotNull(key)
        first.getJSONArray("unavailable").put(json("field" to "className"))
        UiSemantics.project(nodes)
        assertFalse(target.getJSONObject("row").getBoolean("complete"))
        assertNull(UiSemantics.identity(target))
        first.put("editable", true)
        UiSemantics.project(nodes)
        assertEquals(key, UiSemantics.identity(target))
        first.put("editable", false).put("visible", false)
        UiSemantics.project(nodes + node("child", "Uncertain descendant", "first"))
        assertFalse(target.getJSONObject("row").getBoolean("complete"))
        first.put("className", "truncated.Button")
        UiSemantics.project(nodes)
        assertNull(UiSemantics.identity(target))
    }

    @Test fun hiddenOwnTextGapsDoNotDiscardVisibleDescendantRowLabels() {
        for (field in listOf("text", "description")) {
            val row = node("row").put("collectionItemInfo", json("rowIndex" to 0, "columnIndex" to 0))
            val hidden = node("hidden", parent = "row").put("visible", false)
            val label = node("label", parent = "hidden").put(field, "Record A")
            val target = node("target", "Run", "row").put("className", "android.widget.Button").put("clickable", true)
            val nodes = listOf(row, hidden, label, target)
            UiSemantics.project(nodes)
            val key = UiSemantics.identity(target)
            assertNotNull(key)
            hidden.getJSONArray("unavailable").put(json("field" to field))
            UiSemantics.project(nodes)
            assertEquals("Record A", target.getJSONObject("row").getString("label"))
            assertEquals(key, UiSemantics.identity(target))
            hidden
                .put("childrenIncomplete", true)
                .put("missingChildIndex", 0)
            UiSemantics.project(nodes)
            assertNull(UiSemantics.identity(target))
        }
    }

    @Test fun selectionCountsEachAdmittedNodeOnceIncludingTheRoot() {
        for (count in listOf(33, 34, 64, 65)) {
            val root = node("root").put("clickable", true)
            val children = (2..count).map { node("n$it", if (it == count) "Last" else "", "root") }
            val value = UiSemantics.selection(root, { if (it === root) children.asSequence() else emptySequence() }).view
            assertEquals(if (count <= 64) "Last" else "", value.getString("name"))
            assertEquals(count <= 64, value.getBoolean("nameComplete"))
            assertEquals(count <= 64, value.getBoolean("detailComplete"))
        }
    }

    @Test fun selectionKeepsFilteringNameOnlyAndDepthBoundaries() {
        val root = node("root").put("clickable", true)
        val skipped =
            (1..70).map { node("hidden$it", "Hidden", "root").put("visible", false) } +
                node("independent", "Button", "root").put("clickable", true) +
                node("otherWindow", "Other", "root").put("windowId", 2) +
                node("namedAction", "Action", "root").put("actions", JSONArray(listOf("expand")))
        val names = (2..65).map { node("n$it", "Name $it", "root") }
        val children = { current: JSONObject -> if (current === root) (skipped + names).asSequence() else emptySequence() }
        val full = UiSemantics.selection(root, children).view
        assertEquals("Name 2", full.getString("name"))
        assertTrue(full.getBoolean("nameComplete"))
        assertFalse(full.getBoolean("detailComplete"))
        val first = UiSemantics.selection(root, children, nameOnly = true).view
        assertEquals("Name 2", first.getString("name"))
        assertTrue(first.getBoolean("detailComplete"))
        val chain = listOf(root) + (1..10).map { node("depth$it", if (it == 9) "Depth 9" else "") }
        val deep =
            UiSemantics
                .selection(
                    root,
                    { current ->
                        chain
                            .dropWhile { it !== current }
                            .drop(1)
                            .take(1)
                            .asSequence()
                    },
                ).view
        assertEquals("Depth 9", deep.getString("name"))
        assertFalse(deep.getBoolean("detailComplete"))
    }
}
