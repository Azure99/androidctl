package com.rainng.androidctl.agent.runtime

import android.accessibilityservice.AccessibilityService
import com.rainng.androidctl.agent.events.CooldownScheduler
import com.rainng.androidctl.agent.events.ExecutorCooldownScheduler
import com.rainng.androidctl.agent.events.ScheduledTask

internal interface RuntimeAttachmentAccess : AccessibilityAttachmentHandleProvider {
    fun registerAccessibilityService(service: AccessibilityService)

    fun unregisterAccessibilityService()

    fun invalidateAccessibilityAttachmentHandle()

    fun currentAccessibilityService(): AccessibilityService?
}

internal class RuntimeAttachmentController(
    private val contextStore: RuntimeContextStore,
    private val statusStore: RuntimeStatusStore,
    private val mutationLock: RuntimeMutationLock,
    private val refreshRuntimeInputs: (Boolean, AgentRuntimeState) -> Unit,
    private val verificationScheduler: CooldownScheduler = ExecutorCooldownScheduler(),
    private val verificationDelaysMs: LongArray = DEFAULT_VERIFICATION_DELAYS_MS,
) : RuntimeAttachmentAccess {
    private var pendingVerificationTask: ScheduledTask? = null
    private var pendingVerificationGeneration: Long? = null

    override fun registerAccessibilityService(service: AccessibilityService) {
        mutationLock.synchronize {
            cancelPendingVerificationLocked()
            contextStore.registerAccessibilityService(service)
            refreshRuntimeInputs(
                true,
                statusStore.currentState().clearTransitionErrorState(),
            )
            schedulePostAttachVerificationLocked(
                generation = contextStore.currentAccessibilityAttachmentHandle().generation,
                delayIndex = 0,
            )
        }
    }

    override fun unregisterAccessibilityService() {
        mutationLock.synchronize {
            cancelPendingVerificationLocked()
            contextStore.unregisterAccessibilityService()
            refreshRuntimeInputs(false, statusStore.currentState())
        }
    }

    override fun invalidateAccessibilityAttachmentHandle() {
        mutationLock.synchronize {
            cancelPendingVerificationLocked()
            contextStore.invalidateAccessibilityAttachmentHandle()
        }
    }

    override fun currentAccessibilityService(): AccessibilityService? = snapshot().service

    override fun snapshot(): AccessibilityAttachmentHandleSnapshot = contextStore.currentAccessibilityAttachmentHandle()

    private fun schedulePostAttachVerificationLocked(
        generation: Long,
        delayIndex: Int,
    ) {
        if (delayIndex >= verificationDelaysMs.size) {
            pendingVerificationGeneration = null
            return
        }
        val handle = contextStore.currentAccessibilityAttachmentHandle()
        if (handle.revoked || handle.service == null || handle.generation != generation) {
            pendingVerificationGeneration = null
            return
        }
        if (statusStore.currentState().accessibilityEnabled) {
            pendingVerificationGeneration = null
            return
        }

        pendingVerificationGeneration = generation
        pendingVerificationTask =
            verificationScheduler.schedule(verificationDelaysMs[delayIndex]) {
                mutationLock.synchronize {
                    val currentHandle = contextStore.currentAccessibilityAttachmentHandle()
                    if (
                        currentHandle.revoked ||
                        currentHandle.service == null ||
                        currentHandle.generation != generation
                    ) {
                        if (pendingVerificationGeneration == generation) {
                            pendingVerificationGeneration = null
                            pendingVerificationTask = null
                        }
                        return@synchronize
                    }

                    refreshRuntimeInputs(
                        true,
                        statusStore.currentState(),
                    )
                    if (statusStore.currentState().accessibilityEnabled) {
                        pendingVerificationGeneration = null
                        pendingVerificationTask = null
                        return@synchronize
                    }

                    pendingVerificationTask = null
                    schedulePostAttachVerificationLocked(
                        generation = generation,
                        delayIndex = delayIndex + 1,
                    )
                }
            }
    }

    private fun cancelPendingVerificationLocked() {
        pendingVerificationTask?.cancel()
        pendingVerificationTask = null
        pendingVerificationGeneration = null
    }

    private companion object {
        val DEFAULT_VERIFICATION_DELAYS_MS: LongArray = longArrayOf(100L, 300L, 1000L)
    }
}
