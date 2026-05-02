package com.rainng.androidctl.agent.rpc

import android.accessibilityservice.AccessibilityService
import com.rainng.androidctl.agent.errors.DeviceRpcException
import com.rainng.androidctl.agent.errors.RequestValidationException
import com.rainng.androidctl.agent.errors.RpcErrorCode
import com.rainng.androidctl.agent.runtime.AccessibilityAttachmentHandleSnapshot
import com.rainng.androidctl.agent.runtime.RuntimeAccess
import com.rainng.androidctl.agent.runtime.RuntimeReadiness
import com.rainng.androidctl.agent.screenshot.ScreenshotException
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.mockito.Mockito.mock
import java.util.concurrent.AbstractExecutorService
import java.util.concurrent.Callable
import java.util.concurrent.Future
import java.util.concurrent.TimeUnit
import java.util.concurrent.TimeoutException

class RpcDispatcherTest {
    @Test
    fun unknownMethodReturnsInvalidRequest() {
        val payload = dispatch(emptyList(), request(method = "unknown.method"))

        assertEquals(false, payload.getBoolean("ok"))
        assertEquals("INVALID_REQUEST", payload.getJSONObject("error").getString("code"))
    }

    @Test
    fun runtimeBlockedMethodReturnsAccessibilityDisabled() {
        val method =
            fakeMethod(
                name = "snapshot.get",
                policy =
                    RpcMethodPolicy(
                        requiresReadyRuntime = true,
                        timeoutError = RpcErrorCode.SNAPSHOT_UNAVAILABLE,
                        timeoutMessage = "snapshot.get timed out",
                    ),
            )

        val payload =
            dispatch(
                methods = listOf(method),
                request = request(method = "snapshot.get"),
                readiness = RuntimeReadiness(accessibilityEnabled = false, accessibilityConnected = false),
            )

        assertEquals(false, payload.getBoolean("ok"))
        assertEquals("ACCESSIBILITY_DISABLED", payload.getJSONObject("error").getString("code"))
    }

    @Test
    fun runtimeBlockedMethodDoesNotPrepareMalformedRequest() {
        var prepareCount = 0
        val method =
            object : DeviceRpcMethod {
                override val name: String = "action.perform"
                override val policy: RpcMethodPolicy =
                    RpcMethodPolicy(
                        requiresReadyRuntime = true,
                        timeoutError = RpcErrorCode.ACTION_TIMEOUT,
                        timeoutMessage = "action.perform timed out",
                    )

                override fun prepare(request: RpcRequestEnvelope): PreparedRpcCall {
                    prepareCount += 1
                    error("blocked methods must not prepare malformed requests")
                }
            }

        val payload =
            dispatch(
                methods = listOf(method),
                request = request(method = "action.perform", params = JSONObject().put("kind", 123)),
                readiness = RuntimeReadiness(accessibilityEnabled = false, accessibilityConnected = false),
            )

        assertEquals(false, payload.getBoolean("ok"))
        assertEquals("ACCESSIBILITY_DISABLED", payload.getJSONObject("error").getString("code"))
        assertEquals(0, prepareCount)
    }

    @Test
    fun readyRuntimeHandleDependentMethodReturnsRuntimeNotReadyWhenAccessibilityHandleMissing() {
        val method =
            fakeMethod(
                name = "snapshot.get",
                policy =
                    RpcMethodPolicy(
                        requiresReadyRuntime = true,
                        requiresAccessibilityHandle = true,
                        timeoutError = RpcErrorCode.SNAPSHOT_UNAVAILABLE,
                        timeoutMessage = "snapshot.get timed out",
                    ),
            )

        val payload =
            dispatch(
                methods = listOf(method),
                request = request(method = "snapshot.get"),
                readiness = RuntimeReadiness(accessibilityEnabled = true, accessibilityConnected = true),
            )

        assertEquals(false, payload.getBoolean("ok"))
        assertEquals("RUNTIME_NOT_READY", payload.getJSONObject("error").getString("code"))
    }

    @Test
    fun missingAccessibilityHandleDoesNotPrepareMalformedHandleDependentRequest() {
        var prepareCount = 0
        val method =
            object : DeviceRpcMethod {
                override val name: String = "action.perform"
                override val policy: RpcMethodPolicy =
                    RpcMethodPolicy(
                        requiresReadyRuntime = true,
                        requiresAccessibilityHandle = true,
                        timeoutError = RpcErrorCode.ACTION_TIMEOUT,
                        timeoutMessage = "action.perform timed out",
                    )

                override fun prepare(request: RpcRequestEnvelope): PreparedRpcCall {
                    prepareCount += 1
                    error("missing-handle requests must not prepare malformed requests")
                }
            }

        val payload =
            dispatch(
                methods = listOf(method),
                request = request(method = "action.perform", params = JSONObject().put("kind", 123)),
                readiness = RuntimeReadiness(accessibilityEnabled = true, accessibilityConnected = true),
            )

        assertEquals(false, payload.getBoolean("ok"))
        assertEquals("RUNTIME_NOT_READY", payload.getJSONObject("error").getString("code"))
        assertEquals(0, prepareCount)
    }

    @Test
    fun revokedAccessibilityHandleBlocksHandleDependentMethod() {
        val service = mock(AccessibilityService::class.java)
        val method =
            fakeMethod(
                name = "snapshot.get",
                policy =
                    RpcMethodPolicy(
                        requiresReadyRuntime = true,
                        requiresAccessibilityHandle = true,
                        timeoutError = RpcErrorCode.SNAPSHOT_UNAVAILABLE,
                        timeoutMessage = "snapshot.get timed out",
                    ),
            )

        val payload =
            dispatch(
                methods = listOf(method),
                request = request(method = "snapshot.get"),
                readiness = RuntimeReadiness(accessibilityEnabled = true, accessibilityConnected = true),
                attachmentHandle =
                    AccessibilityAttachmentHandleSnapshot(
                        service = service,
                        generation = 4L,
                        revoked = true,
                    ),
            )

        assertEquals(false, payload.getBoolean("ok"))
        assertEquals("RUNTIME_NOT_READY", payload.getJSONObject("error").getString("code"))
    }

    @Test
    fun dispatcherPassesPreparedCallTimeoutBudgetToExecutionRunner() {
        val executor = CapturingTimeoutExecutorService()
        val method =
            fakeMethod(
                name = "events.poll",
                policy =
                    RpcMethodPolicy(
                        timeoutError = RpcErrorCode.INTERNAL_ERROR,
                        timeoutMessage = "events.poll timed out",
                    ),
                timeoutMs = 4321L,
                result = JSONObject().put("timedOut", true),
            )

        val payload =
            dispatch(
                methods = listOf(method),
                request = request(method = "events.poll"),
                executionRunner = RpcExecutionRunner(executor),
            )

        assertEquals(true, payload.getBoolean("ok"))
        assertEquals(4321L, executor.lastTimeoutMs)
    }

    @Test
    fun timeoutCalculationAndExecutionShareOnePreparedRequest() {
        var prepareCount = 0
        var executeCount = 0
        val method =
            object : DeviceRpcMethod {
                override val name: String = "events.poll"
                override val policy: RpcMethodPolicy =
                    RpcMethodPolicy(
                        timeoutError = RpcErrorCode.INTERNAL_ERROR,
                        timeoutMessage = "events.poll timed out",
                    )

                override fun prepare(request: RpcRequestEnvelope): PreparedRpcCall {
                    prepareCount += 1
                    val waitMs = request.params.getLong("waitMs")
                    return PreparedRpcCall.typed(
                        timeoutMs = waitMs + 100L,
                        execute = {
                            executeCount += 1
                            Unit
                        },
                        encode = { JSONObject().put("timedOut", true) },
                    )
                }
            }

        val executor = CapturingTimeoutExecutorService()
        val payload =
            dispatch(
                methods = listOf(method),
                request = request(method = "events.poll", params = JSONObject().put("waitMs", 4321L)),
                executionRunner = RpcExecutionRunner(executor),
            )

        assertEquals(true, payload.getBoolean("ok"))
        assertEquals(1, prepareCount)
        assertEquals(1, executeCount)
        assertEquals(4421L, executor.lastTimeoutMs)
    }

    @Test
    fun prepareIllegalArgumentReturnsInvalidRequestEnvelope() {
        val method =
            object : DeviceRpcMethod {
                override val name: String = "events.poll"
                override val policy: RpcMethodPolicy =
                    RpcMethodPolicy(
                        timeoutError = RpcErrorCode.INTERNAL_ERROR,
                        timeoutMessage = "events.poll timed out",
                    )

                override fun prepare(request: RpcRequestEnvelope): PreparedRpcCall = throw IllegalArgumentException("bad params")
            }

        val payload =
            dispatch(
                methods = listOf(method),
                request = request(method = "events.poll"),
            )

        assertEquals(false, payload.getBoolean("ok"))
        assertEquals("req-dispatch", payload.getString("id"))
        assertEquals("INVALID_REQUEST", payload.getJSONObject("error").getString("code"))
        assertEquals(false, payload.getJSONObject("error").getBoolean("retryable"))
    }

    @Test
    fun prepareRequestValidationExceptionPreservesEnvelopeMapping() {
        val method =
            object : DeviceRpcMethod {
                override val name: String = "events.poll"
                override val policy: RpcMethodPolicy =
                    RpcMethodPolicy(
                        timeoutError = RpcErrorCode.INTERNAL_ERROR,
                        timeoutMessage = "events.poll timed out",
                    )

                override fun prepare(request: RpcRequestEnvelope): PreparedRpcCall =
                    throw RequestValidationException("invalid poll", requestId = "ignored-by-dispatch")
            }

        val payload =
            dispatch(
                methods = listOf(method),
                request = request(method = "events.poll"),
            )

        assertEquals(false, payload.getBoolean("ok"))
        assertEquals("req-dispatch", payload.getString("id"))
        assertEquals("INVALID_REQUEST", payload.getJSONObject("error").getString("code"))
        assertEquals(false, payload.getJSONObject("error").getBoolean("retryable"))
        assertEquals("invalid poll", payload.getJSONObject("error").getString("message"))
    }

    @Test
    fun prepareDeviceRpcExceptionPreservesEnvelopeMapping() {
        val method =
            object : DeviceRpcMethod {
                override val name: String = "events.poll"
                override val policy: RpcMethodPolicy =
                    RpcMethodPolicy(
                        timeoutError = RpcErrorCode.INTERNAL_ERROR,
                        timeoutMessage = "events.poll timed out",
                    )

                override fun prepare(request: RpcRequestEnvelope): PreparedRpcCall =
                    throw DeviceRpcException(
                        code = RpcErrorCode.UNAUTHORIZED,
                        message = "not allowed",
                        retryable = true,
                    )
            }

        val payload =
            dispatch(
                methods = listOf(method),
                request = request(method = "events.poll"),
            )

        assertEquals(false, payload.getBoolean("ok"))
        assertEquals("req-dispatch", payload.getString("id"))
        assertEquals("UNAUTHORIZED", payload.getJSONObject("error").getString("code"))
        assertEquals(true, payload.getJSONObject("error").getBoolean("retryable"))
        assertEquals("not allowed", payload.getJSONObject("error").getString("message"))
    }

    @Test
    fun screenshotBudgetFailureReturnsNonRetryableScreenshotUnavailableEnvelope() {
        val service = mock(AccessibilityService::class.java)
        val method =
            ScreenshotCaptureMethod(
                screenshotExecutionFactory = { _ ->
                    {
                        throw ScreenshotException(
                            message = "screenshot encoded payload exceeds size budget",
                            retryable = false,
                        )
                    }
                },
            )

        val payload =
            dispatch(
                methods = listOf(method),
                request = request(method = "screenshot.capture"),
                accessibilityService = service,
            )

        assertEquals(false, payload.getBoolean("ok"))
        assertEquals("SCREENSHOT_UNAVAILABLE", payload.getJSONObject("error").getString("code"))
        assertEquals(false, payload.getJSONObject("error").getBoolean("retryable"))
        assertEquals(
            "screenshot encoded payload exceeds size budget",
            payload.getJSONObject("error").getString("message"),
        )
    }

    @Test
    fun timeoutUsesMethodSpecificErrorMapping() {
        val method =
            fakeMethod(
                name = "action.perform",
                policy =
                    RpcMethodPolicy(
                        requiresReadyRuntime = true,
                        timeoutError = RpcErrorCode.ACTION_TIMEOUT,
                        timeoutMessage = "action.perform timed out",
                    ),
                timeoutMs = 250L,
                result = JSONObject().put("status", "done"),
            )

        val payload =
            dispatch(
                methods = listOf(method),
                request = request(method = "action.perform"),
                readiness = RuntimeReadiness(accessibilityEnabled = true, accessibilityConnected = true),
                executionRunner = RpcExecutionRunner(TimeoutExecutorService()),
            )

        assertEquals(false, payload.getBoolean("ok"))
        assertEquals("ACTION_TIMEOUT", payload.getJSONObject("error").getString("code"))
        assertTrue(payload.getJSONObject("error").getString("message").startsWith("action.perform timed out"))
    }

    @Test
    fun snapshotTimeoutUsesMethodSpecificErrorWhenEncoderThrowsTimeoutException() {
        var executeCount = 0
        val method =
            fakeTypedMethod(
                name = "snapshot.get",
                policy =
                    RpcMethodPolicy(
                        requiresReadyRuntime = true,
                        timeoutError = RpcErrorCode.SNAPSHOT_UNAVAILABLE,
                        timeoutMessage = "snapshot.get timed out",
                    ),
                timeoutMs = 250L,
                execute = {
                    executeCount += 1
                    "snapshot-payload"
                },
                encode = { _: String -> throw TimeoutException("encoder blocked") },
            )

        val payload =
            dispatch(
                methods = listOf(method),
                request = request(method = "snapshot.get"),
                readiness = RuntimeReadiness(accessibilityEnabled = true, accessibilityConnected = true),
                executionRunner = RpcExecutionRunner(ImmediateExecutorService()),
            )

        assertEquals(1, executeCount)
        assertEquals(false, payload.getBoolean("ok"))
        assertEquals("SNAPSHOT_UNAVAILABLE", payload.getJSONObject("error").getString("code"))
        assertTrue(payload.getJSONObject("error").getString("message").startsWith("snapshot.get timed out"))
    }

    private fun dispatch(
        methods: List<DeviceRpcMethod>,
        request: RpcRequestEnvelope,
        readiness: RuntimeReadiness = RuntimeReadiness(accessibilityEnabled = true, accessibilityConnected = true),
        accessibilityService: AccessibilityService? = null,
        attachmentHandle: AccessibilityAttachmentHandleSnapshot? = null,
        executionRunner: RpcExecutionRunner = RpcExecutionRunner(ImmediateExecutorService()),
    ): JSONObject =
        JSONObject(
            RpcDispatcher(
                runtimeAccess =
                    object : RuntimeAccess {
                        override fun readiness(): RuntimeReadiness = readiness

                        override fun currentDeviceToken(): String = "device-token"

                        override fun applicationContext() = null

                        override fun currentAccessibilityService() = accessibilityService

                        override fun currentAccessibilityAttachmentHandle(): AccessibilityAttachmentHandleSnapshot =
                            attachmentHandle
                                ?: AccessibilityAttachmentHandleSnapshot(
                                    service = accessibilityService,
                                    generation = 0L,
                                    revoked = false,
                                )
                    },
                methodCatalog = RpcMethodCatalog(methods),
                executionRunner = executionRunner,
            ).dispatch(request),
        )

    private fun request(
        method: String,
        params: JSONObject = JSONObject(),
    ): RpcRequestEnvelope = RpcRequestEnvelope(id = "req-dispatch", method = method, params = params)

    private fun fakeMethod(
        name: String,
        policy: RpcMethodPolicy,
        timeoutMs: Long = 1000L,
        result: JSONObject = JSONObject(),
    ): DeviceRpcMethod =
        fakeTypedMethod(
            name = name,
            policy = policy,
            timeoutMs = timeoutMs,
            execute = { EncodedPayload(result) },
            encode = { payload -> payload.value },
        )

    private fun <T> fakeTypedMethod(
        name: String,
        policy: RpcMethodPolicy,
        timeoutMs: Long,
        execute: () -> T,
        encode: (T) -> JSONObject,
    ): DeviceRpcMethod =
        object : DeviceRpcMethod {
            override val name: String = name
            override val policy: RpcMethodPolicy = policy

            override fun prepare(request: RpcRequestEnvelope): PreparedRpcCall =
                PreparedRpcCall.typed(
                    timeoutMs = timeoutMs,
                    execute = execute,
                    encode = encode,
                )
        }

    private data class EncodedPayload(
        val value: JSONObject,
    )

    private class ImmediateExecutorService : AbstractExecutorService() {
        override fun shutdown() = Unit

        override fun shutdownNow(): MutableList<Runnable> = mutableListOf()

        override fun isShutdown(): Boolean = false

        override fun isTerminated(): Boolean = false

        override fun awaitTermination(
            timeout: Long,
            unit: TimeUnit,
        ): Boolean = true

        override fun execute(command: Runnable) = command.run()
    }

    private class CapturingTimeoutExecutorService : AbstractExecutorService() {
        var lastTimeoutMs: Long? = null

        override fun shutdown() = Unit

        override fun shutdownNow(): MutableList<Runnable> = mutableListOf()

        override fun isShutdown(): Boolean = false

        override fun isTerminated(): Boolean = false

        override fun awaitTermination(
            timeout: Long,
            unit: TimeUnit,
        ): Boolean = true

        override fun execute(command: Runnable) = Unit

        override fun <T> submit(task: Callable<T>): Future<T> =
            object : Future<T> {
                override fun cancel(mayInterruptIfRunning: Boolean): Boolean = false

                override fun isCancelled(): Boolean = false

                override fun isDone(): Boolean = false

                override fun get(): T = task.call()

                override fun get(
                    timeout: Long,
                    unit: TimeUnit,
                ): T {
                    lastTimeoutMs = unit.toMillis(timeout)
                    return task.call()
                }
            }
    }

    private class TimeoutExecutorService : AbstractExecutorService() {
        override fun shutdown() = Unit

        override fun shutdownNow(): MutableList<Runnable> = mutableListOf()

        override fun isShutdown(): Boolean = false

        override fun isTerminated(): Boolean = false

        override fun awaitTermination(
            timeout: Long,
            unit: TimeUnit,
        ): Boolean = true

        override fun execute(command: Runnable) = Unit

        override fun <T> submit(task: Callable<T>): Future<T> =
            object : Future<T> {
                override fun cancel(mayInterruptIfRunning: Boolean): Boolean = true

                override fun isCancelled(): Boolean = false

                override fun isDone(): Boolean = false

                override fun get(): T = throw TimeoutException("timed out")

                override fun get(
                    timeout: Long,
                    unit: TimeUnit,
                ): T = throw TimeoutException("timed out")
            }
    }
}
