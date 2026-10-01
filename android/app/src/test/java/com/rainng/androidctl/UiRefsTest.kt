package com.rainng.androidctl

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class UiRefsTest {
    @Test fun uniqueSelectionKeepsItsRefAndRewritesRelations() {
        val refs = UiRefs()
        refs.replace(tree(), emptyMap())
        val first = tree()
        val previous = refs.replace(first, emptyMap()).values.associateBy { it.getString("ref") }
        val second = tree()
        refs.replace(second, previous)
        val button = second.getJSONArray("nodes").getJSONObject(0)
        val label = second.getJSONArray("nodes").getJSONObject(1)
        assertEquals(first.getJSONArray("nodes").getJSONObject(0).getString("ref"), button.getString("ref"))
        assertEquals(button.getString("ref"), label.getString("parent"))
        assertFalse(button.getJSONObject("view").has("nameSource"))
        assertEquals(button.getString("ref"), label.getJSONObject("view").getString("selectionTarget"))
        assertEquals(button.getString("ref"), second.getJSONArray("missing").getJSONObject(0).getString("ref"))
        assertTrue(button.getBoolean("refAvailable"))
    }

    @Test fun changedSelectionGetsANewRef() {
        val changes: List<(JSONObject) -> Unit> =
            listOf(
                { it.put("sourceId", "new-source") },
                { it.put("windowId", 22) },
                { it.getJSONObject("view").put("name", "Different") },
                { it.getJSONObject("view").put("role", "item") },
                { it.getJSONObject("row").put("label", "Different row") },
                { it.getJSONObject("row").getJSONArray("chain").put(json("row" to 1, "column" to 0)) },
            )
        for (change in changes) {
            val refs = UiRefs()
            val first = tree()
            val previous = refs.replace(first, emptyMap()).values.associateBy { it.getString("ref") }
            val second = tree()
            val target = second.getJSONArray("nodes").getJSONObject(0)
            change(target)
            refs.replace(second, previous)
            assertTrue(target.getBoolean("refAvailable"))
            assertNotEquals(first.getJSONArray("nodes").getJSONObject(0).getString("ref"), target.getString("ref"))
        }
    }

    @Test fun incompleteOrDuplicateIdentityCannotSupplyATarget() {
        for (sameName in listOf(true, false)) {
            val raw = tree()
            val nodes = raw.getJSONArray("nodes")
            val duplicate = copy(nodes.getJSONObject(0)).put("ref", "duplicate")
            if (!sameName) duplicate.getJSONObject("view").put("name", "Other name at same address")
            nodes.put(duplicate)
            val usable = UiRefs().replace(raw, emptyMap())
            assertFalse(usable.containsKey("e1"))
            assertFalse(usable.containsKey("duplicate"))
            assertTrue(usable.containsKey("e2"))
            assertFalse(usable.containsKey("e3"))
            assertTrue(usable.containsKey("e4"))
            assertFalse(usable.containsKey("e5"))
            assertFalse(nodes.getJSONObject(0).getBoolean("refAvailable"))
            assertEquals("Selection identity is not unique", duplicate.getString("refReason"))
            assertEquals("Selection identity is incomplete", nodes.getJSONObject(2).getString("refReason"))
        }
    }

    @Test fun clippedExpansionCannotMakeDescendantsOrOtherBranchesSelectable() {
        val nodes =
            listOf(
                node("parent").put("sourceId", "repeated"),
                node("child", "Child", "parent"),
                node("duplicate").put("sourceId", "repeated").put("childrenIncomplete", true).put("missingChildIndex", 0),
                node("sibling", "Independent"),
            )
        UiSemantics.project(nodes)
        val raw = json("nodes" to JSONArray(nodes), "missing" to JSONArray(), "partial" to true)
        val withoutGap = UiRefs().replace(copy(raw), emptyMap())
        assertTrue(withoutGap.containsKey("child"))
        assertTrue(withoutGap.containsKey("sibling"))
        raw.getJSONArray("missing").put(json("ref" to "duplicate", "reason" to "repeatedSource", "unreadChildren" to 1))

        val usable = UiRefs().replace(raw, emptyMap())
        assertTrue(usable.isEmpty())
        for (node in nodes) {
            assertFalse(node.getBoolean("refAvailable"))
            assertEquals("Repeated source expansion was stopped; uniqueness is unknown", node.getString("refReason"))
        }
    }

    @Test fun replacingWithoutPreviousTargetsDoesNotReuseIssuedReferences() {
        val refs = UiRefs()
        val first =
            refs
                .replace(tree(), emptyMap())
                .values
                .map { it.getString("ref") }
                .toSet()
        val fresh =
            refs
                .replace(tree(), emptyMap())
                .values
                .map { it.getString("ref") }
                .toSet()
        assertTrue(first.intersect(fresh).isEmpty())
    }

    private fun tree(): JSONObject {
        val fixture = javaClass.getResourceAsStream("/wire-contract.json")!!.bufferedReader().use { JSONObject(it.readText()) }
        return copy(fixture.getJSONObject("raw")).also {
            UiSemantics.project(it.getJSONArray("nodes").objects())
            it.put("missing", JSONArray(listOf(json("ref" to "e1", "reason" to "test gap"))))
        }
    }
}
