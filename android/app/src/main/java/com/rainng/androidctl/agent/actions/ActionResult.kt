package com.rainng.androidctl.agent.actions

import com.rainng.androidctl.agent.runtime.ObservedWindowState

internal enum class ActionResultStatus(
    val wireName: String,
) {
    Done("done"),
    Partial("partial"),
}

internal data class ActionResult(
    val actionId: String,
    val status: ActionResultStatus,
    val durationMs: Long,
    val resolvedTarget: ActionTarget,
    val observed: ObservedWindowState,
)
