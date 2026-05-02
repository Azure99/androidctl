package com.rainng.androidctl.agent.events

sealed interface DeviceEventPayload {
    val wireType: String
}

data class RuntimeStatusPayload(
    val serverRunning: Boolean,
    val accessibilityEnabled: Boolean,
    val accessibilityConnected: Boolean,
    val runtimeReady: Boolean,
) : DeviceEventPayload {
    override val wireType: String = "runtime.status"
}

data class PackageChangedPayload(
    val packageName: String,
    val activityName: String?,
) : DeviceEventPayload {
    override val wireType: String = "package.changed"
}

data class WindowChangedPayload(
    val packageName: String?,
    val activityName: String?,
    val reason: String,
) : DeviceEventPayload {
    override val wireType: String = "window.changed"
}

data class FocusChangedPayload(
    val packageName: String?,
    val activityName: String?,
    val reason: String,
) : DeviceEventPayload {
    override val wireType: String = "focus.changed"
}

data class ImeChangedPayload(
    val visible: Boolean,
    val windowId: String?,
) : DeviceEventPayload {
    override val wireType: String = "ime.changed"
}

data class SnapshotInvalidatedPayload(
    val packageName: String?,
    val reason: String,
) : DeviceEventPayload {
    override val wireType: String = "snapshot.invalidated"
}
