package com.rainng.androidctl.agent.service

internal interface DeviceAccessibilityLifecycleGuard {
    fun markRuntimeActive()

    fun teardownRuntimeAttachment(attachmentOwner: AccessibilityAttachmentOwner)
}

internal class RuntimeAttachmentLifecycleGuard : DeviceAccessibilityLifecycleGuard {
    private var runtimeActive = false

    override fun markRuntimeActive() {
        runtimeActive = true
    }

    override fun teardownRuntimeAttachment(attachmentOwner: AccessibilityAttachmentOwner) {
        if (!runtimeActive) {
            return
        }
        attachmentOwner.detach()
        runtimeActive = false
    }
}
