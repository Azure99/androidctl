package com.rainng.androidctl.agent.events

import android.accessibilityservice.AccessibilityService
import android.view.accessibility.AccessibilityWindowInfo
import com.rainng.androidctl.agent.WindowIds
import com.rainng.androidctl.agent.logging.AgentFallbackDiagnostics
import com.rainng.androidctl.agent.logging.RateLimitedDiagnosticReporter
import com.rainng.androidctl.agent.runtime.AccessibilityForegroundObservationProvider
import com.rainng.androidctl.agent.runtime.ForegroundObservation

internal data class ObservedAccessibilityEvent(
    val eventType: Int,
)

internal interface DeviceEventEnvironment {
    fun foregroundObservation(): ForegroundObservation

    fun currentImeState(): ImeState
}

internal class DeviceEventProcessor(
    private val buffer: DeviceEventBuffer = DeviceEventBuffer(),
    cooldownScheduler: CooldownScheduler = NoOpCooldownScheduler,
    private val aggregator: DeviceEventAggregator = DeviceEventAggregator(buffer, cooldownScheduler = cooldownScheduler),
    private val imeObservationController: ImeObservationController = ImeObservationController(),
) {
    private val sessionStateLock = Any()
    private var closed = false
    private var sessionGeneration = 0L

    fun recordRuntimeStatus(payload: RuntimeStatusPayload) {
        synchronized(sessionStateLock) {
            if (closed) {
                return
            }
            aggregator.recordRuntimeStatus(payload)
        }
    }

    fun recordAccessibilityEvent(
        event: ObservedAccessibilityEvent,
        environment: DeviceEventEnvironment,
    ) {
        if (!DeviceEventObservationPolicy.shouldObserveEvent(event.eventType)) {
            return
        }
        val observedSessionGeneration =
            synchronized(sessionStateLock) {
                if (closed) {
                    return
                }
                sessionGeneration
            }

        val foregroundObservation = environment.foregroundObservation()
        val imeState =
            imeObservationController.stateForEvent(
                eventType = event.eventType,
                refreshImeState = environment::currentImeState,
            )

        synchronized(sessionStateLock) {
            if (closed || sessionGeneration != observedSessionGeneration) {
                return
            }

            aggregator.recordObservation(
                AccessibilityObservation(
                    eventType = event.eventType,
                    generation = foregroundObservation.generation,
                    packageName = foregroundObservation.state.packageName,
                    activityName = foregroundObservation.state.activityName,
                    imeVisible = imeState.visible,
                    imeWindowId = imeState.windowId,
                ),
            )
        }
    }

    fun poll(request: EventPollRequest): EventPollResult = buffer.poll(request)

    fun cancelPendingWork() {
        synchronized(sessionStateLock) {
            if (closed) {
                return
            }
            aggregator.cancelPendingWork()
        }
    }

    fun resetForAttachmentChange() {
        synchronized(sessionStateLock) {
            if (closed) {
                return
            }
            sessionGeneration += 1L
            buffer.reset()
            aggregator.resetForAttachmentChange()
            imeObservationController.reset()
        }
    }

    fun close() {
        synchronized(sessionStateLock) {
            if (closed) {
                return
            }
            closed = true
            sessionGeneration += 1L
            aggregator.close()
        }
    }
}

internal class AccessibilityServiceDeviceEventEnvironment private constructor(
    private val foregroundObservation: ForegroundObservation,
    windowSnapshotsProvider: () -> List<AccessibilityWindowSnapshot>,
) : DeviceEventEnvironment {
    private val windowSnapshots by lazy(LazyThreadSafetyMode.NONE, windowSnapshotsProvider)

    override fun foregroundObservation(): ForegroundObservation = foregroundObservation

    override fun currentImeState(): ImeState {
        val imeWindow = windowSnapshots.firstOrNull { it.type == AccessibilityWindowInfo.TYPE_INPUT_METHOD }
        return ImeState(
            visible = imeWindow != null,
            windowId = imeWindow?.let { WindowIds.fromPlatformWindowId(it.id) },
        )
    }

    companion object {
        fun capture(
            service: AccessibilityService,
            foregroundObservationProvider: AccessibilityForegroundObservationProvider =
                AccessibilityForegroundObservationProvider(service),
            windowSnapshotReader: AccessibilityWindowSnapshotReader = AccessibilityWindowSnapshotReader(),
        ): AccessibilityServiceDeviceEventEnvironment =
            AccessibilityServiceDeviceEventEnvironment(
                foregroundObservation = foregroundObservationProvider.observe(),
                windowSnapshotsProvider = { windowSnapshotReader.read(service) },
            )
    }
}

internal class AccessibilityWindowSnapshotReader(
    private val diagnosticReporter: RateLimitedDiagnosticReporter = AgentFallbackDiagnostics.reporter,
) {
    fun read(service: AccessibilityService): List<AccessibilityWindowSnapshot> =
        runCatching {
            service.windows
                ?.map { window ->
                    AccessibilityWindowSnapshot(
                        id = window.id,
                        type = window.type,
                    )
                }.orEmpty()
        }.getOrElse { error ->
            diagnosticReporter.warn(
                key = "events.window-snapshot.read.fallback",
                message = "accessibility window snapshots unavailable; using empty list",
                throwable = error,
            )
            emptyList()
        }
}

internal data class AccessibilityWindowSnapshot(
    val id: Int,
    val type: Int,
)
