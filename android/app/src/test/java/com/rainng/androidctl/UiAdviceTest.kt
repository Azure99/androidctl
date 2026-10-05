package com.rainng.androidctl

import org.json.JSONArray
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
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
        control.put("refAvailable", true).put("actions", JSONArray(listOf("click")))
        assertNull(UiAdvice.suggestedRow(tree, label, UiAction.CHECK))
    }
}
