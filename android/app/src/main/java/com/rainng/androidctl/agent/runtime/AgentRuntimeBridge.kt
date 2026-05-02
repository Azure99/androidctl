package com.rainng.androidctl.agent.runtime

import android.accessibilityservice.AccessibilityService
import android.annotation.SuppressLint
import android.content.Context
import com.rainng.androidctl.agent.AgentConstants
import kotlinx.coroutines.flow.StateFlow

// Composition root for Android runtime collaborators.
@SuppressLint("SyntheticAccessor", "StaticFieldLeak")
@Suppress("TooManyFunctions")
object AgentRuntimeBridge {
    private var graph = AgentRuntimeGraph.create()

    val state: StateFlow<AgentRuntimeState>
        get() = graph.statusStore.state

    internal var deviceTokenStoreAccess: DeviceTokenStoreAccess
        get() = graph.deviceTokenCoordinator.deviceTokenStoreAccess
        set(value) {
            graph.deviceTokenCoordinator.deviceTokenStoreAccess = value
        }

    internal var accessibilityServiceEnabledProbe: (Context) -> Boolean
        get() = graph.runtimeCoordinator.accessibilityServiceEnabledProbe
        set(value) {
            graph.runtimeCoordinator.accessibilityServiceEnabledProbe = value
        }

    internal var warningLogger: (String) -> Unit
        get() = graph.runtimeCoordinator.warningLogger
        set(value) {
            graph.runtimeCoordinator.warningLogger = value
        }

    internal var serverRunningProbe: (Context) -> Boolean
        get() = graph.runtimeCoordinator.serverRunningProbe
        set(value) {
            graph.runtimeCoordinator.serverRunningProbe = value
        }

    internal var foregroundHintUpdater: (ForegroundHintState, Int, String?, String?, Long) -> ForegroundHintState
        get() = graph.foregroundObservationStore.foregroundHintUpdater
        set(value) {
            graph.foregroundObservationStore.foregroundHintUpdater = value
        }

    fun initialize(context: Context) {
        graph.runtimeLifecycle.initialize(context)
    }

    fun markServerRunning(
        host: String = AgentConstants.DEFAULT_HOST,
        port: Int = AgentConstants.DEFAULT_PORT,
    ) {
        graph.runtimeLifecycle.markServerRunning(host = host, port = port)
    }

    fun registerAccessibilityService(service: AccessibilityService) {
        graph.runtimeAttachmentAccess.registerAccessibilityService(service)
    }

    fun unregisterAccessibilityService() {
        graph.runtimeAttachmentAccess.unregisterAccessibilityService()
    }

    val currentAccessibilityService: () -> AccessibilityService? = { graph.runtimeAttachmentAccess.currentAccessibilityService() }

    val applicationContext: () -> Context? = { graph.contextStore.applicationContext() }

    @Synchronized
    fun recordObservedWindowState(
        eventType: Int,
        packageName: String?,
        windowClassName: String?,
    ) {
        graph.foregroundObservationWriter.recordObservedWindowState(
            eventType = eventType,
            packageName = packageName,
            windowClassName = windowClassName,
        )
    }

    @Synchronized
    fun resetForegroundObservationState() {
        graph.foregroundObservationWriter.reset()
    }

    internal val currentForegroundHintState: ForegroundHintState
        get() = graph.foregroundObservationStore.currentForegroundHintState

    internal val currentForegroundGeneration: Long
        get() = graph.foregroundObservationStore.currentForegroundGeneration

    internal fun currentRuntimeFacts(): RuntimeFacts = graph.factsStore.current()

    internal fun setRuntimeStateRecorderForTest(recorder: (AgentRuntimeState) -> Unit) {
        graph.statusStore.runtimeStateRecorder = recorder
    }

    internal fun currentAccessibilityAttachmentHandle(): AccessibilityAttachmentHandleSnapshot = graph.runtimeAttachmentAccess.snapshot()

    internal val runtimeLifecycleAccess: RuntimeLifecycle
        get() = graph.runtimeLifecycle

    internal val runtimeAttachmentAccess: RuntimeAttachmentAccess
        get() = graph.runtimeAttachmentAccess

    internal val runtimeAccessRole: RuntimeAccess
        get() = graph.runtimeAccess

    internal val runtimeStatusAccessRole: RuntimeStatusAccess
        get() = graph.runtimeStatusAccess

    internal val foregroundObservationStateAccessRole: ForegroundObservationStateAccess
        get() = graph.foregroundObservationStateAccess

    internal val foregroundObservationWriterRole: ForegroundObservationWriter
        get() = graph.foregroundObservationWriter

    internal fun resetForTest() {
        graph = AgentRuntimeGraph.create()
    }
}
