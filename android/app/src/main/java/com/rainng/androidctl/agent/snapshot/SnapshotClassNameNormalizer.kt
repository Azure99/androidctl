package com.rainng.androidctl.agent.snapshot

internal const val DEFAULT_SNAPSHOT_CLASS_NAME = "android.view.View"

internal fun normalizeSnapshotClassName(value: String?): String {
    val normalized = value?.trim()
    return if (normalized.isNullOrEmpty()) {
        DEFAULT_SNAPSHOT_CLASS_NAME
    } else {
        normalized
    }
}
