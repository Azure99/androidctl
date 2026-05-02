package com.rainng.androidctl.agent.rpc

import com.rainng.androidctl.agent.RequestBudgets
import com.rainng.androidctl.agent.errors.DeviceRpcException
import com.rainng.androidctl.agent.errors.RequestValidationException
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

internal interface RpcRequestDelegate {
    fun handle(
        headers: Map<String, String>,
        rawBody: String,
    ): String

    fun shutdown(force: Boolean)
}

internal fun interface RpcShutdownResource {
    fun shutdown(force: Boolean)
}

internal class RpcRequestHandler(
    private val authorizationGate: RpcAuthorizationGate,
    private val dispatcher: RpcDispatcher,
    private val methodExecutor: ExecutorService = newMethodExecutor(),
    private val shutdownResources: List<RpcShutdownResource> = emptyList(),
) : RpcRequestDelegate {
    private val methodShutdownStarted = AtomicBoolean(false)
    private val methodForceShutdownStarted = AtomicBoolean(false)

    private data class ParsedRequestResult(
        val request: RpcRequestEnvelope? = null,
        val errorResponse: String? = null,
    )

    override fun handle(
        headers: Map<String, String>,
        rawBody: String,
    ): String {
        val parsedRequest = parseRequest(rawBody)
        val request = parsedRequest.request
        return parsedRequest.errorResponse
            ?: authorizationGate.authorize(request?.id, headers)
            ?: dispatcher.dispatch(checkNotNull(request))
    }

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

    private fun parseRequest(rawBody: String): ParsedRequestResult =
        try {
            ParsedRequestResult(request = RpcRequestParser.parse(rawBody))
        } catch (error: RequestValidationException) {
            ParsedRequestResult(errorResponse = rpcError(id = error.requestId, error = error))
        }

    override fun shutdown(force: Boolean) {
        if (force) {
            shutdownOwnedResources(force = true)
        }
        shutdownMethodExecutor(force)
        if (!force) {
            shutdownOwnedResources(force = false)
        }
    }

    private fun shutdownMethodExecutor(force: Boolean) {
        if (methodShutdownStarted.compareAndSet(false, true)) {
            methodExecutor.shutdown()
        }
        val terminated =
            try {
                methodExecutor.awaitTermination(HANDLER_SHUTDOWN_GRACE_MS, TimeUnit.MILLISECONDS)
            } catch (_: InterruptedException) {
                Thread.currentThread().interrupt()
                false
            }
        if (!terminated && force && methodForceShutdownStarted.compareAndSet(false, true)) {
            methodExecutor.shutdownNow()
        }
    }

    private fun shutdownOwnedResources(force: Boolean) {
        shutdownResources.forEach { resource ->
            resource.shutdown(force)
        }
    }

    companion object {
        internal fun newMethodExecutor(): ExecutorService =
            ThreadPoolExecutor(
                RequestBudgets.METHOD_EXECUTOR_CORE_POOL_SIZE,
                RequestBudgets.METHOD_EXECUTOR_MAX_POOL_SIZE,
                RequestBudgets.METHOD_EXECUTOR_KEEP_ALIVE_SECONDS,
                TimeUnit.SECONDS,
                LinkedBlockingQueue(RequestBudgets.METHOD_EXECUTOR_QUEUE_CAPACITY),
                Executors.defaultThreadFactory(),
                ThreadPoolExecutor.AbortPolicy(),
            ).apply {
                allowCoreThreadTimeOut(true)
            }

        private const val HANDLER_SHUTDOWN_GRACE_MS = 100L
    }
}
