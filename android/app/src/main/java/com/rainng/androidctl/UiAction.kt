package com.rainng.androidctl

import android.view.accessibility.AccessibilityNodeInfo

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
    val platformName: String get() = if (changesChecked) CLICK.wire else wire
}
