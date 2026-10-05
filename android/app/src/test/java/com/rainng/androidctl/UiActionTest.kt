package com.rainng.androidctl

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
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

    @Test fun fillRequiresAnEditableNodeAndItsAdvertisementButDoesNotRequireFocus() {
        val target = node("e1").put("editable", true).put("focused", false).put("actions", JSONArray(listOf("fill")))
        assertTrue(UiAction.FILL.ready(target))
        target.put("editable", false)
        assertFalse(UiAction.FILL.ready(target))
        assertEquals("target is not editable", UiAction.FILL.notReadyReason(target))
        target.put("editable", true).put("actions", JSONArray())
        assertFalse(UiAction.FILL.ready(target))
        assertEquals("target does not expose the fill action", UiAction.FILL.notReadyReason(target))
    }

    @Test fun admissionReportsTheFirstBlockingConditionAndTheActualPlatformAction() {
        val target = node("e1").put("visible", false).put("enabled", false)
        assertEquals("target is not visible", UiAction.CHECK.notReadyReason(target))
        target.put("visible", true)
        assertEquals("target is disabled", UiAction.CHECK.notReadyReason(target))
        target.put("enabled", true)
        assertEquals("target does not expose the click action", UiAction.CHECK.notReadyReason(target))
        target.put("actions", JSONArray(listOf("click")))
        assertEquals("target is not checkable", UiAction.CHECK.notReadyReason(target))
        target.put("checkable", true)
        assertNull(UiAction.CHECK.notReadyReason(target))
        assertTrue(UiAction.CHECK.ready(target))
        target.put("actions", JSONArray()).put("className", "android.widget.RadioButton")
        assertNull(UiAction.UNCHECK.notReadyReason(target))
    }

    @Test fun matchingReadableValuesCanSkipDisabledInputButCannotUsePasswordMasks() {
        val target = node("e1", "value").put("editable", true).put("enabled", false)
        val args = json("value" to "value")
        assertTrue(UiAction.FILL.alreadySatisfied(args, target))
        assertEquals("target is disabled", UiAction.FILL.notReadyReason(target))
        target.put("password", true)
        assertFalse(UiAction.FILL.alreadySatisfied(args, target))
        target.put("password", false).getJSONArray("unavailable").put(json("field" to "text"))
        assertFalse(UiAction.FILL.alreadySatisfied(args, target))
        target.put("checkable", true).put("checked", "true")
        assertTrue(UiAction.CHECK.alreadySatisfied(JSONObject(), target))
        assertFalse(UiAction.UNCHECK.alreadySatisfied(JSONObject(), target))
        target.put("checked", "false")
        assertTrue(UiAction.UNCHECK.alreadySatisfied(JSONObject(), target))
        target.put("checkable", false)
        assertFalse(UiAction.UNCHECK.alreadySatisfied(JSONObject(), target))
        target.put("focused", true)
        assertTrue(UiAction.FOCUS.alreadySatisfied(JSONObject(), target))
        target.put("visible", false)
        assertFalse(UiAction.FOCUS.alreadySatisfied(JSONObject(), target))
        assertFalse(UiAction.CHECK.alreadySatisfied(JSONObject(), target))
    }

    @Test fun inputFocusAdmissionRequiresVisibleEnabledAndFocusedWithoutAnEditableOrActionRequirement() {
        val target = node("e1").put("focused", true)
        assertNull(UiAction.inputFocusNotReadyReason(target))
        target.put("visible", false).put("enabled", false).put("focused", false)
        assertEquals("target is not visible", UiAction.inputFocusNotReadyReason(target))
        target.put("visible", true)
        assertEquals("target is disabled", UiAction.inputFocusNotReadyReason(target))
        target.put("enabled", true)
        assertEquals("target is not input-focused", UiAction.inputFocusNotReadyReason(target))
        target.put("editable", true).put("text", "value").put("enabled", false)
        assertTrue(UiAction.FILL.alreadySatisfied(json("value" to "value"), target))
        assertEquals("target is disabled", UiAction.inputFocusNotReadyReason(target))
    }
}
