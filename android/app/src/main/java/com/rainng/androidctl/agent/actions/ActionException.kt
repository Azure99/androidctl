package com.rainng.androidctl.agent.actions

import com.rainng.androidctl.agent.errors.DeviceRpcException
import com.rainng.androidctl.agent.errors.RpcErrorCode

class ActionException(
    code: RpcErrorCode,
    message: String,
    retryable: Boolean,
) : DeviceRpcException(code, message, retryable)
