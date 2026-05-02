package com.rainng.androidctl.agent.snapshot

internal data class SnapshotGetRequest(
    val includeInvisible: Boolean,
    val includeSystemWindows: Boolean,
)
