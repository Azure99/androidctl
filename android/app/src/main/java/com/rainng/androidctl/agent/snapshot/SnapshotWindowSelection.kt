package com.rainng.androidctl.agent.snapshot

import android.view.accessibility.AccessibilityWindowInfo
import com.rainng.androidctl.agent.WindowIds

internal data class SnapshotWindowDescriptor(
    val id: Int,
    val type: Int,
)

internal object SnapshotWindowSelection {
    fun payloadWindows(
        windows: List<SnapshotWindowDescriptor>,
        includeSystemWindows: Boolean,
    ): List<SnapshotWindowDescriptor> {
        if (includeSystemWindows) {
            return windows
        }
        return windows.filter { it.type == AccessibilityWindowInfo.TYPE_APPLICATION }
    }

    fun imeInfo(windows: List<SnapshotWindowDescriptor>): SnapshotIme {
        val imeWindow = windows.firstOrNull { it.type == AccessibilityWindowInfo.TYPE_INPUT_METHOD }
        return SnapshotIme(
            visible = imeWindow != null,
            windowId = imeWindow?.let { WindowIds.fromPlatformWindowId(it.id) },
        )
    }
}

internal fun descriptorFor(window: AccessibilityWindowInfo): SnapshotWindowDescriptor =
    SnapshotWindowDescriptor(
        id = window.id,
        type = window.type,
    )
