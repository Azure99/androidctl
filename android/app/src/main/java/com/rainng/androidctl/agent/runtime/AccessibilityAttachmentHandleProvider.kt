package com.rainng.androidctl.agent.runtime

import android.accessibilityservice.AccessibilityService

internal data class AccessibilityAttachmentHandleSnapshot(
    val service: AccessibilityService?,
    val generation: Long,
    val revoked: Boolean,
    val activationUptimeMillis: Long = 0L,
)

internal fun interface AccessibilityAttachmentHandleProvider {
    fun snapshot(): AccessibilityAttachmentHandleSnapshot
}
