package com.rainng.androidctl

import org.json.JSONArray
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class UiActionTest {
    @Test fun knownFocusDoesNotRequireAnotherFocusAction() {
        val target = node("e1").put("focused", true).put("actions", JSONArray())
        assertTrue(UiAction.FOCUS.ready(target))
        target.put("focused", false)
        assertFalse(UiAction.FOCUS.ready(target))
        target.getJSONArray("actions").put("focus")
        assertTrue(UiAction.FOCUS.ready(target))
        target.put("enabled", false)
        assertFalse(UiAction.FOCUS.ready(target))
        target.put("enabled", true).put("visible", false)
        assertFalse(UiAction.FOCUS.ready(target))
    }

    @Test fun imeRequiresFocusAndItsAdvertisementButDoesNotRequireAnEditableNode() {
        val target = node("e1").put("focused", true).put("editable", false).put("actions", JSONArray(listOf("ime")))
        assertTrue(UiAction.IME.ready(target))
        target.put("focused", false)
        assertFalse(UiAction.IME.ready(target))
        assertEquals("target is not input-focused", UiAction.IME.notReadyReason(target))
        target.put("focused", true).put("actions", JSONArray())
        assertFalse(UiAction.IME.ready(target))
        assertEquals("target does not expose the ime action", UiAction.IME.notReadyReason(target))
    }

    @Test fun admissionReportsTheFirstBlockingConditionAndTheActualPlatformAction() {
        val target = node("e1").put("visible", false).put("enabled", false)
        assertEquals("target is not visible", UiAction.CHECK.notReadyReason(target))
        target.put("visible", true)
        assertEquals("target is disabled", UiAction.CHECK.notReadyReason(target))
        target.put("enabled", true)
        assertEquals("target does not expose the click action", UiAction.CHECK.notReadyReason(target))
    }

    @Test fun matchingReadableValuesCanSkipDisabledInputButCannotUsePasswordMasks() {
        val target = node("e1", "value").put("editable", true).put("enabled", false)
        target.put("focused", true)
        assertTrue(UiAction.FOCUS.alreadySatisfied(target))
        target.put("visible", false)
        assertFalse(UiAction.FOCUS.alreadySatisfied(target))
    }
}
