package com.rainng.androidctl.agent.snapshot

import android.view.accessibility.AccessibilityWindowInfo
import com.rainng.androidctl.agent.WindowIds

internal data class SnapshotWindowSelectionResult(
    val descriptors: List<SnapshotWindowDescriptor>,
    val payloadWindows: List<AccessibilityWindowInfo>,
)

internal class SnapshotWindowCollector {
    fun selectWindows(
        allWindows: List<AccessibilityWindowInfo>,
        includeSystemWindows: Boolean,
    ): SnapshotWindowSelectionResult {
        val descriptors = allWindows.map(::descriptorFor)
        val payloadWindowIds =
            SnapshotWindowSelection
                .payloadWindows(
                    windows = descriptors,
                    includeSystemWindows = includeSystemWindows,
                ).mapTo(linkedSetOf()) { it.id }
        return SnapshotWindowSelectionResult(
            descriptors = descriptors,
            payloadWindows = allWindows.filter { it.id in payloadWindowIds },
        )
    }

    fun appendWindowPayload(
        window: AccessibilityWindowInfo,
        includeInvisible: Boolean,
        state: SnapshotCollectionState,
        nodeCollector: SnapshotNodeCollector,
    ) {
        val root = window.root ?: return
        val windowKey = WindowIds.fromPlatformWindowId(window.id)
        val rootPackageName = root.packageName?.toString()
        val rootRid =
            nodeCollector.appendWindowRoot(
                node = root,
                windowKey = windowKey,
                includeInvisible = includeInvisible,
                state = state,
            ) ?: return
        state.windowsPayload +=
            SnapshotWindow(
                windowId = windowKey,
                type = SnapshotProtocolTypes.windowType(window.type),
                layer = window.layer,
                packageName = rootPackageName,
                bounds = rectToList(window.boundsInScreenRect()),
                rootRid = rootRid,
            )
    }
}
