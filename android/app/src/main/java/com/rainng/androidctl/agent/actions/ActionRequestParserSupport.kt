package com.rainng.androidctl.agent.actions

import com.rainng.androidctl.agent.errors.RpcErrorCode

internal fun requireTapTarget(
    target: ActionTarget,
    message: String,
): ActionTarget =
    when (target) {
        is ActionTarget.Handle, is ActionTarget.Coordinates -> target
        ActionTarget.None -> throw invalidRequest(message)
    }

internal fun requireHandleTarget(
    target: ActionTarget,
    message: String,
): ActionTarget.Handle = target as? ActionTarget.Handle ?: throw invalidRequest(message)

internal fun requireNoneTarget(
    target: ActionTarget,
    message: String,
): ActionTarget.None =
    if (target == ActionTarget.None) {
        ActionTarget.None
    } else {
        throw invalidRequest(message)
    }

internal fun invalidRequest(message: String): ActionException =
    ActionException(
        code = RpcErrorCode.INVALID_REQUEST,
        message = message,
        retryable = false,
    )
