package com.rainng.androidctl.agent.runtime

import android.accessibilityservice.AccessibilityService
import android.content.Context
import android.os.SystemClock

internal class RuntimeContextStore(
    private val activationUptimeSource: () -> Long = ::defaultActivationUptimeMillis,
) {
    @Volatile
    private var appContext: Context? = null

    @Volatile
    private var lastRegisteredAccessibilityService: AccessibilityService? = null

    @Volatile
    private var lastRegisteredActivationUptimeMillis: Long = 0L

    @Volatile
    private var accessibilityAttachmentHandle =
        AccessibilityAttachmentHandleSnapshot(
            service = null,
            generation = 0L,
            revoked = false,
        )

    fun setApplicationContext(context: Context) {
        appContext = context
    }

    fun applicationContext(): Context? = appContext

    @Synchronized
    fun registerAccessibilityService(service: AccessibilityService) {
        val activationUptimeMillis = nextRegistrationActivationUptimeMillis(service)
        accessibilityAttachmentHandle =
            AccessibilityAttachmentHandleSnapshot(
                service = service,
                generation = accessibilityAttachmentHandle.generation + 1L,
                revoked = false,
                activationUptimeMillis = activationUptimeMillis,
            )
        lastRegisteredAccessibilityService = service
        lastRegisteredActivationUptimeMillis = activationUptimeMillis
    }

    @Synchronized
    fun unregisterAccessibilityService() {
        accessibilityAttachmentHandle =
            AccessibilityAttachmentHandleSnapshot(
                service = null,
                generation = accessibilityAttachmentHandle.generation + 1L,
                revoked = true,
                activationUptimeMillis = activationUptimeSource(),
            )
    }

    @Synchronized
    fun invalidateAccessibilityAttachmentHandle() {
        accessibilityAttachmentHandle =
            AccessibilityAttachmentHandleSnapshot(
                service = null,
                generation = accessibilityAttachmentHandle.generation + 1L,
                revoked = true,
                activationUptimeMillis = activationUptimeSource(),
            )
    }

    fun currentAccessibilityAttachmentHandle(): AccessibilityAttachmentHandleSnapshot = accessibilityAttachmentHandle

    private fun nextRegistrationActivationUptimeMillis(service: AccessibilityService): Long {
        val activationUptimeMillis = activationUptimeSource()
        val isSameInstanceReattachWithoutClockAdvance =
            accessibilityAttachmentHandle.revoked &&
                lastRegisteredAccessibilityService === service &&
                activationUptimeMillis <= lastRegisteredActivationUptimeMillis
        return if (isSameInstanceReattachWithoutClockAdvance) {
            lastRegisteredActivationUptimeMillis + 1L
        } else {
            activationUptimeMillis
        }
    }

    fun reset() {
        appContext = null
        lastRegisteredAccessibilityService = null
        lastRegisteredActivationUptimeMillis = 0L
        accessibilityAttachmentHandle =
            AccessibilityAttachmentHandleSnapshot(
                service = null,
                generation = 0L,
                revoked = false,
            )
    }

    private companion object {
        private const val NANOS_PER_MILLISECOND = 1_000_000L

        fun defaultActivationUptimeMillis(): Long =
            try {
                SystemClock.uptimeMillis()
            } catch (_: RuntimeException) {
                System.nanoTime() / NANOS_PER_MILLISECOND
            }
    }
}
