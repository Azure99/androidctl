package com.rainng.androidctl.agent.rpc

import android.accessibilityservice.AccessibilityService
import com.rainng.androidctl.agent.actions.ActionResult
import com.rainng.androidctl.agent.actions.ActionResultStatus
import com.rainng.androidctl.agent.actions.ActionTarget
import com.rainng.androidctl.agent.device.AppEntryResponse
import com.rainng.androidctl.agent.device.AppsListResponse
import com.rainng.androidctl.agent.events.DeviceEvent
import com.rainng.androidctl.agent.events.EventPollRequest
import com.rainng.androidctl.agent.events.EventPollResult
import com.rainng.androidctl.agent.events.ImeChangedPayload
import com.rainng.androidctl.agent.events.PackageChangedPayload
import com.rainng.androidctl.agent.runtime.ObservedWindowState
import com.rainng.androidctl.agent.runtime.RuntimeAccess
import com.rainng.androidctl.agent.runtime.RuntimeReadiness
import com.rainng.androidctl.agent.screenshot.ScreenshotResponse
import com.rainng.androidctl.agent.snapshot.NodeFingerprint
import com.rainng.androidctl.agent.snapshot.NodePath
import com.rainng.androidctl.agent.snapshot.SnapshotDisplay
import com.rainng.androidctl.agent.snapshot.SnapshotIme
import com.rainng.androidctl.agent.snapshot.SnapshotNode
import com.rainng.androidctl.agent.snapshot.SnapshotNodeHandle
import com.rainng.androidctl.agent.snapshot.SnapshotPayload
import com.rainng.androidctl.agent.snapshot.SnapshotPublication
import com.rainng.androidctl.agent.snapshot.SnapshotRecord
import com.rainng.androidctl.agent.snapshot.SnapshotRegistry
import com.rainng.androidctl.agent.snapshot.SnapshotWindow
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.mockito.Mockito.mock
import java.util.concurrent.AbstractExecutorService
import java.util.concurrent.Callable
import java.util.concurrent.Future
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.TimeUnit

class RpcRequestHandlerTest {
    private val handler =
        newHandler(
            expectedTokenProvider = { "device-token" },
            readinessProvider = {
                RuntimeReadiness(false, false)
            },
            versionProvider = { "1.0.0" },
        )

    @Test
    fun invalidJson_returnsInvalidRequest() {
        val payload = JSONObject(handler.handle(headers = emptyMap(), rawBody = "not-json"))

        assertEquals(false, payload.getBoolean("ok"))
        assertEquals("INVALID_REQUEST", payload.getJSONObject("error").getString("code"))
        assertEquals(false, payload.getJSONObject("error").getBoolean("retryable"))
        assertFalse(payload.has("id"))
    }

    @Test
    fun missingMethod_returnsInvalidRequest() {
        val payload = JSONObject(handler.handle(headers = emptyMap(), rawBody = """{"id":"req-1","params":{}}"""))

        assertEquals(false, payload.getBoolean("ok"))
        assertEquals("INVALID_REQUEST", payload.getJSONObject("error").getString("code"))
        assertEquals("req-1", payload.getString("id"))
    }

    @Test
    fun invalidId_returnsInvalidRequestWithoutEchoingId() {
        val payload = JSONObject(handler.handle(headers = emptyMap(), rawBody = """{"id":123,"method":"meta.get","params":{}}"""))

        assertEquals(false, payload.getBoolean("ok"))
        assertEquals("INVALID_REQUEST", payload.getJSONObject("error").getString("code"))
        assertFalse(payload.has("id"))
    }

    @Test
    fun invalidParamsWithValidId_returnsInvalidRequestAndEchoesId() {
        val payload = JSONObject(handler.handle(headers = emptyMap(), rawBody = """{"id":"req-params","method":"meta.get","params":[]}"""))

        assertEquals(false, payload.getBoolean("ok"))
        assertEquals("INVALID_REQUEST", payload.getJSONObject("error").getString("code"))
        assertEquals("req-params", payload.getString("id"))
        assertEquals(false, payload.getJSONObject("error").getBoolean("retryable"))
    }

    @Test
    fun numericMethod_returnsInvalidRequestAndEchoesId() {
        val payload = JSONObject(handler.handle(headers = emptyMap(), rawBody = """{"id":"req-method","method":123,"params":{}}"""))

        assertEquals(false, payload.getBoolean("ok"))
        assertEquals("INVALID_REQUEST", payload.getJSONObject("error").getString("code"))
        assertEquals(false, payload.getJSONObject("error").getBoolean("retryable"))
        assertEquals("req-method", payload.getString("id"))
    }

    @Test
    fun missingBearerToken_returnsUnauthorized() {
        val payload = JSONObject(handler.handle(headers = emptyMap(), rawBody = """{"id":"req-2","method":"meta.get","params":{}}"""))

        assertEquals(false, payload.getBoolean("ok"))
        assertEquals("UNAUTHORIZED", payload.getJSONObject("error").getString("code"))
    }

    @Test
    fun blockedAuthReturnsRuntimeNotReadyBeforeUnauthorized() {
        var expectedTokenLookups = 0
        val handler =
            newHandler(
                expectedTokenProvider = {
                    expectedTokenLookups += 1
                    "device-token"
                },
                readinessProvider = {
                    RuntimeReadiness(
                        accessibilityEnabled = true,
                        accessibilityConnected = true,
                        authBlockedMessage = "stored device token could not be decrypted",
                    )
                },
            )

        val payload =
            JSONObject(
                handler.handle(
                    headers = mapOf("authorization" to "Bearer wrong-token"),
                    rawBody = """{"id":"req-auth-blocked","method":"meta.get","params":{}}""",
                ),
            )

        assertEquals(false, payload.getBoolean("ok"))
        assertEquals("RUNTIME_NOT_READY", payload.getJSONObject("error").getString("code"))
        assertEquals(0, expectedTokenLookups)
    }

    @Test
    fun blockedAuthWithoutBearerReturnsUnauthorized() {
        var expectedTokenLookups = 0
        val handler =
            newHandler(
                expectedTokenProvider = {
                    expectedTokenLookups += 1
                    "device-token"
                },
                readinessProvider = {
                    RuntimeReadiness(
                        accessibilityEnabled = true,
                        accessibilityConnected = true,
                        authBlockedMessage = "stored device token could not be decrypted",
                    )
                },
            )

        val payload =
            JSONObject(
                handler.handle(
                    headers = emptyMap(),
                    rawBody = """{"id":"req-auth-missing","method":"meta.get","params":{}}""",
                ),
            )

        assertEquals(false, payload.getBoolean("ok"))
        assertEquals("UNAUTHORIZED", payload.getJSONObject("error").getString("code"))
        assertEquals(0, expectedTokenLookups)
    }

    @Test
    fun metaGet_returnsVersionAndCapabilities() {
        val payload =
            JSONObject(
                handler.handle(
                    headers = mapOf("authorization" to "Bearer device-token"),
                    rawBody = """{"id":"req-3","method":"meta.get","params":{}}""",
                ),
            )

        assertTrue(payload.getBoolean("ok"))
        assertEquals("androidctl-device-agent", payload.getJSONObject("result").getString("service"))
        assertEquals(true, payload.getJSONObject("result").getJSONObject("capabilities").getBoolean("supportsEventsPoll"))
        assertEquals(true, payload.getJSONObject("result").getJSONObject("capabilities").getBoolean("supportsScreenshot"))
    }

    @Test
    fun snapshotBeforeRuntimeReady_returnsAccessibilityDisabled() {
        val payload =
            JSONObject(
                handler.handle(
                    headers = mapOf("authorization" to "Bearer device-token"),
                    rawBody = """{"id":"req-4","method":"snapshot.get","params":{"includeInvisible":true,"includeSystemWindows":true}}""",
                ),
            )

        assertEquals(false, payload.getBoolean("ok"))
        assertEquals("ACCESSIBILITY_DISABLED", payload.getJSONObject("error").getString("code"))
    }

    @Test
    fun successfulSnapshotReturnsResolvableSnapshotId() {
        SnapshotRegistry.resetForTest()
        val runtimeReadyHandler =
            newHandler(
                expectedTokenProvider = { "device-token" },
                readinessProvider = { RuntimeReadiness(true, true) },
                accessibilityService = mock(AccessibilityService::class.java),
                versionProvider = { "1.0.0" },
            )

        val payload =
            JSONObject(
                runtimeReadyHandler.handle(
                    headers = mapOf("authorization" to "Bearer device-token"),
                    rawBody =
                        """
                        {
                          "id":"req-snapshot-success",
                          "method":"snapshot.get",
                          "params":{"includeInvisible":true,"includeSystemWindows":true}
                        }
                        """.trimIndent(),
                ),
            )

        assertTrue(payload.getBoolean("ok"))
        val snapshotId = payload.getJSONObject("result").getLong("snapshotId")
        assertNotNull(SnapshotRegistry.find(snapshotId))
    }

    @Test
    fun snapshotWhenAccessibilityEnabledButDisconnected_returnsRuntimeNotReady() {
        val runtimeHandler =
            newHandler(
                expectedTokenProvider = { "device-token" },
                readinessProvider = {
                    RuntimeReadiness(true, false)
                },
                versionProvider = { "1.0.0" },
            )

        val payload =
            JSONObject(
                runtimeHandler.handle(
                    headers = mapOf("authorization" to "Bearer device-token"),
                    rawBody = """{"id":"req-5","method":"snapshot.get","params":{"includeInvisible":true,"includeSystemWindows":true}}""",
                ),
            )

        assertEquals(false, payload.getBoolean("ok"))
        assertEquals("RUNTIME_NOT_READY", payload.getJSONObject("error").getString("code"))
    }

    @Test
    fun blockedActionMalformedParamsReturnsReadinessErrorBeforeValidation() {
        val payload =
            JSONObject(
                handler.handle(
                    headers = mapOf("authorization" to "Bearer device-token"),
                    rawBody = """{"id":"req-malformed","method":"action.perform","params":{"kind":123}}""",
                ),
            )

        assertEquals(false, payload.getBoolean("ok"))
        assertEquals("ACCESSIBILITY_DISABLED", payload.getJSONObject("error").getString("code"))
    }

    @Test
    fun allowedMalformedEventsPollParamsReturnInvalidRequest() {
        val payload =
            JSONObject(
                handler.handle(
                    headers = mapOf("authorization" to "Bearer device-token"),
                    rawBody = """{"id":"req-events-invalid","method":"events.poll","params":{"waitMs":-1}}""",
                ),
            )

        assertEquals(false, payload.getBoolean("ok"))
        assertEquals("INVALID_REQUEST", payload.getJSONObject("error").getString("code"))
        assertEquals("req-events-invalid", payload.getString("id"))
        assertEquals(false, payload.getJSONObject("error").getBoolean("retryable"))
    }

    @Test
    fun eventsPollSuccessEncodesTypedPayloadsWithoutChangingWireShape() {
        val handler =
            newHandler(
                expectedTokenProvider = { "device-token" },
                readinessProvider = { RuntimeReadiness(false, false) },
                versionProvider = { "1.0.0" },
                eventsPollProvider = {
                    EventPollResult(
                        events =
                            listOf(
                                DeviceEvent(
                                    seq = 3L,
                                    timestamp = "2026-03-27T00:00:00Z",
                                    data = PackageChangedPayload(packageName = "com.android.settings", activityName = null),
                                ),
                                DeviceEvent(
                                    seq = 4L,
                                    timestamp = "2026-03-27T00:00:01Z",
                                    data = ImeChangedPayload(visible = false, windowId = null),
                                ),
                            ),
                        latestSeq = 4L,
                        needResync = false,
                        timedOut = false,
                    )
                },
            )

        val payload =
            JSONObject(
                handler.handle(
                    headers = mapOf("authorization" to "Bearer device-token"),
                    rawBody = """{"id":"req-events-ok","method":"events.poll","params":{"afterSeq":0,"waitMs":0,"limit":20}}""",
                ),
            )

        assertTrue(payload.getBoolean("ok"))
        assertEquals("req-events-ok", payload.getString("id"))
        val result = payload.getJSONObject("result")
        assertEquals(4L, result.getLong("latestSeq"))
        assertFalse(result.getBoolean("needResync"))
        assertFalse(result.getBoolean("timedOut"))
        val events = result.getJSONArray("events")
        assertEquals(2, events.length())
        assertTrue(events.getJSONObject(0).getJSONObject("data").isNull("activityName"))
        assertTrue(events.getJSONObject(1).getJSONObject("data").isNull("windowId"))
    }

    @Test
    fun readyRuntimeMalformedSnapshotParamsWithMissingHandleReturnRuntimeNotReady() {
        val runtimeReadyHandler =
            newHandler(
                expectedTokenProvider = { "device-token" },
                readinessProvider = { RuntimeReadiness(true, true) },
                versionProvider = { "1.0.0" },
            )

        val payload =
            JSONObject(
                runtimeReadyHandler.handle(
                    headers = mapOf("authorization" to "Bearer device-token"),
                    rawBody =
                        """
                        {
                          "id":"req-snapshot-invalid",
                          "method":"snapshot.get",
                          "params":{"includeInvisible":"false","includeSystemWindows":true}
                        }
                        """.trimIndent(),
                ),
            )

        assertEquals(false, payload.getBoolean("ok"))
        assertEquals("RUNTIME_NOT_READY", payload.getJSONObject("error").getString("code"))
        assertEquals("req-snapshot-invalid", payload.getString("id"))
    }

    @Test
    fun readyRuntimeMalformedActionParamsWithMissingHandleReturnRuntimeNotReady() {
        val runtimeReadyHandler =
            newHandler(
                expectedTokenProvider = { "device-token" },
                readinessProvider = { RuntimeReadiness(true, true) },
                versionProvider = { "1.0.0" },
            )

        val payload =
            JSONObject(
                runtimeReadyHandler.handle(
                    headers = mapOf("authorization" to "Bearer device-token"),
                    rawBody = """{"id":"req-action-invalid","method":"action.perform","params":{"kind":123}}""",
                ),
            )

        assertEquals(false, payload.getBoolean("ok"))
        assertEquals("RUNTIME_NOT_READY", payload.getJSONObject("error").getString("code"))
        assertEquals("req-action-invalid", payload.getString("id"))
    }

    @Test
    fun readyRuntimeMalformedScreenshotParamsReturnRuntimeNotReady() {
        val runtimeReadyHandler =
            newHandler(
                expectedTokenProvider = { "device-token" },
                readinessProvider = { RuntimeReadiness(true, true) },
                versionProvider = { "1.0.0" },
            )

        val payload =
            JSONObject(
                runtimeReadyHandler.handle(
                    headers = mapOf("authorization" to "Bearer device-token"),
                    rawBody = """{"id":"req-screenshot-invalid","method":"screenshot.capture","params":{"format":true}}""",
                ),
            )

        assertEquals(false, payload.getBoolean("ok"))
        assertEquals("RUNTIME_NOT_READY", payload.getJSONObject("error").getString("code"))
        assertEquals("req-screenshot-invalid", payload.getString("id"))
    }

    @Test
    fun dispatcherUsesRequestEnvelopeIdWhenPrepareThrowsValidationError() {
        val handler =
            RpcRequestHandler(
                authorizationGate =
                    RpcAuthorizationGate(
                        expectedTokenProvider = { "device-token" },
                        readinessProvider = { RuntimeReadiness(true, true) },
                    ),
                dispatcher =
                    RpcDispatcher(
                        runtimeAccess = fakeRuntimeAccess(readinessProvider = { RuntimeReadiness(true, true) }),
                        methodCatalog =
                            RpcMethodCatalog(
                                listOf(
                                    object : DeviceRpcMethod {
                                        override val name: String = "events.poll"
                                        override val policy: RpcMethodPolicy =
                                            RpcMethodPolicy(
                                                timeoutError = com.rainng.androidctl.agent.errors.RpcErrorCode.INTERNAL_ERROR,
                                                timeoutMessage = "events.poll timed out",
                                            )

                                        override fun prepare(request: RpcRequestEnvelope): PreparedRpcCall =
                                            throw com.rainng.androidctl.agent.errors.RequestValidationException(
                                                message = "invalid poll",
                                                requestId = "different-id",
                                            )
                                    },
                                ),
                            ),
                        executionRunner = RpcExecutionRunner(RpcRequestHandler.newMethodExecutor()),
                    ),
            )

        val payload =
            JSONObject(
                handler.handle(
                    headers = mapOf("authorization" to "Bearer device-token"),
                    rawBody = """{"id":"req-envelope","method":"events.poll","params":{}}""",
                ),
            )

        assertEquals(false, payload.getBoolean("ok"))
        assertEquals("req-envelope", payload.getString("id"))
        assertEquals("INVALID_REQUEST", payload.getJSONObject("error").getString("code"))
    }

    @Test
    fun methodPoliciesRemainConsistentWhenRuntimeIsNotReady() {
        val handler =
            newHandler(
                expectedTokenProvider = { "device-token" },
                readinessProvider = {
                    RuntimeReadiness(false, false)
                },
                versionProvider = { "1.0.0" },
                eventsPollProvider = {
                    EventPollResult(
                        events = emptyList(),
                        latestSeq = 0L,
                        needResync = false,
                        timedOut = true,
                    )
                },
                appsListProvider = {
                    AppsListResponse(
                        apps =
                            listOf(
                                AppEntryResponse(
                                    packageName = "com.android.settings",
                                    appLabel = "Settings",
                                    launchable = true,
                                ),
                            ),
                    )
                },
            )

        val blockedMethods = listOf("snapshot.get", "action.perform", "screenshot.capture")
        blockedMethods.forEach { method ->
            val payload =
                JSONObject(
                    handler.handle(
                        headers = mapOf("authorization" to "Bearer device-token"),
                        rawBody = """{"id":"req-blocked","method":"$method","params":{}}""",
                    ),
                )
            assertEquals(false, payload.getBoolean("ok"))
            assertEquals("ACCESSIBILITY_DISABLED", payload.getJSONObject("error").getString("code"))
        }

        val allowedMethods = listOf("events.poll", "apps.list")
        allowedMethods.forEach { method ->
            val payload =
                JSONObject(
                    handler.handle(
                        headers = mapOf("authorization" to "Bearer device-token"),
                        rawBody = """{"id":"req-allowed","method":"$method","params":{}}""",
                    ),
                )
            assertEquals(true, payload.getBoolean("ok"))
        }
    }

    @Test
    fun metaGetUnexpectedFailureReturnsErrorEnvelope() {
        val handler =
            newHandler(
                expectedTokenProvider = { "device-token" },
                readinessProvider = {
                    RuntimeReadiness(false, false)
                },
                versionProvider = { throw IllegalStateException("boom") },
            )

        val payload =
            JSONObject(
                handler.handle(
                    headers = mapOf("authorization" to "Bearer device-token"),
                    rawBody = """{"id":"req-14","method":"meta.get","params":{}}""",
                ),
            )

        assertEquals(false, payload.getBoolean("ok"))
        assertEquals("INTERNAL_ERROR", payload.getJSONObject("error").getString("code"))
        assertEquals(true, payload.getJSONObject("error").getBoolean("retryable"))
    }

    @Test
    fun busyExecutorReturnsInternalErrorEnvelope() {
        val handler =
            newHandler(
                expectedTokenProvider = { "device-token" },
                readinessProvider = {
                    RuntimeReadiness(false, false)
                },
                versionProvider = { "1.0.0" },
                methodExecutor = RejectingExecutorService(),
            )

        val payload =
            JSONObject(
                handler.handle(
                    headers = mapOf("authorization" to "Bearer device-token"),
                    rawBody = """{"id":"req-15","method":"meta.get","params":{}}""",
                ),
            )

        assertEquals(false, payload.getBoolean("ok"))
        assertEquals("INTERNAL_ERROR", payload.getJSONObject("error").getString("code"))
        assertEquals("server is busy", payload.getJSONObject("error").getString("message"))
    }

    @Test
    fun interruptedFutureReturnsRetryableInternalErrorEnvelope() {
        val handler =
            newHandler(
                expectedTokenProvider = { "device-token" },
                readinessProvider = {
                    RuntimeReadiness(false, false)
                },
                versionProvider = { "1.0.0" },
                methodExecutor = InterruptedExecutorService(),
            )

        val payload =
            JSONObject(
                handler.handle(
                    headers = mapOf("authorization" to "Bearer device-token"),
                    rawBody = """{"id":"req-16","method":"meta.get","params":{}}""",
                ),
            )

        assertEquals(false, payload.getBoolean("ok"))
        assertEquals("INTERNAL_ERROR", payload.getJSONObject("error").getString("code"))
        assertEquals(true, payload.getJSONObject("error").getBoolean("retryable"))
    }

    @Test
    fun cancelledFutureReturnsRetryableInternalErrorEnvelope() {
        val handler =
            newHandler(
                expectedTokenProvider = { "device-token" },
                readinessProvider = {
                    RuntimeReadiness(false, false)
                },
                versionProvider = { "1.0.0" },
                methodExecutor = CancelledExecutorService(),
            )

        val payload =
            JSONObject(
                handler.handle(
                    headers = mapOf("authorization" to "Bearer device-token"),
                    rawBody = """{"id":"req-17","method":"meta.get","params":{}}""",
                ),
            )

        assertEquals(false, payload.getBoolean("ok"))
        assertEquals("INTERNAL_ERROR", payload.getJSONObject("error").getString("code"))
        assertEquals(true, payload.getJSONObject("error").getBoolean("retryable"))
    }

    @Test
    fun gracefulShutdownStopsMethodExecutorWithoutForcing() {
        val methodExecutor = RecordingShutdownExecutorService(awaitResult = true)
        val handler = newHandler(methodExecutor = methodExecutor)

        handler.shutdown(force = false)

        assertEquals(1, methodExecutor.shutdownCalls)
        assertEquals(0, methodExecutor.shutdownNowCalls)
        assertEquals(listOf(100L to TimeUnit.MILLISECONDS), methodExecutor.awaitCalls)
    }

    @Test
    fun forcedShutdownCancelsMethodExecutorAfterGraceTimeoutAndIsIdempotent() {
        val methodExecutor = RecordingShutdownExecutorService(awaitResult = false)
        val handler = newHandler(methodExecutor = methodExecutor)

        handler.shutdown(force = true)
        handler.shutdown(force = true)

        assertEquals(1, methodExecutor.shutdownCalls)
        assertEquals(1, methodExecutor.shutdownNowCalls)
        assertEquals(
            listOf(
                100L to TimeUnit.MILLISECONDS,
                100L to TimeUnit.MILLISECONDS,
            ),
            methodExecutor.awaitCalls,
        )
    }

    @Test
    fun interruptedShutdownRestoresThreadFlag() {
        val methodExecutor = RecordingShutdownExecutorService(awaitError = InterruptedException("interrupted"))
        val handler = newHandler(methodExecutor = methodExecutor)

        try {
            handler.shutdown(force = false)

            assertTrue(Thread.currentThread().isInterrupted)
            assertEquals(1, methodExecutor.shutdownCalls)
            assertEquals(0, methodExecutor.shutdownNowCalls)
        } finally {
            Thread.interrupted()
        }
    }

    private fun newHandler(
        expectedTokenProvider: () -> String = { "device-token" },
        readinessProvider: () -> RuntimeReadiness = {
            RuntimeReadiness(false, false)
        },
        versionProvider: () -> String = { "1.0.0" },
        eventsPollProvider: (EventPollRequest) -> EventPollResult = {
            EventPollResult(
                events = emptyList(),
                latestSeq = 0L,
                needResync = false,
                timedOut = true,
            )
        },
        appsListProvider: () -> AppsListResponse = { AppsListResponse(apps = emptyList()) },
        accessibilityService: AccessibilityService? = null,
        methodExecutor: java.util.concurrent.ExecutorService = RpcRequestHandler.newMethodExecutor(),
    ): RpcRequestHandler {
        val runtimeAccess =
            fakeRuntimeAccess(
                readinessProvider = readinessProvider,
                accessibilityService = accessibilityService,
            )
        return RpcRequestHandler(
            authorizationGate =
                RpcAuthorizationGate(
                    expectedTokenProvider = expectedTokenProvider,
                    readinessProvider = readinessProvider,
                ),
            dispatcher =
                RpcDispatcher(
                    runtimeAccess = runtimeAccess,
                    methodCatalog =
                        RpcMethodCatalog(
                            listOf(
                                MetaGetMethod(versionProvider),
                                AppsListMethod(appsListProvider),
                                EventsPollMethod(eventsPollProvider),
                                SnapshotGetMethod(snapshotExecutionFactory = {
                                    {
                                        snapshotPublication(snapshotId = SnapshotRegistry.nextSnapshotId())
                                    }
                                }),
                                ActionPerformMethod {
                                    {
                                        ActionResult(
                                            actionId = "act-00001",
                                            status = ActionResultStatus.Done,
                                            durationMs = 0L,
                                            resolvedTarget = ActionTarget.None,
                                            observed = ObservedWindowState(),
                                        )
                                    }
                                },
                                ScreenshotCaptureMethod {
                                    {
                                        ScreenshotResponse(
                                            contentType = "image/png",
                                            widthPx = 1,
                                            heightPx = 1,
                                            bodyBase64 = "AA==",
                                        )
                                    }
                                },
                            ),
                        ),
                    executionRunner = RpcExecutionRunner(methodExecutor),
                ),
            methodExecutor = methodExecutor,
        )
    }

    private fun fakeRuntimeAccess(
        readinessProvider: () -> RuntimeReadiness,
        accessibilityService: AccessibilityService? = null,
    ): RuntimeAccess =
        object : RuntimeAccess {
            override fun readiness(): RuntimeReadiness = readinessProvider()

            override fun currentDeviceToken(): String = "device-token"

            override fun applicationContext() = null

            override fun currentAccessibilityService() = accessibilityService
        }

    private class RejectingExecutorService : AbstractExecutorService() {
        override fun shutdown() = Unit

        override fun shutdownNow(): MutableList<Runnable> = mutableListOf()

        override fun isShutdown(): Boolean = false

        override fun isTerminated(): Boolean = false

        override fun awaitTermination(
            timeout: Long,
            unit: TimeUnit,
        ): Boolean = true

        override fun execute(command: Runnable): Unit = throw RejectedExecutionException("busy")
    }

    private class InterruptedExecutorService : AbstractExecutorService() {
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

                override fun get(): T = throw InterruptedException("interrupted")

                override fun get(
                    timeout: Long,
                    unit: TimeUnit,
                ): T = throw InterruptedException("interrupted")
            }
    }

    private class CancelledExecutorService : AbstractExecutorService() {
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

                override fun isCancelled(): Boolean = true

                override fun isDone(): Boolean = true

                override fun get(): T = throw java.util.concurrent.CancellationException("cancelled")

                override fun get(
                    timeout: Long,
                    unit: TimeUnit,
                ): T = throw java.util.concurrent.CancellationException("cancelled")
            }
    }

    private class RecordingShutdownExecutorService(
        private val awaitResult: Boolean = false,
        private val awaitError: InterruptedException? = null,
    ) : AbstractExecutorService() {
        var shutdownCalls: Int = 0
        var shutdownNowCalls: Int = 0
        val awaitCalls = mutableListOf<Pair<Long, TimeUnit>>()

        override fun shutdown() {
            shutdownCalls += 1
        }

        override fun shutdownNow(): MutableList<Runnable> {
            shutdownNowCalls += 1
            return mutableListOf()
        }

        override fun isShutdown(): Boolean = shutdownCalls > 0

        override fun isTerminated(): Boolean = awaitResult

        override fun awaitTermination(
            timeout: Long,
            unit: TimeUnit,
        ): Boolean {
            awaitCalls += timeout to unit
            awaitError?.let { throw it }
            return awaitResult
        }

        override fun execute(command: Runnable) {
            command.run()
        }
    }

    private fun snapshotPublication(
        snapshotId: Long,
        generation: Long = SnapshotRegistry.currentGeneration(),
    ): SnapshotPublication {
        val payload =
            SnapshotPayload(
                snapshotId = snapshotId,
                capturedAt = "2026-03-26T00:00:00Z",
                packageName = "com.android.settings",
                activityName = "SettingsActivity",
                display = SnapshotDisplay(widthPx = 1080, heightPx = 2400, densityDpi = 420, rotation = 0),
                ime = SnapshotIme(visible = false, windowId = null),
                windows =
                    listOf(
                        SnapshotWindow(
                            windowId = "w1",
                            type = "application",
                            layer = 0,
                            packageName = "com.android.settings",
                            bounds = listOf(0, 0, 1080, 2400),
                            rootRid = "w1:0",
                        ),
                    ),
                nodes =
                    listOf(
                        SnapshotNode(
                            rid = "w1:0",
                            windowId = "w1",
                            parentRid = null,
                            childRids = emptyList(),
                            className = "android.widget.FrameLayout",
                            resourceId = "android:id/content",
                            text = null,
                            contentDesc = null,
                            hintText = null,
                            stateDescription = null,
                            paneTitle = null,
                            packageName = "com.android.settings",
                            bounds = listOf(0, 0, 1080, 2400),
                            visibleToUser = true,
                            importantForAccessibility = true,
                            clickable = false,
                            enabled = true,
                            editable = false,
                            focusable = false,
                            focused = false,
                            checkable = false,
                            checked = false,
                            selected = false,
                            scrollable = false,
                            password = false,
                            actions = emptyList(),
                        ),
                    ),
            )
        return SnapshotPublication.create(
            response = payload,
            registryRecord =
                SnapshotRecord(
                    snapshotId = snapshotId,
                    ridToHandle =
                        mapOf(
                            "w1:0" to
                                SnapshotNodeHandle(
                                    path = NodePath(windowId = "w1", childIndices = emptyList()),
                                    fingerprint = NodeFingerprint.fromSnapshotNode(payload.nodes.single()),
                                ),
                        ),
                ),
            generation = generation,
        )
    }
}
