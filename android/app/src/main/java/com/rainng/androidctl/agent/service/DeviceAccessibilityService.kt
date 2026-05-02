package com.rainng.androidctl.agent.service

import android.accessibilityservice.AccessibilityService
import com.rainng.androidctl.agent.events.AccessibilityEventIngress
import com.rainng.androidctl.agent.events.BridgeAccessibilityEventIngress
import com.rainng.androidctl.agent.events.DeviceEventHub
import com.rainng.androidctl.agent.logging.AgentLog
import com.rainng.androidctl.agent.runtime.AgentRuntimeBridge

class DeviceAccessibilityService : AccessibilityService() {
    internal var runtimeInitializer: (android.content.Context) -> Unit = AgentRuntimeBridge::initialize
    internal var frameworkHooks: FrameworkHooks = AndroidFrameworkHooks
    internal var attachmentCoordinator: AccessibilityAttachmentOwner = AccessibilityAttachmentCoordinator
    internal var lifecycleGuard: DeviceAccessibilityLifecycleGuard = RuntimeAttachmentLifecycleGuard()
    internal var eventIngress: AccessibilityEventIngress = BridgeAccessibilityEventIngress()
    internal var eventHubShutdown: () -> Unit = DeviceEventHub::shutdown

    override fun onServiceConnected() {
        frameworkHooks.onServiceConnectedSuper(this)
        runtimeInitializer(frameworkHooks.runtimeContext(this))
        attachmentCoordinator.attach(this)
        lifecycleGuard.markRuntimeActive()
        frameworkHooks.info("Accessibility service connected")
    }

    override fun onAccessibilityEvent(event: android.view.accessibility.AccessibilityEvent?) {
        eventIngress.record(this, event)
    }

    override fun onInterrupt() {
        frameworkHooks.warning("Accessibility service interrupted")
    }

    override fun onUnbind(intent: android.content.Intent?): Boolean {
        teardownRuntimeAttachment()
        frameworkHooks.info("Accessibility service unbound")
        return frameworkHooks.onUnbindSuper(this, intent)
    }

    override fun onDestroy() {
        teardownRuntimeAttachment()
        eventHubShutdown()
        frameworkHooks.info("Accessibility service destroyed")
        frameworkHooks.onDestroySuper(this)
    }

    private fun teardownRuntimeAttachment() {
        lifecycleGuard.teardownRuntimeAttachment(attachmentCoordinator)
    }

    internal fun invokeSuperOnServiceConnected() {
        super.onServiceConnected()
    }

    internal fun invokeSuperOnDestroy() {
        super.onDestroy()
    }

    internal fun invokeSuperOnUnbind(intent: android.content.Intent?): Boolean = super.onUnbind(intent)

    internal interface FrameworkHooks {
        fun runtimeContext(service: DeviceAccessibilityService): android.content.Context

        fun info(message: String)

        fun warning(message: String)

        fun onServiceConnectedSuper(service: DeviceAccessibilityService)

        fun onUnbindSuper(
            service: DeviceAccessibilityService,
            intent: android.content.Intent?,
        ): Boolean

        fun onDestroySuper(service: DeviceAccessibilityService)
    }

    private object AndroidFrameworkHooks : FrameworkHooks {
        override fun runtimeContext(service: DeviceAccessibilityService): android.content.Context = service.applicationContext

        override fun info(message: String) {
            AgentLog.i(message)
        }

        override fun warning(message: String) {
            AgentLog.w(message)
        }

        override fun onServiceConnectedSuper(service: DeviceAccessibilityService) {
            service.invokeSuperOnServiceConnected()
        }

        override fun onUnbindSuper(
            service: DeviceAccessibilityService,
            intent: android.content.Intent?,
        ): Boolean = service.invokeSuperOnUnbind(intent)

        override fun onDestroySuper(service: DeviceAccessibilityService) {
            service.invokeSuperOnDestroy()
        }
    }
}
