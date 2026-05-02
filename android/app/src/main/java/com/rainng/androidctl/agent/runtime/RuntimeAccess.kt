package com.rainng.androidctl.agent.runtime

import android.accessibilityservice.AccessibilityService
import android.content.Context

internal interface RuntimeAccess {
    fun readiness(): RuntimeReadiness

    fun currentDeviceToken(): String

    fun applicationContext(): Context?

    fun currentAccessibilityService(): AccessibilityService?

    fun currentAccessibilityAttachmentHandle(): AccessibilityAttachmentHandleSnapshot =
        AccessibilityAttachmentHandleSnapshot(
            service = currentAccessibilityService(),
            generation = 0L,
            revoked = false,
        )
}

internal class GraphRuntimeAccess(
    private val runtimeFactsProvider: () -> RuntimeFacts,
    private val readinessProjection: (RuntimeFacts) -> RuntimeReadiness = RuntimeReadiness::fromFacts,
    private val applicationContextProvider: () -> Context?,
    private val attachmentHandleProvider: AccessibilityAttachmentHandleProvider,
) : RuntimeAccess {
    override fun readiness(): RuntimeReadiness = readinessProjection(runtimeFactsProvider())

    override fun currentDeviceToken(): String = runtimeFactsProvider().auth.currentToken.orEmpty()

    override fun applicationContext(): Context? = applicationContextProvider()

    override fun currentAccessibilityAttachmentHandle(): AccessibilityAttachmentHandleSnapshot = attachmentHandleProvider.snapshot()

    override fun currentAccessibilityService(): AccessibilityService? =
        currentAccessibilityAttachmentHandle()
            .takeUnless(AccessibilityAttachmentHandleSnapshot::revoked)
            ?.service
}
