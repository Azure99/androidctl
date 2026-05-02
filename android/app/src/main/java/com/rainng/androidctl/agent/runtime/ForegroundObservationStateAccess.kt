package com.rainng.androidctl.agent.runtime

internal interface ForegroundObservationStateAccess {
    fun foregroundHintState(): ForegroundHintState

    fun foregroundGeneration(): Long
}
