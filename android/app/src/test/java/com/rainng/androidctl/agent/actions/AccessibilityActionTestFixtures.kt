package com.rainng.androidctl.agent.actions

import android.view.accessibility.AccessibilityNodeInfo

internal fun imeEnterActionId(): Int =
    runCatching { AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.id }
        .getOrDefault(android.R.id.accessibilityActionImeEnter)
