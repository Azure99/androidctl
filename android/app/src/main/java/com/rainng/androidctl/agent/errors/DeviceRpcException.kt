package com.rainng.androidctl.agent.errors

open class DeviceRpcException(
    val code: RpcErrorCode,
    override val message: String,
    val retryable: Boolean,
) : RuntimeException(message)

class RequestValidationException(
    message: String,
    val requestId: String? = null,
) : DeviceRpcException(
        code = RpcErrorCode.INVALID_REQUEST,
        message = message,
        retryable = false,
    )
