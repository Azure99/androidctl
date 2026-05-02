package com.rainng.androidctl.agent.snapshot

import android.accessibilityservice.AccessibilityService
import android.content.Context
import android.graphics.Rect
import android.hardware.display.DisplayManager
import android.util.DisplayMetrics
import android.view.Display
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import com.rainng.androidctl.agent.errors.RpcErrorCode
import com.rainng.androidctl.agent.runtime.AccessibilityForegroundObservationProvider
import com.rainng.androidctl.agent.runtime.AgentRuntimeBridge
import com.rainng.androidctl.agent.runtime.ForegroundObservationProvider
import com.rainng.androidctl.agent.runtime.ForegroundObservationStateAccess

internal class SnapshotCollector(
    private val service: AccessibilityService,
    foregroundObservationStateAccess: ForegroundObservationStateAccess? = null,
    foregroundObservationProvider: ForegroundObservationProvider? = null,
    private val actionIdProvider: (AccessibilityNodeInfo) -> List<Int> = { node ->
        node.actionList.map { action -> action.id }
    },
    private val displayProvider: (Int) -> Display? = { displayId ->
        (service.getSystemService(Context.DISPLAY_SERVICE) as? DisplayManager)?.getDisplay(displayId)
    },
    private val snapshotDisplayProvider: (Display) -> SnapshotDisplay? = ::snapshotDisplayFromDisplay,
) {
    private val foregroundObservationStateAccessProvider: () -> ForegroundObservationStateAccess =
        { foregroundObservationStateAccess ?: AgentRuntimeBridge.foregroundObservationStateAccessRole }
    private val foregroundObservationProvider: ForegroundObservationProvider =
        foregroundObservationProvider ?: AccessibilityForegroundObservationProvider(
            service = service,
            foregroundObservationStateAccessProvider = foregroundObservationStateAccessProvider,
        )
    private val windowCollector = SnapshotWindowCollector()
    private val nodeCollector = SnapshotNodeCollector(actionIdProvider)
    private val displayResolver = SnapshotDisplayResolver(displayProvider, snapshotDisplayProvider)

    fun collect(
        includeInvisible: Boolean,
        includeSystemWindows: Boolean,
    ): SnapshotPublication {
        val generation = SnapshotRegistry.currentGeneration()
        val foregroundObservation = foregroundObservationProvider.observe()
        if (!foregroundObservation.interactive) {
            throw SnapshotException(
                code = RpcErrorCode.NO_ACTIVE_WINDOW,
                message = "device screen is not interactive",
                retryable = true,
            )
        }

        val allWindows = service.windows?.toList().orEmpty()
        val windowSelection = windowCollector.selectWindows(allWindows, includeSystemWindows)
        if (windowSelection.payloadWindows.isEmpty()) {
            throw SnapshotException(
                code = RpcErrorCode.NO_ACTIVE_WINDOW,
                message = "no active accessibility window is available",
                retryable = true,
            )
        }
        val state = SnapshotCollectionState(snapshotId = SnapshotRegistry.nextSnapshotId())

        windowSelection.payloadWindows.forEach { window ->
            windowCollector.appendWindowPayload(
                window = window,
                includeInvisible = includeInvisible,
                state = state,
                nodeCollector = nodeCollector,
            )
        }

        val foregroundState = foregroundObservation.state

        return SnapshotPublication.create(
            response =
                SnapshotPayload(
                    snapshotId = state.snapshotId,
                    capturedAt =
                        java.time.Instant
                            .now()
                            .toString(),
                    packageName = foregroundState.packageName,
                    activityName = foregroundState.activityName,
                    display = displayResolver.resolve(service, windowSelection.payloadWindows),
                    ime = SnapshotWindowSelection.imeInfo(windowSelection.descriptors),
                    windows = state.windowsPayload,
                    nodes = state.nodesPayload,
                ),
            registryRecord =
                SnapshotRecord(
                    snapshotId = state.snapshotId,
                    ridToHandle = state.ridToHandle.toMap(),
                ),
            generation = generation,
        )
    }
}

internal fun AccessibilityWindowInfo.boundsInScreenRect(): Rect {
    val rect = Rect()
    getBoundsInScreen(rect)
    return rect
}

internal fun AccessibilityNodeInfo.boundsInScreenRect(): Rect {
    val rect = Rect()
    getBoundsInScreen(rect)
    return rect
}

internal fun rectToList(rect: Rect): List<Int> = listOf(rect.left, rect.top, rect.right, rect.bottom)

@Suppress("DEPRECATION")
internal fun snapshotDisplayFromDisplay(display: Display): SnapshotDisplay {
    val metrics = DisplayMetrics()
    display.getRealMetrics(metrics)
    return SnapshotDisplay(
        widthPx = metrics.widthPixels,
        heightPx = metrics.heightPixels,
        densityDpi = metrics.densityDpi,
        rotation = display.rotation,
    )
}
