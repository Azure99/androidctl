package com.rainng.androidctl.agent.snapshot

import com.rainng.androidctl.agent.errors.DeviceRpcException
import com.rainng.androidctl.agent.errors.RpcErrorCode

class SnapshotException(
    code: RpcErrorCode,
    message: String,
    retryable: Boolean,
) : DeviceRpcException(code, message, retryable)
