package com.rainng.androidctl.agent.snapshot

import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo

internal object SnapshotProtocolTypes {
    fun windowType(type: Int): String =
        when (type) {
            AccessibilityWindowInfo.TYPE_APPLICATION -> "application"
            AccessibilityWindowInfo.TYPE_INPUT_METHOD -> "inputMethod"
            AccessibilityWindowInfo.TYPE_SYSTEM -> "system"
            AccessibilityWindowInfo.TYPE_ACCESSIBILITY_OVERLAY -> "accessibilityOverlay"
            AccessibilityWindowInfo.TYPE_SPLIT_SCREEN_DIVIDER -> "splitScreenDivider"
            AccessibilityWindowInfo.TYPE_MAGNIFICATION_OVERLAY -> "magnificationOverlay"
            else -> "unknown"
        }

    fun actionType(actionId: Int): String =
        when (actionId) {
            AccessibilityNodeInfo.ACTION_CLICK -> "click"
            AccessibilityNodeInfo.ACTION_LONG_CLICK -> "longClick"
            AccessibilityNodeInfo.ACTION_FOCUS -> "focus"
            AccessibilityNodeInfo.ACTION_SCROLL_FORWARD -> "scrollForward"
            AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD -> "scrollBackward"
            scrollDownActionId() -> "scrollDown"
            scrollUpActionId() -> "scrollUp"
            scrollLeftActionId() -> "scrollLeft"
            scrollRightActionId() -> "scrollRight"
            AccessibilityNodeInfo.ACTION_SET_TEXT -> "setText"
            AccessibilityNodeInfo.ACTION_DISMISS -> "dismiss"
            imeEnterActionId() -> "submit"
            else -> "action_$actionId"
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
