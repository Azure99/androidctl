package com.rainng.androidctl.agent.events

import android.accessibilityservice.AccessibilityService
import android.view.accessibility.AccessibilityEvent
import com.rainng.androidctl.agent.runtime.AccessibilityAttachmentHandleSnapshot
import com.rainng.androidctl.agent.runtime.AgentRuntimeBridge
import com.rainng.androidctl.agent.runtime.ForegroundObservationWriter

internal interface AccessibilityEventIngress {
    fun record(
        service: AccessibilityService,
        event: AccessibilityEvent?,
    )
}

internal class BridgeAccessibilityEventIngress(
    private val observationPolicy: (Int) -> Boolean = DeviceEventObservationPolicy::shouldObserveEvent,
    private val attachmentHandleProvider: () -> AccessibilityAttachmentHandleSnapshot =
        AgentRuntimeBridge::currentAccessibilityAttachmentHandle,
    foregroundObservationWriter: ForegroundObservationWriter? = null,
    private val foregroundObservationWriterProvider: () -> ForegroundObservationWriter =
        { foregroundObservationWriter ?: AgentRuntimeBridge.foregroundObservationWriterRole },
    private val environmentCapture: (AccessibilityService) -> DeviceEventEnvironment =
        AccessibilityServiceDeviceEventEnvironment::capture,
    private val eventSink: (ObservedAccessibilityEvent, DeviceEventEnvironment) -> Unit =
        DeviceEventHub::recordAccessibilityObservation,
) : AccessibilityEventIngress {
    override fun record(
        service: AccessibilityService,
        event: AccessibilityEvent?,
    ) {
        if (event == null) {
            return
        }
        if (!observationPolicy(event.eventType)) {
            return
        }
        if (!isCurrentLiveAttachment(service, event)) {
            return
        }
        foregroundObservationWriterProvider().recordObservedWindowState(
            eventType = event.eventType,
            packageName = event.packageName?.toString(),
            windowClassName = event.className?.toString(),
        )
        eventSink(
            ObservedAccessibilityEvent(
                eventType = event.eventType,
            ),
            environmentCapture(service),
        )
    }

    private fun isCurrentLiveAttachment(
        service: AccessibilityService,
        event: AccessibilityEvent,
    ): Boolean {
        val attachmentHandle = attachmentHandleProvider()
        return !attachmentHandle.revoked &&
            attachmentHandle.service === service &&
            event.eventTime >= attachmentHandle.activationUptimeMillis
    }
}
