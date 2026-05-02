package com.rainng.androidctl.agent.rpc

import com.rainng.androidctl.agent.RequestBudgets
import com.rainng.androidctl.agent.errors.RequestValidationException
import com.rainng.androidctl.agent.errors.RpcErrorCode
import com.rainng.androidctl.agent.service.AccessibilityAttachmentCoordinator
import com.rainng.androidctl.agent.snapshot.NodeFingerprint
import com.rainng.androidctl.agent.snapshot.NodePath
import com.rainng.androidctl.agent.snapshot.SnapshotDisplay
import com.rainng.androidctl.agent.snapshot.SnapshotException
import com.rainng.androidctl.agent.snapshot.SnapshotGetRequest
import com.rainng.androidctl.agent.snapshot.SnapshotIme
import com.rainng.androidctl.agent.snapshot.SnapshotNode
import com.rainng.androidctl.agent.snapshot.SnapshotNodeHandle
import com.rainng.androidctl.agent.snapshot.SnapshotPayload
import com.rainng.androidctl.agent.snapshot.SnapshotPublication
import com.rainng.androidctl.agent.snapshot.SnapshotRecord
import com.rainng.androidctl.agent.snapshot.SnapshotRegistry
import com.rainng.androidctl.agent.snapshot.SnapshotResponseCodec
import com.rainng.androidctl.agent.snapshot.SnapshotWindow
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference

class SnapshotGetMethodTest {
    @Before
    fun setUp() {
        SnapshotRegistry.resetForTest()
        AccessibilityAttachmentCoordinator.resetForTest()
    }

    @After
    fun tearDown() {
        SnapshotRegistry.resetForTest()
        AccessibilityAttachmentCoordinator.resetForTest()
    }

    @Test
    fun policyUsesSnapshotDefaults() {
        val method = SnapshotGetMethod(snapshotExecutionFactory = providerFactory { _ -> publication(snapshotId = 42L) })

        assertEquals(true, method.policy.requiresReadyRuntime)
        assertEquals(true, method.policy.requiresAccessibilityHandle)
        assertEquals("SNAPSHOT_UNAVAILABLE", method.policy.timeoutError.name)
        assertEquals("snapshot.get timed out", method.policy.timeoutMessage)
    }

    @Test
    fun prepareUsesExplicitFlags() {
        var includeInvisible: Boolean? = null
        var includeSystemWindows: Boolean? = null
        val method =
            SnapshotGetMethod(
                snapshotExecutionFactory =
                    providerFactory { request ->
                        includeInvisible = request.includeInvisible
                        includeSystemWindows = request.includeSystemWindows
                        publication(snapshotId = 42L)
                    },
            )

        val prepared = method.prepare(request("""{"includeInvisible":false,"includeSystemWindows":false}"""))
        val payload = prepared.executeEncoded()

        assertEquals(RequestBudgets.SNAPSHOT_GET_METHOD_TIMEOUT_MS, prepared.timeoutMs)
        assertEquals(42L, payload.getLong("snapshotId"))
        assertNotNull(SnapshotRegistry.find(42L))
        assertEquals(false, includeInvisible)
        assertEquals(false, includeSystemWindows)
    }

    @Test
    fun prepareUsesRequiredFlags() {
        var includeInvisible: Boolean? = null
        var includeSystemWindows: Boolean? = null
        val method =
            SnapshotGetMethod(
                snapshotExecutionFactory =
                    providerFactory { request ->
                        includeInvisible = request.includeInvisible
                        includeSystemWindows = request.includeSystemWindows
                        publication(snapshotId = 77L)
                    },
            )

        val payload = method.prepare(defaultRequest()).executeEncoded()

        assertEquals(true, includeInvisible)
        assertEquals(true, includeSystemWindows)
        assertEquals(77L, payload.getLong("snapshotId"))
        assertNotNull(SnapshotRegistry.find(77L))
    }

    @Test
    fun prepareKeepsSnapshotPayloadRawOnlyWithoutSemanticScreenFields() {
        val payload =
            SnapshotGetMethod(
                snapshotExecutionFactory =
                    providerFactory { _ ->
                        publication(snapshotId = 42L)
                    },
            ).prepare(defaultRequest()).executeEncoded()
        val encodedText = payload.toString()

        assertTrue(payload.has("windows"))
        assertTrue(payload.has("nodes"))
        assertFalse(payload.has("screenId"))
        assertFalse(payload.has("nextScreenId"))
        assertFalse(payload.has("continuityStatus"))
        assertFalse(encodedText.contains("\"ref\""))
        assertFalse(encodedText.contains("\"blockingGroup\""))
    }

    @Test
    fun prepareBindsSnapshotExecutionFactoryBeforeExecute() {
        var boundSnapshotId = 51L
        val method =
            SnapshotGetMethod(
                snapshotExecutionFactory = {
                    val preparedSnapshotId = boundSnapshotId
                    { publication(snapshotId = preparedSnapshotId) }
                },
            )

        val prepared = method.prepare(defaultRequest())
        boundSnapshotId = 99L
        val payload = prepared.executeEncoded()

        assertEquals(51L, payload.getLong("snapshotId"))
    }

    @Test
    fun preparePublishesNormalizedClassNameForBlankSystemSurfaceNode() {
        val method =
            SnapshotGetMethod(
                snapshotExecutionFactory = providerFactory { _ -> publication(snapshotId = 93L, className = "   ") },
            )

        val payload = method.prepare(defaultRequest()).executeEncoded()
        val snapshotId = payload.getLong("snapshotId")
        val node = payload.getJSONArray("nodes").getJSONObject(0)
        val retained = SnapshotRegistry.find(snapshotId)

        assertEquals("android.view.View", node.getString("className"))
        assertNotNull(retained)
        assertEquals(
            "android.view.View",
            retained
                ?.ridToHandle
                ?.getValue("w1:0")
                ?.fingerprint
                ?.className,
        )
    }

    @Test
    fun prepareRejectsCoerciveBoolean() {
        val method = SnapshotGetMethod(snapshotExecutionFactory = providerFactory { _ -> publication(snapshotId = 42L) })

        try {
            method.prepare(request("""{"includeInvisible":"false","includeSystemWindows":true}"""))
            fail("expected RequestValidationException")
        } catch (error: RequestValidationException) {
            assertEquals("snapshot.get includeInvisible must be a boolean", error.message)
        }
    }

    @Test
    fun prepareRejectsMissingFlags() {
        val method = SnapshotGetMethod(snapshotExecutionFactory = providerFactory { _ -> publication(snapshotId = 42L) })

        try {
            method.prepare(request("""{}"""))
            fail("expected RequestValidationException")
        } catch (error: RequestValidationException) {
            assertEquals("snapshot.get requires includeInvisible", error.message)
        }

        try {
            method.prepare(request("""{"includeInvisible":true}"""))
            fail("expected RequestValidationException")
        } catch (error: RequestValidationException) {
            assertEquals("snapshot.get requires includeSystemWindows", error.message)
        }
    }

    @Test
    fun successfulSnapshotLeavesSnapshotIdImmediatelyResolvable() {
        val method = SnapshotGetMethod(snapshotExecutionFactory = providerFactory { _ -> publication(snapshotId = 88L) })

        val payload = method.prepare(defaultRequest()).executeEncoded()

        val snapshotId = payload.getLong("snapshotId")
        assertEquals(88L, snapshotId)
        assertNotNull(SnapshotRegistry.find(snapshotId))
    }

    @Test
    fun prepareDoesNotInvokeSnapshotEncoding() {
        var encoded = false
        var providerCalls = 0
        val method =
            SnapshotGetMethod(
                snapshotExecutionFactory =
                    providerFactory { _ ->
                        providerCalls += 1
                        publication(snapshotId = 92L)
                    },
                responseEncoder = { payload ->
                    encoded = true
                    encode(payload)
                },
            )

        val prepared = method.prepare(defaultRequest())
        assertFalse(encoded)
        assertEquals(0, providerCalls)
        prepared.executeEncoded()
        assertTrue(encoded)
        assertEquals(1, providerCalls)
    }

    @Test
    fun resetBeforePublicationPreventsPublication() {
        val generation = SnapshotRegistry.currentGeneration()
        val resetFinished = CountDownLatch(1)
        val method =
            SnapshotGetMethod(
                snapshotExecutionFactory =
                    providerFactory { _ ->
                        val resetThread =
                            Thread {
                                AccessibilityAttachmentCoordinator.resetForAttachmentChange()
                                resetFinished.countDown()
                            }
                        resetThread.start()
                        assertTrue(resetFinished.await(1, TimeUnit.SECONDS))
                        publication(snapshotId = 91L, generation = generation)
                    },
            )

        assertSnapshotUnavailable { method.prepare(defaultRequest()).executeEncoded() }
        assertEquals(null, SnapshotRegistry.find(91L))
    }

    @Test
    fun resetDuringResponsePreparationBlocksResetUntilEncodingReturns() {
        val encodingStarted = CountDownLatch(1)
        val allowEncodingFinish = CountDownLatch(1)
        val resetFinished = CountDownLatch(1)
        val method =
            SnapshotGetMethod(
                snapshotExecutionFactory = providerFactory { _ -> publication(snapshotId = 92L) },
                responseEncoder = { payload: SnapshotPayload ->
                    encodingStarted.countDown()
                    val snapshotVisibleDuringEncode = SnapshotRegistry.find(payload.snapshotId) != null
                    assertEquals(true, snapshotVisibleDuringEncode)
                    assertTrue(allowEncodingFinish.await(1, TimeUnit.SECONDS))
                    encode(payload)
                },
            )

        val payloadRef = AtomicReference<JSONObject?>(null)
        val executeThread =
            Thread {
                payloadRef.set(method.prepare(defaultRequest()).executeEncoded())
            }
        executeThread.start()
        assertTrue(encodingStarted.await(1, TimeUnit.SECONDS))

        val resetThread =
            Thread {
                AccessibilityAttachmentCoordinator.resetForAttachmentChange()
                resetFinished.countDown()
            }
        resetThread.start()

        assertFalse(resetFinished.await(100, TimeUnit.MILLISECONDS))
        allowEncodingFinish.countDown()
        executeThread.join(TimeUnit.SECONDS.toMillis(1))

        val payload = payloadRef.get()
        assertNotNull(payload)
        assertEquals(92L, payload?.getLong("snapshotId"))
        assertTrue(resetFinished.await(1, TimeUnit.SECONDS))
        assertEquals(null, SnapshotRegistry.find(92L))
    }

    @Test
    fun resetAfterPublicationCannotClearSnapshotBeforeEncodingReturns() {
        val resetStarted = CountDownLatch(1)
        val resetFinished = CountDownLatch(1)
        var snapshotVisibleDuringEncode: Boolean? = null
        val method =
            SnapshotGetMethod(
                snapshotExecutionFactory = providerFactory { _ -> publication(snapshotId = 92L) },
                responseEncoder = { payload: SnapshotPayload ->
                    val resetThread =
                        Thread {
                            resetStarted.countDown()
                            AccessibilityAttachmentCoordinator.resetForAttachmentChange()
                            resetFinished.countDown()
                        }
                    resetThread.start()
                    assertTrue(resetStarted.await(1, TimeUnit.SECONDS))
                    snapshotVisibleDuringEncode = SnapshotRegistry.find(payload.snapshotId) != null
                    assertFalse(resetFinished.await(100, TimeUnit.MILLISECONDS))
                    encode(payload)
                },
            )

        val payload = method.prepare(defaultRequest()).executeEncoded()

        assertEquals(92L, payload.getLong("snapshotId"))
        assertEquals(true, snapshotVisibleDuringEncode)
        assertTrue(resetFinished.await(1, TimeUnit.SECONDS))
        assertEquals(null, SnapshotRegistry.find(92L))
    }

    private fun assertSnapshotUnavailable(block: () -> Unit) {
        try {
            block()
            fail("expected SnapshotException")
        } catch (error: SnapshotException) {
            assertEquals(RpcErrorCode.SNAPSHOT_UNAVAILABLE, error.code)
            assertEquals("snapshot publication raced with session reset", error.message)
            assertTrue(error.retryable)
        }
    }

    private fun request(params: String): RpcRequestEnvelope =
        RpcRequestEnvelope(
            id = "req-snapshot",
            method = "snapshot.get",
            params = JSONObject(params),
        )

    private fun defaultRequest(): RpcRequestEnvelope = request("""{"includeInvisible":true,"includeSystemWindows":true}""")

    private fun providerFactory(provider: (SnapshotGetRequest) -> SnapshotPublication): (SnapshotGetRequest) -> () -> SnapshotPublication =
        { request -> { provider(request) } }

    private fun publication(
        snapshotId: Long,
        className: String = "android.widget.FrameLayout",
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
                            className = className,
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

    private fun encode(payload: SnapshotPayload): JSONObject {
        val writer =
            com.rainng.androidctl.agent.rpc.codec.JsonWriter
                .objectWriter()
        SnapshotResponseCodec.write(writer, payload)
        return writer.toJsonObject()
    }
}
