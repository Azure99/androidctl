package com.rainng.androidctl.agent.events

data class DeviceEvent(
    val seq: Long,
    val timestamp: String,
    val data: DeviceEventPayload,
) {
    val type: String
        get() = data.wireType
}

data class EventPollRequest(
    val afterSeq: Long,
    val waitMs: Long,
    val limit: Int,
)

data class EventPollResult(
    val events: List<DeviceEvent>,
    val latestSeq: Long,
    val needResync: Boolean,
    val timedOut: Boolean,
)

data class AccessibilityObservation(
    val eventType: Int,
    val generation: Long,
    val packageName: String?,
    val activityName: String?,
    val imeVisible: Boolean,
    val imeWindowId: String?,
)

data class ImeState(
    val visible: Boolean,
    val windowId: String?,
)
