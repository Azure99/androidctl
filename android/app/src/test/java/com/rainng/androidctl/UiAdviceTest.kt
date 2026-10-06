package com.rainng.androidctl

import org.json.JSONArray
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class UiAdviceTest {
    @Test fun rowControlAdmissionReportsTheFirstBlockingCondition() {
        val check = node("control").put("visible", false).put("enabled", false).put("checked", "unknown")
        check.put("actions", JSONArray(listOf("click")))
        assertEquals("control is not checkable", UiAdvice.rowControlNotReadyReason(check))
        check.put("checkable", true)
        assertEquals("control is not visible", UiAdvice.rowControlNotReadyReason(check))
        check.put("visible", true)
        assertEquals("control is disabled", UiAdvice.rowControlNotReadyReason(check))
        check.put("enabled", true)
        assertEquals("checked state is not false, true, or mixed", UiAdvice.rowControlNotReadyReason(check))
        check.put("checked", "false")
        assertEquals("control now exposes the click action", UiAdvice.rowControlNotReadyReason(check))
        assertFalse(UiAdvice.rowControlReady(check))
        check.put("actions", JSONArray())
        for (state in listOf("false", "true", "mixed")) {
            check.put("checked", state)
            assertNull(UiAdvice.rowControlNotReadyReason(check))
            assertTrue(UiAdvice.rowControlReady(check))
        }
    }

    @Test fun suggestedRowKeepsTheRequestedLabelAndUniqueVerificationControlDistinct() {
        val row =
            node("row", "Option")
                .put("collectionItemInfo", json("rowIndex" to 0, "columnIndex" to 0))
                .put("refAvailable", true)
                .put("actions", JSONArray(listOf("click")))
        val label = node("label", "Option", "row").put("refAvailable", true)
        val control = node("control", parent = "row").put("checkable", true).put("refAvailable", true)
        val nodes = listOf(row, label, control)
        UiSemantics.project(nodes)
        val tree = json("nodes" to JSONArray(nodes))
        val suggestion = UiAdvice.suggestedRow(tree, label, UiAction.CHECK)!!
        assertEquals("row", suggestion.row.getString("ref"))
        assertEquals("control", suggestion.verificationControl.getString("ref"))
        control.put("checked", "mixed")
        assertEquals("control", UiAdvice.suggestedRow(tree, control, UiAction.CHECK)!!.verificationControl.getString("ref"))
        control.put("enabled", false)
        assertNull(UiAdvice.suggestedRow(tree, label, UiAction.CHECK))
        control.put("enabled", true).put("refAvailable", false)
        assertNull(UiAdvice.suggestedRow(tree, label, UiAction.CHECK))
        assertEquals("explicitRowClick", assertThrows(Rejected::class.java) { UiAdvice.checkAdvice(tree, label, UiAction.CHECK) }.code)
        control.put("refAvailable", true).put("actions", JSONArray(listOf("click")))
        assertNull(UiAdvice.suggestedRow(tree, label, UiAction.CHECK))
    }

    @Test fun rowAdviceRequiresOneKnownCheckableAndNeverSuggestsUndoingAMatch() {
        val row =
            node("row", "Option")
                .put("collectionItemInfo", json("rowIndex" to 0, "columnIndex" to 0))
                .put("clickable", true)
                .put("refAvailable", true)
                .put("actions", JSONArray(listOf("click")))
        val check = node("check", parent = "row").put("checkable", true).put("checked", "false").put("actions", JSONArray())
        val nodes = listOf(row, check)
        UiSemantics.project(nodes)
        val tree = json("nodes" to JSONArray(nodes))
        val advice = assertThrows(Rejected::class.java) { UiAdvice.checkAdvice(tree, check, UiAction.CHECK) }
        assertEquals("explicitRowClick", advice.code)
        assertTrue(advice.message.contains("row row (Option)"))
        check.put("checked", "true")
        assertEquals("valueAlreadyMatches", assertThrows(Rejected::class.java) { UiAdvice.checkAdvice(tree, row, UiAction.CHECK) }.code)
        check.put("checked", "false").put("actions", JSONArray(listOf("click")))
        UiAdvice.checkAdvice(tree, check, UiAction.CHECK)
        check.put("actions", JSONArray()).put("checked", "-1")
        UiAdvice.checkAdvice(tree, check, UiAction.CHECK)
        check.put("checked", "false")
        tree.getJSONArray("nodes").put(node("second", parent = "row").put("checkable", true))
        UiAdvice.checkAdvice(tree, check, UiAction.CHECK)
        tree.put("nodes", JSONArray(nodes))
        row.put("childrenIncomplete", true)
        UiAdvice.checkAdvice(tree, check, UiAction.CHECK)
        row.put("childrenIncomplete", false)
        check.put("collectionItemInfo", json("rowIndex" to 1, "columnIndex" to 0))
        UiAdvice.checkAdvice(tree, row, UiAction.CHECK)
    }

    @Test fun locateRejectsKnownRadioUncheckBeforeReadiness() {
        val radio =
            node("radio", "Option")
                .put("checkable", true)
                .put("className", "android.widget.RadioButton")
                .put("actions", JSONArray())
        val tree = json("nodes" to JSONArray(listOf(radio)))
        for ((checked, state) in listOf("true" to "selected", "mixed" to "mixed", "-1" to "unknown")) {
            radio.put("checked", checked)
            val rejected = assertThrows(Rejected::class.java) { UiAdvice.checkAdvice(tree, radio, UiAction.UNCHECK) }
            assertEquals("radioUncheck", rejected.code)
            assertTrue(rejected.message.contains("state is $state"))
        }
        radio.put("checked", "false")
        UiAdvice.checkAdvice(tree, radio, UiAction.UNCHECK)
        assertTrue(UiAction.UNCHECK.ready(radio))
        radio.getJSONArray("unavailable").put(json("field" to "className"))
        UiAdvice.checkAdvice(tree, radio, UiAction.UNCHECK)
    }
}
