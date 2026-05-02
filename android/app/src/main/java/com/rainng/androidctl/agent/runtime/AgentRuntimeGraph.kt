package com.rainng.androidctl.agent.runtime

import com.rainng.androidctl.agent.events.DeviceEventHub
import com.rainng.androidctl.agent.events.RuntimeStatusPayload
import com.rainng.androidctl.agent.logging.AgentLog

@Suppress("LongParameterList")
internal class AgentRuntimeGraph internal constructor(
    val contextStore: RuntimeContextStore,
    val factsStore: RuntimeFactsStore,
    val statusStore: RuntimeStatusStore,
    val foregroundObservationStore: ForegroundObservationStore,
    val deviceTokenCoordinator: DeviceTokenCoordinator,
    val runtimeCoordinator: RuntimeCoordinator,
    val runtimeAttachmentAccess: RuntimeAttachmentAccess,
    val runtimeAccess: RuntimeAccess,
    val runtimeStatusAccess: RuntimeStatusAccess,
    val foregroundObservationStateAccess: ForegroundObservationStateAccess,
    val foregroundObservationWriter: ForegroundObservationWriter,
) {
    val runtimeLifecycle: RuntimeLifecycle
        get() = runtimeCoordinator

    companion object {
        fun create(): AgentRuntimeGraph = AgentRuntimeGraphFactory().create()
    }
}

private class AgentRuntimeGraphFactory {
    fun create(): AgentRuntimeGraph {
        val contextStore = RuntimeContextStore()
        val factsStore = RuntimeFactsStore()
        val foregroundObservationStore = ForegroundObservationStore()
        val statusStore =
            RuntimeStatusStore(
                runtimeStateRecorder = {},
                runtimeEventPublisher = createRuntimeEventPublisher(factsStore),
            )
        val deviceTokenCoordinator = DeviceTokenCoordinator(defaultDeviceTokenStoreAccess())
        val mutationLock = RuntimeMutationLock()
        val collaborators =
            createRuntimeCoordinatorCollaborators(
                factsStore = factsStore,
                statusStore = statusStore,
                foregroundObservationStore = foregroundObservationStore,
                deviceTokenCoordinator = deviceTokenCoordinator,
            )
        val runtimeCoordinator =
            RuntimeCoordinator(
                contextStore = contextStore,
                factsStore = factsStore,
                statusStore = statusStore,
                deviceTokenCoordinator = deviceTokenCoordinator,
                mutationLock = mutationLock,
                collaborators = collaborators,
            )
        val runtimeAttachmentAccess =
            RuntimeAttachmentController(
                contextStore = contextStore,
                statusStore = statusStore,
                mutationLock = mutationLock,
                refreshRuntimeInputs = runtimeCoordinator::refreshRuntimeInputs,
            )
        return AgentRuntimeGraph(
            contextStore = contextStore,
            factsStore = factsStore,
            statusStore = statusStore,
            foregroundObservationStore = foregroundObservationStore,
            deviceTokenCoordinator = deviceTokenCoordinator,
            runtimeCoordinator = runtimeCoordinator,
            runtimeAttachmentAccess = runtimeAttachmentAccess,
            runtimeAccess =
                createRuntimeAccess(
                    contextStore = contextStore,
                    factsStore = factsStore,
                    runtimeAttachmentAccess = runtimeAttachmentAccess,
                ),
            runtimeStatusAccess =
                GraphRuntimeStatusAccess(
                    state = statusStore.state,
                    runtimeLifecycle = runtimeCoordinator,
                ),
            foregroundObservationStateAccess = foregroundObservationStore,
            foregroundObservationWriter = createForegroundObservationWriter(runtimeCoordinator),
        )
    }

    private fun createRuntimeEventPublisher(factsStore: RuntimeFactsStore): () -> Unit =
        { DeviceEventHub.recordRuntimeStatus(factsStore.current().toRuntimeStatusPayload()) }

    private fun createRuntimeCoordinatorCollaborators(
        factsStore: RuntimeFactsStore,
        statusStore: RuntimeStatusStore,
        foregroundObservationStore: ForegroundObservationStore,
        deviceTokenCoordinator: DeviceTokenCoordinator,
    ): RuntimeCoordinatorCollaborators =
        RuntimeCoordinatorCollaborators(
            authCoordinator =
                RuntimeAuthCoordinator(
                    factsStore = factsStore,
                    statusStore = statusStore,
                    deviceTokenCoordinator = deviceTokenCoordinator,
                ),
            probeReconciler =
                RuntimeProbeReconciler(
                    accessibilityServiceEnabledProbe = ::defaultIsAccessibilityServiceEnabled,
                    serverRunningProbe = ::defaultIsAgentServerRunning,
                    warningLogger = AgentLog::w,
                ),
            foregroundObservationManager =
                ForegroundObservationManager(
                    factsStore = factsStore,
                    foregroundObservationStore = foregroundObservationStore,
                ),
        )

    private fun createRuntimeAccess(
        contextStore: RuntimeContextStore,
        factsStore: RuntimeFactsStore,
        runtimeAttachmentAccess: RuntimeAttachmentAccess,
    ): RuntimeAccess =
        GraphRuntimeAccess(
            runtimeFactsProvider = factsStore::current,
            applicationContextProvider = contextStore::applicationContext,
            attachmentHandleProvider = runtimeAttachmentAccess,
        )

    private fun createForegroundObservationWriter(runtimeCoordinator: RuntimeCoordinator): ForegroundObservationWriter =
        GraphForegroundObservationWriter(
            recordObservedWindowStateAction = runtimeCoordinator::recordObservedWindowState,
            resetAction = runtimeCoordinator::resetForegroundObservationState,
        )
}

internal fun RuntimeFacts.toRuntimeStatusPayload(): RuntimeStatusPayload =
    RuntimeStatusPayload(
        serverRunning = serverPhase == ServerPhase.RUNNING,
        accessibilityEnabled = accessibilityEnabled,
        accessibilityConnected = accessibilityEnabled && accessibilityAttached,
        runtimeReady = RuntimeReadiness.fromFacts(this).ready,
    )
