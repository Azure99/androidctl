package com.rainng.androidctl

import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONObject

internal enum class UiAction(
    val wire: String,
    val platformId: Int,
) {
    CLICK("click", AccessibilityNodeInfo.ACTION_CLICK),
    CHECK("check", AccessibilityNodeInfo.ACTION_CLICK),
    UNCHECK("uncheck", AccessibilityNodeInfo.ACTION_CLICK),
    FILL("fill", AccessibilityNodeInfo.ACTION_SET_TEXT),
    FOCUS("focus", AccessibilityNodeInfo.ACTION_FOCUS),
    LONG_CLICK("long-click", AccessibilityNodeInfo.ACTION_LONG_CLICK),
    EXPAND("expand", AccessibilityNodeInfo.ACTION_EXPAND),
    COLLAPSE("collapse", AccessibilityNodeInfo.ACTION_COLLAPSE),
    DISMISS("dismiss", AccessibilityNodeInfo.ACTION_DISMISS),
    IME("ime", android.R.id.accessibilityActionImeEnter),
    SCROLL_FORWARD("scroll-forward", AccessibilityNodeInfo.ACTION_SCROLL_FORWARD),
    SCROLL_BACKWARD("scroll-backward", AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD),
    SCROLL_UP("scroll-up", android.R.id.accessibilityActionScrollUp),
    SCROLL_DOWN("scroll-down", android.R.id.accessibilityActionScrollDown),
    SCROLL_LEFT("scroll-left", android.R.id.accessibilityActionScrollLeft),
    SCROLL_RIGHT("scroll-right", android.R.id.accessibilityActionScrollRight),
    ;

    val changesChecked: Boolean get() = this == CHECK || this == UNCHECK
    val verifiesValue: Boolean get() = this == FILL || changesChecked
    val platformName: String get() = if (changesChecked) CLICK.wire else wire
    val isScroll: Boolean
        get() =
            when (this) {
                SCROLL_FORWARD, SCROLL_BACKWARD, SCROLL_UP, SCROLL_DOWN, SCROLL_LEFT, SCROLL_RIGHT -> true
                else -> false
            }

    fun ready(node: JSONObject): Boolean = notReadyReason(node) == null

    fun notReadyReason(node: JSONObject): String? {
        if (!node.getBoolean("visible")) return "target is not visible"
        if (!node.getBoolean("enabled")) return "target is disabled"
        if (!available(node)) {
            return if (isScroll) {
                "target has no advertised route for $wire"
            } else {
                "target does not expose the $platformName action"
            }
        }
        if (this == FILL && !node.optBoolean("editable")) return "target is not editable"
        if (this == IME && !node.getBoolean("focused")) return "target is not input-focused"
        if (changesChecked && !node.optBoolean("checkable")) return "target is not checkable"
        return null
    }

    fun alreadySatisfied(
        args: JSONObject,
        node: JSONObject,
    ): Boolean {
        if (!node.getBoolean("visible")) return false
        if (this == FOCUS) return node.getBoolean("focused")
        val valueType =
            when (this) {
                FILL -> node.getBoolean("editable")
                CHECK, UNCHECK -> node.getBoolean("checkable")
                else -> false
            }
        return valueType && UiVerification.evaluate(this, args, node).state == VerificationState.MATCHES
    }

    private fun available(node: JSONObject): Boolean {
        if (this == FOCUS && node.getBoolean("focused")) return true
        if (isScroll) return UiScroll.route(node, this) != null
        val radio = UiSemantics.role(node) == Role.RADIO
        val actions = node.getJSONArray("actions")
        return (this == UNCHECK && radio) ||
            (0 until actions.length()).any { actions.getString(it) == platformName }
    }

    companion object {
        fun inputFocusNotReadyReason(node: JSONObject): String? =
            when {
                !node.getBoolean("visible") -> "target is not visible"
                !node.getBoolean("enabled") -> "target is disabled"
                !node.getBoolean("focused") -> "target is not input-focused"
                else -> null
            }

        fun parse(value: String): UiAction =
            entries.firstOrNull { it.wire == value }
                ?: throw Rejected("action", "Unsupported action: $value; use ${entries.joinToString("|") { it.wire }}")
    }
}
