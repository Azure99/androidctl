package com.rainng.androidctl.agent.service

import android.accessibilityservice.AccessibilityService
import com.rainng.androidctl.agent.events.DeviceEventHub
import com.rainng.androidctl.agent.logging.AgentFallbackDiagnostics
import com.rainng.androidctl.agent.runtime.AgentRuntimeBridge
import com.rainng.androidctl.agent.runtime.ForegroundObservationWriter
import com.rainng.androidctl.agent.runtime.RuntimeAttachmentAccess
import com.rainng.androidctl.agent.runtime.RuntimeLifecycle
import com.rainng.androidctl.agent.snapshot.SnapshotRegistry
import com.rainng.androidctl.agent.snapshot.SnapshotResetResult

internal interface AccessibilityAttachmentOwner {
    fun attach(service: AccessibilityService)

    fun detach()
}

internal object AccessibilityAttachmentCoordinator : AccessibilityAttachmentOwner {
    private var runtimeAttachmentControllerOverride: RuntimeAttachmentAccess? = null
    internal var runtimeAttachmentController: RuntimeAttachmentAccess
        get() = runtimeAttachmentControllerOverride ?: AgentRuntimeBridge.runtimeAttachmentAccess
        set(value) {
            runtimeAttachmentControllerOverride = value
        }

    private var runtimeLifecycleOverride: RuntimeLifecycle? = null
    internal var runtimeLifecycle: RuntimeLifecycle
        get() = runtimeLifecycleOverride ?: AgentRuntimeBridge.runtimeLifecycleAccess
        set(value) {
            runtimeLifecycleOverride = value
        }

    internal var snapshotRegistryReset: () -> SnapshotResetResult = { SnapshotRegistry.resetSessionState() }
    internal var eventPipelineReset: () -> Unit = DeviceEventHub::resetForAttachmentChange
    internal var snapshotResetTimeoutReporter: (SnapshotResetResult) -> Unit = ::logSnapshotResetTimeout
    private var foregroundObservationWriterOverride: ForegroundObservationWriter? = null
    internal var foregroundObservationWriter: ForegroundObservationWriter
        get() = foregroundObservationWriterOverride ?: AgentRuntimeBridge.foregroundObservationWriterRole
        set(value) {
            foregroundObservationWriterOverride = value
        }

    private var attachmentActive = false

    @Synchronized
    override fun attach(service: AccessibilityService) {
        if (attachmentActive) {
            runtimeAttachmentController.invalidateAccessibilityAttachmentHandle()
        }
        resetForAttachmentChange()
        runtimeAttachmentController.registerAccessibilityService(service)
        attachmentActive = true
    }

    @Synchronized
    override fun detach() {
        if (!attachmentActive) {
            return
        }
        runtimeAttachmentController.unregisterAccessibilityService()
        resetForAttachmentChange()
        attachmentActive = false
        runtimeLifecycle.reconcileRuntimeState()
    }

    @Synchronized
    fun resetForAttachmentChange() {
        val snapshotResetResult = snapshotRegistryReset()
        if (snapshotResetResult.timedOut) {
            snapshotResetTimeoutReporter(snapshotResetResult)
        }
        eventPipelineReset()
        foregroundObservationWriter.reset()
    }

    internal fun resetForTest() {
        runtimeAttachmentControllerOverride = null
        runtimeLifecycleOverride = null
        snapshotRegistryReset = { SnapshotRegistry.resetSessionState() }
        eventPipelineReset = DeviceEventHub::resetForAttachmentChange
        snapshotResetTimeoutReporter = ::logSnapshotResetTimeout
        foregroundObservationWriterOverride = null
        attachmentActive = false
    }

    private fun logSnapshotResetTimeout(result: SnapshotResetResult) {
        AgentFallbackDiagnostics.reporter.warn(
            key = "service.snapshot-reset.timeout",
            message =
                "snapshot reset timed out activePublications=${result.activePublicationCount} " +
                    "timeoutMs=${result.timeoutMs}",
        )
    }
}
