package com.rainng.androidctl.agent.service

import android.accessibilityservice.AccessibilityService
import org.junit.Assert.assertEquals
import org.junit.Test

class RuntimeAttachmentLifecycleGuardTest {
    @Test
    fun teardownRuntimeAttachmentDetachesOnlyOnceAfterMarkedActive() {
        val guard = RuntimeAttachmentLifecycleGuard()
        val attachmentOwner = RecordingAttachmentOwner()

        guard.markRuntimeActive()
        guard.teardownRuntimeAttachment(attachmentOwner)
        guard.teardownRuntimeAttachment(attachmentOwner)

        assertEquals(1, attachmentOwner.detachCalls)
    }

    @Test
    fun teardownRuntimeAttachmentDoesNothingBeforeServiceBecomesActive() {
        val guard = RuntimeAttachmentLifecycleGuard()
        val attachmentOwner = RecordingAttachmentOwner()

        guard.teardownRuntimeAttachment(attachmentOwner)

        assertEquals(0, attachmentOwner.detachCalls)
    }

    private class RecordingAttachmentOwner : AccessibilityAttachmentOwner {
        var detachCalls = 0

        override fun attach(service: AccessibilityService) = Unit

        override fun detach() {
            detachCalls += 1
        }
    }
}
