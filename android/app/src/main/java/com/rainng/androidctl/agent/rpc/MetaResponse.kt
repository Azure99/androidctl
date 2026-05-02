package com.rainng.androidctl.agent.rpc

internal data class MetaResponse(
    val service: String,
    val version: String,
    val capabilities: MetaCapabilities,
)

internal data class MetaCapabilities(
    val supportsEventsPoll: Boolean,
    val supportsScreenshot: Boolean,
    val actionKinds: List<String>,
)
