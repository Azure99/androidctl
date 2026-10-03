package com.rainng.androidctl

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class UiSelectorTest {
    @Test fun selectorsRejectUnknownFieldsBeforeReadingNodesAndKeepLongStrings() {
        assertEquals("selector", assertThrows(Rejected::class.java) { UiSelector(json("unknown" to "value")) }.code)
        val name = "x".repeat(60000) + "&"
        val target = node("long", name)
        UiSemantics.project(listOf(target))
        assertEquals(true, UiSelector(json("name" to name)).matches(target))
        assertEquals(true, UiSelector(json("name" to ("x".repeat(60000) + "&amp;"))).matches(target))
    }

    @Test fun ambiguityOffersReadableRowsWithoutInventingUnknownPositions() {
        val target = node("e1", "Delete").put("refAvailable", true)
        UiSemantics.project(listOf(target))
        target.put("row", json("complete" to true, "label" to "Cable", "nearest" to json("row" to 1, "column" to -1)))
        val message = UiSelector.ambiguity(listOf(target), incomplete = false)
        assertTrue(message.contains("row=\"Cable\" rowIndex=1"))
        assertFalse(message.contains("columnIndex"))
        assertFalse(message.contains("nearest"))
        target.getJSONObject("row").put("complete", false)
        val unknown = UiSelector.ambiguity(listOf(target), incomplete = true)
        assertTrue(unknown.contains("row-unavailable"))
        assertFalse(unknown.contains("Cable"))
        assertTrue(unknown.contains("additional matches may be missing"))
    }

    @Test fun ambiguousCandidatesExposeOnlyPublishedUsableRefsAndMarkOmissions() {
        val nodes = (1..7).map { node("e$it", "Delete").put("refAvailable", true) }
        UiSemantics.project(nodes)
        nodes[0].getJSONObject("view").put("name", "A".repeat(300))
        nodes[1].put("refAvailable", false).put("refReason", "Selection identity is incomplete")
        nodes[2].put("row", json("complete" to true, "label" to "business-B"))
        val complete = UiSelector.ambiguity(nodes, incomplete = false)
        assertTrue(complete.contains("candidate ref=e1 "))
        assertFalse(complete.contains("ref=e2 "))
        assertTrue(complete.contains("Selection identity is incomplete"))
        assertTrue(complete.contains("business-B"))
        assertTrue(complete.contains("44 chars omitted"))
        assertTrue(complete.contains("2 candidates omitted"))
        assertFalse(complete.contains("ref=e6 "))
        nodes.forEach { it.put("refAvailable", false).put("refReason", "Selection identity is incomplete") }
        val partial = UiSelector.ambiguity(nodes, incomplete = true)
        assertFalse(partial.contains("candidate ref="))
        assertTrue(partial.contains("Selection identity is incomplete"))
        assertTrue(partial.contains("additional matches may be missing"))
    }

    @Test fun exactSelectorKeepsUnknownFieldsOutOfUniqueness() {
        val input = node("e1", "prefix").put("editable", true).put("hint", "Quantity")
        input.getJSONArray("unavailable").put(json("field" to "text", "reason" to "truncated"))
        UiSemantics.project(listOf(input))
        assertNull(UiSelector(json("text" to "prefix")).matches(input))
        assertEquals(false, UiSelector(json("text" to "prefix", "role" to "button")).matches(input))
        assertEquals(true, UiSelector(json("name" to "Quantity", "role" to "textbox")).matches(input))
    }

    @Test fun entitiesAreDecodedOnceAndBothSpellingsRemainCandidates() {
        val one = node("e1", "A & B")
        val two = node("e2", "A &amp; B")
        UiSemantics.project(listOf(one, two))
        val selector = UiSelector(json("name" to "A &amp; B"))
        assertEquals(listOf(true, true), listOf(one, two).map(selector::matches))
    }

    @Test fun invisibleNodesLeaveTheCandidateSet() {
        val candidate = node("a", "Open")
        val selector = UiSelector(json("name" to "Open"))
        UiSemantics.project(listOf(candidate))
        candidate.put("enabled", false)
        assertEquals(true, selector.matches(candidate))
        candidate.put("visible", false)
        assertEquals(false, selector.matches(candidate))
    }
}
