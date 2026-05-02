package com.rainng.androidctl.agent.rpc

import com.rainng.androidctl.agent.errors.RpcErrorCode

internal data class RpcMethodPolicy(
    val requiresReadyRuntime: Boolean = false,
    val requiresAccessibilityHandle: Boolean = false,
    val timeoutError: RpcErrorCode,
    val timeoutMessage: String,
)
