package com.rainng.androidctl.agent.rpc

import com.rainng.androidctl.agent.errors.DeviceRpcException
import com.rainng.androidctl.agent.errors.RequestValidationException
import com.rainng.androidctl.agent.errors.RpcErrorCode
import org.json.JSONObject
import java.util.concurrent.CancellationException
import java.util.concurrent.ExecutionException
import java.util.concurrent.ExecutorService
import java.util.concurrent.Future
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.TimeUnit
import java.util.concurrent.TimeoutException

internal class RpcExecutionRunner(
    private val methodExecutor: ExecutorService,
) {
    private fun runRpc(
        id: String?,
        block: () -> JSONObject,
    ): String =
        try {
            RpcEnvelope.success(id = id, result = block())
        } catch (error: IllegalArgumentException) {
            rpcError(
                id,
                RequestValidationException(error.message ?: "request validation failed"),
            )
        } catch (error: DeviceRpcException) {
            rpcError(id, error)
        }

    fun runPrepared(
        id: String?,
        prepare: () -> PreparedRpcCall,
        timeoutError: DeviceRpcException,
    ): String =
        runRpc(id) {
            val preparedCall = prepare()
            runWithTimeout(
                timeoutMs = preparedCall.timeoutMs,
                timeoutError = timeoutError,
            ) {
                preparedCall.executeEncoded()
            }
        }

    private fun <T> runWithTimeout(
        timeoutMs: Long,
        timeoutError: DeviceRpcException,
        block: () -> T,
    ): T = awaitFuture(submitMethod(block), timeoutMs, timeoutError)

    fun timeoutError(
        code: RpcErrorCode,
        message: String,
    ): DeviceRpcException =
        DeviceRpcException(
            code = code,
            message = message,
            retryable = true,
        )

    private fun rpcError(
        id: String?,
        error: DeviceRpcException,
    ): String =
        RpcEnvelope.error(
            id = id,
            code = error.code,
            message = error.message,
            retryable = error.retryable,
        )

    private fun <T> submitMethod(block: () -> T): Future<T> =
        try {
            methodExecutor.submit<T> { block() }
        } catch (rejected: RejectedExecutionException) {
            throw serverBusyException(rejected)
        }

    private fun <T> awaitFuture(
        future: Future<T>,
        timeoutMs: Long,
        timeoutError: DeviceRpcException,
    ): T =
        try {
            future.get(timeoutMs, TimeUnit.MILLISECONDS)
        } catch (timeout: TimeoutException) {
            future.cancel(true)
            throw timeoutError.withCause(timeout)
        } catch (error: InterruptedException) {
            future.cancel(true)
            Thread.currentThread().interrupt()
            throw DeviceRpcException(
                code = RpcErrorCode.INTERNAL_ERROR,
                message = "request execution interrupted",
                retryable = true,
            ).withCause(error)
        } catch (error: CancellationException) {
            throw DeviceRpcException(
                code = RpcErrorCode.INTERNAL_ERROR,
                message = "request execution cancelled",
                retryable = true,
            ).withCause(error)
        } catch (error: ExecutionException) {
            throw unwrapExecutionFailure(error, timeoutError)
        }

    private fun serverBusyException(error: RejectedExecutionException): DeviceRpcException =
        DeviceRpcException(
            code = RpcErrorCode.INTERNAL_ERROR,
            message = "server is busy",
            retryable = true,
        ).apply {
            initCause(error)
        }

    private fun unwrapExecutionFailure(
        error: ExecutionException,
        timeoutError: DeviceRpcException,
    ): DeviceRpcException {
        val cause = error.cause
        return when (cause) {
            is DeviceRpcException -> cause
            is TimeoutException -> timeoutError.withCause(cause)
            is IllegalArgumentException ->
                RequestValidationException(
                    cause.message ?: "request validation failed",
                )
            else ->
                DeviceRpcException(
                    code = RpcErrorCode.INTERNAL_ERROR,
                    message = cause?.message ?: "unexpected internal error",
                    retryable = true,
                )
        }
    }

    private fun DeviceRpcException.withCause(cause: Throwable): DeviceRpcException =
        DeviceRpcException(
            code = code,
            message = if (cause.message.isNullOrBlank()) message else "$message: ${cause.message}",
            retryable = retryable,
        ).apply {
            initCause(cause)
        }
}
