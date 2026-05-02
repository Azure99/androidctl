package com.rainng.androidctl.agent.device

internal data class AppsListResponse(
    val apps: List<AppEntryResponse>,
)

internal data class AppEntryResponse(
    val packageName: String,
    val appLabel: String,
    val launchable: Boolean,
)
