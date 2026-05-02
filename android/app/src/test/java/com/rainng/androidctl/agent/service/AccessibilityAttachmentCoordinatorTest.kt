package com.rainng.androidctl.agent.service

import android.content.Context
import com.rainng.androidctl.agent.actions.AccessibilityActionTargetResolver
import com.rainng.androidctl.agent.auth.DeviceTokenLoadResult
import com.rainng.androidctl.agent.bootstrap.AccessibilityBoundExecutionFactory
import com.rainng.androidctl.agent.errors.DeviceRpcException
import com.rainng.androidctl.agent.errors.RpcErrorCode
import com.rainng.androidctl.agent.events.DeviceEventHub
import com.rainng.androidctl.agent.events.EventPollRequest
import com.rainng.androidctl.agent.events.RuntimeStatusPayload
import com.rainng.androidctl.agent.rpc.RpcEnvironment
import com.rainng.androidctl.agent.runtime.AgentRuntimeBridge
import com.rainng.androidctl.agent.snapshot.SnapshotRecord
import com.rainng.androidctl.agent.snapshot.SnapshotRegistry
import com.rainng.androidctl.agent.snapshot.SnapshotResetResult
import com.rainng.androidctl.agent.testsupport.assertActionException
import com.rainng.androidctl.agent.testsupport.mockNode
import com.rainng.androidctl.agent.testsupport.mockService
import com.rainng.androidctl.agent.testsupport.mockWindow
import com.rainng.androidctl.agent.testsupport.snapshotRecord
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test
import org.mockito.Mockito.doReturn
import org.mockito.Mockito.mock
import org.mockito.Mockito.`when`
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class AccessibilityAttachmentCoordinatorTest {
    @Before
    fun setUp() {
        AgentRuntimeBridge.resetForTest()
        DeviceEventHub.resetForTest()
        AccessibilityAttachmentCoordinator.resetForTest()
        SnapshotRegistry.resetForTest()
    }

    @After
    fun tearDown() {
        AgentRuntimeBridge.resetForTest()
        DeviceEventHub.resetForTest()
        AccessibilityAttachmentCoordinator.resetForTest()
        SnapshotRegistry.resetForTest()
    }

    @Test
    fun resetForAttachmentChangeClearsRetainedSnapshotsAndForegroundHints() {
        AgentRuntimeBridge.recordObservedWindowState(
            eventType = android.view.accessibility.AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
            packageName = "com.android.settings",
            windowClassName = "com.android.settings.Settings",
        )
        val snapshotId = SnapshotRegistry.nextSnapshotId()
        val generation = SnapshotRegistry.currentGeneration()
        publishCurrent(snapshotRecord(snapshotId = snapshotId, rid = "w1:0"))

        AccessibilityAttachmentCoordinator.resetForAttachmentChange()

        assertNull(SnapshotRegistry.find(snapshotId))
        assertEquals(generation + 1L, SnapshotRegistry.currentGeneration())
        assertNull(AgentRuntimeBridge.currentForegroundHintState.fallbackPackageName)
        assertEquals(0L, AgentRuntimeBridge.currentForegroundGeneration)
        assertEquals(snapshotId + 1L, SnapshotRegistry.nextSnapshotId())
    }

    @Test
    fun resetForAttachmentChangeMakesPreviouslyValidHandleStale() {
        val child = mockNode()
        val root = mockNode(children = listOf(child))
        val snapshotId = SnapshotRegistry.nextSnapshotId()
        publishCurrent(
            snapshotRecord(snapshotId = snapshotId, rid = "w1:0"),
        )
        val service = mockService()
        doReturn(listOf(mockWindow(1, root))).`when`(service).windows
        val resolver = AccessibilityActionTargetResolver(service)

        AccessibilityAttachmentCoordinator.resetForAttachmentChange()

        assertActionException(
            expectedCode = RpcErrorCode.STALE_TARGET,
            expectedMessage = "snapshot handle is stale",
            expectedRetryable = true,
        ) {
            resolver.withResolvedNode(snapshotId, "w1:0") { "done" }
        }
    }

    @Test
    fun resetForAttachmentChangeMakesPreResetHandleStaleWhileWaitingForActivePublication() {
        val olderSnapshotId = SnapshotRegistry.nextSnapshotId()
        publishCurrent(
            snapshotRecord(snapshotId = olderSnapshotId, rid = "w1:0"),
        )
        val activePublication =
            SnapshotRegistry.beginPublicationIfCurrent(
                SnapshotRegistry.currentGeneration(),
                snapshotRecord(snapshotId = SnapshotRegistry.nextSnapshotId(), rid = "w2:0"),
            )
        assertNotNull(activePublication)
        val resetStarted = CountDownLatch(1)
        val resetFinished = CountDownLatch(1)
        val service = mockService()
        doReturn(listOf(mockWindow(1, mockNode()))).`when`(service).windows
        val resolver = AccessibilityActionTargetResolver(service)

        val resetThread =
            Thread {
                resetStarted.countDown()
                AccessibilityAttachmentCoordinator.resetForAttachmentChange()
                resetFinished.countDown()
            }
        resetThread.start()

        assertEquals(true, resetStarted.await(1, TimeUnit.SECONDS))
        waitForActiveResetFence()
        try {
            assertEquals(false, resetFinished.await(100, TimeUnit.MILLISECONDS))

            assertActionException(
                expectedCode = RpcErrorCode.STALE_TARGET,
                expectedMessage = "snapshot handle is stale",
                expectedRetryable = true,
            ) {
                resolver.withResolvedNode(olderSnapshotId, "w1:0") { "done" }
            }
        } finally {
            activePublication?.release()
            assertEquals(true, resetFinished.await(1, TimeUnit.SECONDS))
        }
    }

    @Test
    fun resetForAttachmentChangeRejectsPublicationCapturedBeforeReset() {
        val generation = SnapshotRegistry.currentGeneration()
        val snapshotId = SnapshotRegistry.nextSnapshotId()

        AccessibilityAttachmentCoordinator.resetForAttachmentChange()

        val stalePublication =
            SnapshotRegistry.beginPublicationIfCurrent(
                generation,
                snapshotRecord(snapshotId = snapshotId, rid = "w1:0"),
            )
        val recorded = stalePublication != null
        stalePublication?.release()

        assertEquals(false, recorded)
        assertNull(SnapshotRegistry.find(snapshotId))
    }

    @Test
    fun snapshotResetOccursBeforeEventResetInsideAttachmentCoordinator() {
        val generation = SnapshotRegistry.currentGeneration()
        val snapshotId = SnapshotRegistry.nextSnapshotId()
        var publishedDuringReset = false

        AccessibilityAttachmentCoordinator.eventPipelineReset = {
            val publication =
                SnapshotRegistry.beginPublicationIfCurrent(
                    generation,
                    snapshotRecord(snapshotId = snapshotId, rid = "w1:0"),
                )
            publishedDuringReset = publication != null
            publication?.release()
        }

        AccessibilityAttachmentCoordinator.resetForAttachmentChange()

        assertFalse(publishedDuringReset)
        assertNull(SnapshotRegistry.find(snapshotId))
    }

    @Test
    fun detachRepublishesDisconnectedRuntimeStatusAfterResetBoundary() {
        val context = mock(Context::class.java)
        val applicationContext = mock(Context::class.java)
        val service = mockService()
        `when`(context.applicationContext).thenReturn(applicationContext)
        AgentRuntimeBridge.deviceTokenStoreAccess =
            object : com.rainng.androidctl.agent.runtime.DeviceTokenStoreAccess {
                override fun initialize(context: Context) = Unit

                override fun loadCurrentToken(): DeviceTokenLoadResult = DeviceTokenLoadResult.Available("token-1")

                override fun regenerateToken(): String = "token-2"
            }
        AgentRuntimeBridge.accessibilityServiceEnabledProbe = { true }
        AgentRuntimeBridge.serverRunningProbe = { true }
        AgentRuntimeBridge.initialize(context)
        AccessibilityAttachmentCoordinator.attach(service)
        val connectedPoll = DeviceEventHub.poll(EventPollRequest(afterSeq = 0L, waitMs = 0L, limit = 20))

        AccessibilityAttachmentCoordinator.detach()

        val detachedPoll =
            DeviceEventHub.poll(
                EventPollRequest(
                    afterSeq = connectedPoll.latestSeq,
                    waitMs = 0L,
                    limit = 20,
                ),
            )

        assertEquals(true, detachedPoll.needResync)
        assertEquals(listOf("runtime.status"), detachedPoll.events.map { it.type })
        assertEquals(
            RuntimeStatusPayload(
                serverRunning = true,
                accessibilityEnabled = true,
                accessibilityConnected = false,
                runtimeReady = false,
            ),
            detachedPoll.events.single().data,
        )
    }

    @Test
    fun reattachRejectsBoundRpcDuringResetWindow() {
        val staleService = mockService()
        val replacementService = mockService()
        AccessibilityAttachmentCoordinator.attach(staleService)
        val boundCall =
            AccessibilityBoundExecutionFactory(RpcEnvironment()).bind { service -> service }
        val resetStarted = CountDownLatch(1)
        val allowResetToFinish = CountDownLatch(1)
        val attachFinished = CountDownLatch(1)
        AccessibilityAttachmentCoordinator.snapshotRegistryReset = {
            resetStarted.countDown()
            allowResetToFinish.await(1L, TimeUnit.SECONDS)
            SnapshotResetResult(completed = true, timedOut = false, activePublicationCount = 0, timeoutMs = 0L)
        }

        val attachThread =
            Thread {
                AccessibilityAttachmentCoordinator.attach(replacementService)
                attachFinished.countDown()
            }
        attachThread.start()

        assertEquals(true, resetStarted.await(1L, TimeUnit.SECONDS))
        try {
            try {
                boundCall()
                org.junit.Assert.fail("expected DeviceRpcException")
            } catch (error: DeviceRpcException) {
                assertEquals(RpcErrorCode.RUNTIME_NOT_READY, error.code)
            }
        } finally {
            allowResetToFinish.countDown()
            assertEquals(true, attachFinished.await(1L, TimeUnit.SECONDS))
        }
    }

    @Test
    fun resetForAttachmentChangeDelegatesEventForegroundAndSnapshotResetsThroughNarrowCollaborators() {
        val calls = mutableListOf<String>()
        AccessibilityAttachmentCoordinator.eventPipelineReset = { calls += "events" }
        AccessibilityAttachmentCoordinator.foregroundObservationWriter.recordObservedWindowState(
            eventType = android.view.accessibility.AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
            packageName = "com.android.settings",
            windowClassName = "com.android.settings.Settings",
        )
        AccessibilityAttachmentCoordinator.snapshotRegistryReset = {
            calls += "snapshots"
            SnapshotRegistry.resetSessionState()
        }
        AccessibilityAttachmentCoordinator.foregroundObservationWriter =
            object : com.rainng.androidctl.agent.runtime.ForegroundObservationWriter {
                override fun recordObservedWindowState(
                    eventType: Int,
                    packageName: String?,
                    windowClassName: String?,
                ) {
                    AgentRuntimeBridge.recordObservedWindowState(eventType, packageName, windowClassName)
                }

                override fun reset() {
                    calls += "foreground"
                    AgentRuntimeBridge.resetForegroundObservationState()
                }
            }

        AccessibilityAttachmentCoordinator.resetForAttachmentChange()

        assertEquals(true, calls.contains("foreground"))
        assertEquals(true, calls.indexOf("snapshots") >= 0)
        assertEquals(true, calls.indexOf("events") >= 0)
        assertEquals(true, calls.indexOf("snapshots") < calls.indexOf("events"))
        assertNull(AgentRuntimeBridge.currentForegroundHintState.fallbackPackageName)
        assertEquals(0L, AgentRuntimeBridge.currentForegroundGeneration)
    }

    @Test
    fun resetForAttachmentChangeReportsSnapshotResetTimeoutDiagnostic() {
        val diagnosticResults = mutableListOf<SnapshotResetResult>()
        val timeoutResult =
            SnapshotResetResult(completed = false, timedOut = true, activePublicationCount = 2, timeoutMs = 50L)
        AccessibilityAttachmentCoordinator.snapshotRegistryReset = { timeoutResult }
        AccessibilityAttachmentCoordinator.eventPipelineReset = {}
        AccessibilityAttachmentCoordinator.snapshotResetTimeoutReporter = { result ->
            diagnosticResults += result
        }

        AccessibilityAttachmentCoordinator.resetForAttachmentChange()

        assertEquals(listOf(timeoutResult), diagnosticResults)
    }

    @Test
    fun resetForTestRestoresBridgeBackedForegroundResetBehavior() {
        AccessibilityAttachmentCoordinator.foregroundObservationWriter =
            object : com.rainng.androidctl.agent.runtime.ForegroundObservationWriter {
                override fun recordObservedWindowState(
                    eventType: Int,
                    packageName: String?,
                    windowClassName: String?,
                ) = Unit

                override fun reset() = Unit
            }
        AgentRuntimeBridge.recordObservedWindowState(
            eventType = android.view.accessibility.AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
            packageName = "com.example.override",
            windowClassName = "com.example.override.OverrideActivity",
        )

        AccessibilityAttachmentCoordinator.resetForTest()
        AccessibilityAttachmentCoordinator.resetForAttachmentChange()

        assertNull(AgentRuntimeBridge.currentForegroundHintState.fallbackPackageName)
        assertEquals(0L, AgentRuntimeBridge.currentForegroundGeneration)
    }

    @Test
    fun resetForAttachmentChangeUsesLatestBridgeWriterAfterBridgeReset() {
        AccessibilityAttachmentCoordinator

        AgentRuntimeBridge.resetForTest()
        AgentRuntimeBridge.recordObservedWindowState(
            eventType = android.view.accessibility.AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
            packageName = "com.example.latest",
            windowClassName = "com.example.latest.LatestActivity",
        )

        AccessibilityAttachmentCoordinator.resetForAttachmentChange()

        assertNull(AgentRuntimeBridge.currentForegroundHintState.fallbackPackageName)
        assertEquals(0L, AgentRuntimeBridge.currentForegroundGeneration)
    }

    private fun publishCurrent(record: SnapshotRecord): Boolean {
        val publication =
            SnapshotRegistry.beginPublicationIfCurrent(
                SnapshotRegistry.currentGeneration(),
                record,
            ) ?: return false
        publication.release()
        return true
    }

    private fun waitForActiveResetFence() {
        val deadlineNanos = System.nanoTime() + TimeUnit.SECONDS.toNanos(1)
        while (System.nanoTime() < deadlineNanos) {
            if (SnapshotRegistry.resetInProgressForTest()) {
                return
            }
            Thread.yield()
        }
        throw AssertionError("expected snapshot reset fence to become active")
    }
}
