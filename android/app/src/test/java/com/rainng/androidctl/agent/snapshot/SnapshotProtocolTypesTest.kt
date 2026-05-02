package com.rainng.androidctl.agent.snapshot

import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import org.junit.Assert.assertEquals
import org.junit.Test

class SnapshotProtocolTypesTest {
    @Test
    fun windowTypeWireTokensStayStable() {
        assertEquals("application", SnapshotProtocolTypes.windowType(AccessibilityWindowInfo.TYPE_APPLICATION))
        assertEquals("inputMethod", SnapshotProtocolTypes.windowType(AccessibilityWindowInfo.TYPE_INPUT_METHOD))
        assertEquals("system", SnapshotProtocolTypes.windowType(AccessibilityWindowInfo.TYPE_SYSTEM))
        assertEquals(
            "accessibilityOverlay",
            SnapshotProtocolTypes.windowType(AccessibilityWindowInfo.TYPE_ACCESSIBILITY_OVERLAY),
        )
        assertEquals(
            "splitScreenDivider",
            SnapshotProtocolTypes.windowType(AccessibilityWindowInfo.TYPE_SPLIT_SCREEN_DIVIDER),
        )
        assertEquals(
            "magnificationOverlay",
            SnapshotProtocolTypes.windowType(AccessibilityWindowInfo.TYPE_MAGNIFICATION_OVERLAY),
        )
        assertEquals("unknown", SnapshotProtocolTypes.windowType(999))
    }

    @Test
    fun actionWireTokensStayStable() {
        assertEquals("click", SnapshotProtocolTypes.actionType(AccessibilityNodeInfo.ACTION_CLICK))
        assertEquals("longClick", SnapshotProtocolTypes.actionType(AccessibilityNodeInfo.ACTION_LONG_CLICK))
        assertEquals("focus", SnapshotProtocolTypes.actionType(AccessibilityNodeInfo.ACTION_FOCUS))
        assertEquals("scrollForward", SnapshotProtocolTypes.actionType(AccessibilityNodeInfo.ACTION_SCROLL_FORWARD))
        assertEquals("scrollBackward", SnapshotProtocolTypes.actionType(AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD))
        assertEquals("scrollDown", SnapshotProtocolTypes.actionType(scrollDownActionId()))
        assertEquals("scrollUp", SnapshotProtocolTypes.actionType(scrollUpActionId()))
        assertEquals("scrollLeft", SnapshotProtocolTypes.actionType(scrollLeftActionId()))
        assertEquals("scrollRight", SnapshotProtocolTypes.actionType(scrollRightActionId()))
        assertEquals("setText", SnapshotProtocolTypes.actionType(AccessibilityNodeInfo.ACTION_SET_TEXT))
        assertEquals("dismiss", SnapshotProtocolTypes.actionType(AccessibilityNodeInfo.ACTION_DISMISS))
        assertEquals("submit", SnapshotProtocolTypes.actionType(imeEnterActionId()))
        assertEquals("action_321", SnapshotProtocolTypes.actionType(321))
    }

    private fun scrollDownActionId(): Int =
        runCatching { AccessibilityNodeInfo.AccessibilityAction.ACTION_SCROLL_DOWN.id }
            .getOrDefault(android.R.id.accessibilityActionScrollDown)

    private fun scrollUpActionId(): Int =
        runCatching { AccessibilityNodeInfo.AccessibilityAction.ACTION_SCROLL_UP.id }
            .getOrDefault(android.R.id.accessibilityActionScrollUp)

    private fun scrollLeftActionId(): Int =
        runCatching { AccessibilityNodeInfo.AccessibilityAction.ACTION_SCROLL_LEFT.id }
            .getOrDefault(android.R.id.accessibilityActionScrollLeft)

    private fun scrollRightActionId(): Int =
        runCatching { AccessibilityNodeInfo.AccessibilityAction.ACTION_SCROLL_RIGHT.id }
            .getOrDefault(android.R.id.accessibilityActionScrollRight)

    private fun imeEnterActionId(): Int =
        runCatching { AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.id }
            .getOrDefault(android.R.id.accessibilityActionImeEnter)
}
