package com.rainng.androidctl.agent

internal object WindowIds {
    fun fromPlatformWindowId(id: Int): String = "w$id"

    fun matchesPlatformWindow(
        opaqueWindowId: String,
        platformWindowId: Int,
    ): Boolean = opaqueWindowId == fromPlatformWindowId(platformWindowId)
}
