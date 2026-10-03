package com.rainng.androidctl

import org.json.JSONArray
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class UiObservationTest {
    @Test fun absenceNeedsDispatchPriorPresenceAndTheSameConnection() {
        val before = json("connection" to 7, "windows" to JSONArray(listOf(json("id" to 1))))
        assertTrue(windowAbsent(1, before, true, 7, listOf(2)))
        assertFalse(windowAbsent(1, before, true, 7, listOf(1)))
        assertFalse(windowAbsent(1, before, true, 8, listOf(2)))
        assertFalse(windowAbsent(1, before, false, 7, listOf(2)))
        assertFalse(windowAbsent(1, json("connection" to 7, "windows" to JSONArray()), true, 7, listOf(2)))
        assertFalse(windowAbsent(null, before, true, 7, listOf(2)))
        assertFalse(windowAbsent(1, null, true, 7, listOf(2)))
    }

    @Test fun settlementAndObservedChangeUseTheSameSignatureWithoutExpandingItsFields() {
        val target = node("e1", "Ready").put("hint", "Old").put("password", false).put("checkedModel", "binary")
        target.put("actions", JSONArray(listOf("click", "focus")))
        UiSemantics.project(listOf(target))
        val before =
            json(
                "connection" to 1,
                "scope" to json("window" to 1, "package" to "fixture"),
                "nodes" to JSONArray(listOf(target)),
                "windows" to JSONArray(),
                "partial" to false,
            )
        for ((field, value) in listOf("hint" to "New", "password" to true, "checkedModel" to "triState")) {
            val after = copy(before)
            after.getJSONArray("nodes").getJSONObject(0).put(field, value)
            assertEquals("unchanged", ObservedChange.compare(before, after).getString("state"))
            assertEquals(UiSnapshot.signature(before), UiSnapshot.signature(after))
        }
        val reordered = copy(before)
        reordered.getJSONArray("nodes").getJSONObject(0).put("actions", JSONArray(listOf("focus", "click")))
        assertEquals("changed", ObservedChange.compare(before, reordered).getString("state"))
        assertNotEquals(UiSnapshot.signature(before), UiSnapshot.signature(reordered))
    }

    @Test fun platformFeedbackParticipatesInCompleteObservationChanges() {
        val target = node("e1", "Quantity")
        UiSemantics.project(listOf(target))
        val before = json("nodes" to JSONArray(listOf(target)), "windows" to JSONArray(), "partial" to false)
        val changes =
            listOf(
                "error" to "Address is incomplete",
                "contentInvalid" to true,
                "rangeInfo" to json("current" to 35),
                "collectionInfo" to json("rows" to 2),
                "collectionItemInfo" to json("rowIndex" to 1),
            )
        for ((field, value) in changes) {
            val after = copy(before)
            after.getJSONArray("nodes").getJSONObject(0).put(field, value)
            assertNotEquals(UiSnapshot.signature(before), UiSnapshot.signature(after))
            assertEquals(UiSnapshot.signature(after), UiSnapshot.signature(copy(after)))
        }
    }

    @Test fun endpointChangeRequiresCompleteObservationsInTheSameConnectionAndScope() {
        val before =
            json(
                "connection" to 7,
                "scope" to json("window" to 1, "package" to "fixture"),
                "nodes" to JSONArray(listOf(node("a", "Original").put("checked", "false").put("checkedModel", "binary"))),
                "windows" to JSONArray(),
                "partial" to false,
            )
        UiSemantics.project(before.getJSONArray("nodes").objects())
        val after = copy(before)
        after.getJSONArray("nodes").getJSONObject(0).put("ref", "new-ref")
        assertEquals("unchanged", ObservedChange.compare(before, after).getString("state"))
        after.getJSONArray("nodes").getJSONObject(0).put("text", "Different")
        assertEquals("changed", ObservedChange.compare(before, after).getString("state"))
        after.put("partial", true)
        assertEquals("unknown", ObservedChange.compare(before, after).getString("state"))
        assertEquals("unknown", ObservedChange.compare(after, before).getString("state"))
        after.put("partial", false)
        after.put("connection", 8)
        assertEquals("unknown", ObservedChange.compare(before, after).getString("state"))
        after.put("connection", 7).getJSONObject("scope").put("window", 2)
        assertEquals("unknown", ObservedChange.compare(before, after).getString("state"))
        assertEquals("unknown", ObservedChange.compare(null, after).getString("state"))
        assertEquals("unknown", ObservedChange.compare(before, null).getString("state"))
        assertFalse(ObservedChange.compare(before, after).has("comparableFields"))
    }
}
