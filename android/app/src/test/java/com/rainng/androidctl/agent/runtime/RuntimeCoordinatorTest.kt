package com.rainng.androidctl.agent.runtime

import android.accessibilityservice.AccessibilityService
import android.content.Context
import android.view.accessibility.AccessibilityEvent
import com.rainng.androidctl.agent.auth.DEVICE_TOKEN_DECRYPT_FAILED_MESSAGE
import com.rainng.androidctl.agent.auth.DeviceTokenLoadResult
import com.rainng.androidctl.agent.events.TestClock
import com.rainng.androidctl.agent.events.TestCooldownScheduler
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test
import org.mockito.Mockito.mock
import org.mockito.Mockito.`when`
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

class RuntimeCoordinatorTest {
    private val publishedStates = CopyOnWriteArrayList<AgentRuntimeState>()
    private val contextStore = RuntimeContextStore()
    private val factsStore = RuntimeFactsStore()
    private val statusStore = RuntimeStatusStore(runtimeStateRecorder = publishedStates::add)
    private val mutationLock = RuntimeMutationLock()

    @Test
    fun initializeStoresApplicationContextLoadsTokenAndAccessibilityState() {
        val context = mock(Context::class.java)
        val applicationContext = mock(Context::class.java)
        `when`(context.applicationContext).thenReturn(applicationContext)

        var initializedContext: Context? = null
        val coordinator =
            newCoordinator(
                deviceTokenStoreAccess =
                    object : DeviceTokenStoreAccess {
                        override fun initialize(context: Context) {
                            initializedContext = context
                        }

                        override fun loadCurrentToken(): DeviceTokenLoadResult = DeviceTokenLoadResult.Available("token-1")

                        override fun regenerateToken(): String = "unused"
                    },
                accessibilityProbe = { observedContext ->
                    assertSame(applicationContext, observedContext)
                    true
                },
                serverProbe = { observedContext ->
                    assertSame(applicationContext, observedContext)
                    false
                },
            )

        coordinator.initialize(context)

        assertSame(applicationContext, contextStore.applicationContext())
        assertSame(applicationContext, initializedContext)
        with(statusStore.currentState()) {
            assertEquals("token-1", deviceToken)
            assertFalse(serverRunning)
            assertEquals(ServerPhase.STOPPED, serverPhase)
            assertTrue(accessibilityEnabled)
            assertFalse(runtimeReady)
        }
        assertEquals(2, publishedStates.size)
    }

    @Test
    fun repeatedInitializePreservesExistingRunningConnectedRuntimeAuthState() {
        val firstContext = mock(Context::class.java)
        val firstApplicationContext = mock(Context::class.java)
        `when`(firstContext.applicationContext).thenReturn(firstApplicationContext)
        val secondContext = mock(Context::class.java)
        val secondApplicationContext = mock(Context::class.java)
        `when`(secondContext.applicationContext).thenReturn(secondApplicationContext)
        val service = mock(AccessibilityService::class.java)

        var initializeCount = 0
        var loadCount = 0
        val coordinator =
            newCoordinator(
                deviceTokenStoreAccess =
                    object : DeviceTokenStoreAccess {
                        override fun initialize(context: Context) {
                            initializeCount += 1
                        }

                        override fun loadCurrentToken(): DeviceTokenLoadResult = DeviceTokenLoadResult.Available("token-${++loadCount}")

                        override fun regenerateToken(): String = "unused"
                    },
                accessibilityProbe = { true },
                serverProbe = { true },
            )
        val attachmentController = newAttachmentController(coordinator)

        coordinator.initialize(firstContext)
        coordinator.markServerRunning()
        attachmentController.registerAccessibilityService(service)
        publishedStates.clear()

        coordinator.initialize(secondContext)

        assertSame(secondApplicationContext, contextStore.applicationContext())
        assertSame(service, contextStore.currentAccessibilityAttachmentHandle().service)
        assertEquals(2, initializeCount)
        assertEquals(1, loadCount)
        with(statusStore.currentState()) {
            assertEquals("token-1", deviceToken)
            assertEquals(ServerPhase.RUNNING, serverPhase)
            assertTrue(serverRunning)
            assertTrue(accessibilityEnabled)
            assertTrue(accessibilityConnected)
            assertTrue(runtimeReady)
        }
        assertEquals(1, publishedStates.size)
    }

    @Test
    fun repeatedInitializePreservesBlockedAuthUntilExplicitRegeneration() {
        val firstContext = mock(Context::class.java)
        val firstApplicationContext = mock(Context::class.java)
        `when`(firstContext.applicationContext).thenReturn(firstApplicationContext)
        val secondContext = mock(Context::class.java)
        val secondApplicationContext = mock(Context::class.java)
        `when`(secondContext.applicationContext).thenReturn(secondApplicationContext)

        var initializeCount = 0
        var loadCount = 0
        val coordinator =
            newCoordinator(
                deviceTokenStoreAccess =
                    object : DeviceTokenStoreAccess {
                        override fun initialize(context: Context) {
                            initializeCount += 1
                        }

                        override fun loadCurrentToken(): DeviceTokenLoadResult =
                            DeviceTokenLoadResult.Blocked(
                                DEVICE_TOKEN_DECRYPT_FAILED_MESSAGE.also { loadCount += 1 },
                            )

                        override fun regenerateToken(): String = "token-2"
                    },
                accessibilityProbe = { true },
                serverProbe = { true },
            )

        coordinator.initialize(firstContext)
        publishedStates.clear()

        coordinator.initialize(secondContext)

        assertSame(secondApplicationContext, contextStore.applicationContext())
        assertEquals(2, initializeCount)
        assertEquals(1, loadCount)
        with(statusStore.currentState()) {
            assertEquals("", deviceToken)
            assertEquals(DEVICE_TOKEN_DECRYPT_FAILED_MESSAGE, authBlockedMessage)
            assertEquals(DEVICE_TOKEN_DECRYPT_FAILED_MESSAGE, lastError)
            assertEquals(ServerPhase.RUNNING, serverPhase)
            assertTrue(serverRunning)
            assertTrue(accessibilityEnabled)
            assertFalse(runtimeReady)
        }
        assertEquals(1, publishedStates.size)

        coordinator.regenerateDeviceToken()

        with(statusStore.currentState()) {
            assertEquals("token-2", deviceToken)
            assertEquals(null, authBlockedMessage)
            assertEquals(null, lastError)
        }
    }

    @Test
    fun initializePublishesLoadedAuthStateBeforeConcurrentServerMutationPublishesRunning() {
        val context = mock(Context::class.java)
        val applicationContext = mock(Context::class.java)
        `when`(context.applicationContext).thenReturn(applicationContext)
        val loadStarted = CountDownLatch(1)
        val allowLoadFinish = CountDownLatch(1)
        val markServerRunningAttempted = CountDownLatch(1)
        val loadedAuthPublished = CountDownLatch(1)
        val allowLoadedAuthPublicationToComplete = CountDownLatch(1)
        val runningStatePublished = CountDownLatch(1)
        val blockFirstLoadedAuthPublication = AtomicBoolean(true)
        val publicationOrder = CopyOnWriteArrayList<String>()
        statusStore.runtimeStateRecorder = { state ->
            publishedStates += state
            if (state.deviceToken == "token-1" && blockFirstLoadedAuthPublication.compareAndSet(true, false)) {
                publicationOrder += "loaded-auth"
                loadedAuthPublished.countDown()
                assertTrue(allowLoadedAuthPublicationToComplete.await(5, TimeUnit.SECONDS))
            }
            if (state.serverPhase == ServerPhase.RUNNING) {
                publicationOrder += "running"
                runningStatePublished.countDown()
            }
        }
        val coordinator =
            newCoordinator(
                deviceTokenStoreAccess =
                    object : DeviceTokenStoreAccess {
                        override fun initialize(context: Context) = Unit

                        override fun loadCurrentToken(): DeviceTokenLoadResult {
                            loadStarted.countDown()
                            assertTrue(allowLoadFinish.await(5, TimeUnit.SECONDS))
                            return DeviceTokenLoadResult.Available("token-1")
                        }

                        override fun regenerateToken(): String = "token-2"
                    },
                accessibilityProbe = { true },
                serverProbe = { false },
            )

        val initializeThread = Thread { coordinator.initialize(context) }
        initializeThread.start()
        assertTrue(loadStarted.await(5, TimeUnit.SECONDS))

        val markServerRunningThread =
            Thread {
                markServerRunningAttempted.countDown()
                coordinator.markServerRunning()
            }
        markServerRunningThread.start()

        assertTrue(markServerRunningAttempted.await(5, TimeUnit.SECONDS))
        allowLoadFinish.countDown()
        assertTrue(loadedAuthPublished.await(5, TimeUnit.SECONDS))
        assertEquals(listOf("loaded-auth"), publicationOrder)
        assertFalse(publishedStates.any { it.serverPhase == ServerPhase.RUNNING })

        allowLoadedAuthPublicationToComplete.countDown()

        initializeThread.join(5000)
        markServerRunningThread.join(5000)

        assertFalse(initializeThread.isAlive)
        assertFalse(markServerRunningThread.isAlive)
        assertTrue(runningStatePublished.await(5, TimeUnit.SECONDS))
        assertEquals(listOf("loaded-auth", "running"), publicationOrder)
        with(publishedStates.last { it.serverPhase == ServerPhase.RUNNING }) {
            assertEquals("token-1", deviceToken)
            assertTrue(serverRunning)
        }
        with(statusStore.currentState()) {
            assertEquals("token-1", deviceToken)
            assertEquals(ServerPhase.RUNNING, serverPhase)
        }
    }

    @Test
    fun registerAccessibilityServiceBlocksConcurrentMarkServerRunningUntilAttachmentRefreshCompletes() {
        val service = mock(AccessibilityService::class.java)
        val attachmentRefreshEntered = CountDownLatch(1)
        val allowAttachmentRefresh = CountDownLatch(1)
        val markServerRunningAttempted = CountDownLatch(1)
        val runningStatePublished = CountDownLatch(1)
        statusStore.runtimeStateRecorder = { state ->
            publishedStates += state
            if (state.serverPhase == ServerPhase.RUNNING) {
                runningStatePublished.countDown()
            }
        }
        val coordinator =
            newCoordinator(
                accessibilityProbe = { true },
                serverProbe = { false },
            )
        val attachmentController =
            RuntimeAttachmentController(
                contextStore = contextStore,
                statusStore = statusStore,
                mutationLock = mutationLock,
                refreshRuntimeInputs = { accessibilityConnected, baseState ->
                    attachmentRefreshEntered.countDown()
                    assertTrue(allowAttachmentRefresh.await(5, TimeUnit.SECONDS))
                    coordinator.refreshRuntimeInputs(
                        accessibilityConnected = accessibilityConnected,
                        baseState = baseState,
                    )
                },
            )

        val registerThread =
            Thread {
                attachmentController.registerAccessibilityService(service)
            }
        registerThread.start()
        assertTrue(attachmentRefreshEntered.await(5, TimeUnit.SECONDS))

        val markServerRunningThread =
            Thread {
                markServerRunningAttempted.countDown()
                coordinator.markServerRunning()
            }
        markServerRunningThread.start()

        assertTrue(markServerRunningAttempted.await(5, TimeUnit.SECONDS))
        assertFalse(runningStatePublished.await(200, TimeUnit.MILLISECONDS))

        allowAttachmentRefresh.countDown()

        registerThread.join(5000)
        markServerRunningThread.join(5000)

        assertFalse(registerThread.isAlive)
        assertFalse(markServerRunningThread.isAlive)
        assertTrue(runningStatePublished.await(5, TimeUnit.SECONDS))
        assertEquals(ServerPhase.RUNNING, statusStore.currentState().serverPhase)
        assertTrue(statusStore.currentInputs().accessibilityConnected)
    }

    @Test
    fun recordAndResetForegroundObservationDelegateThroughCoordinatorOwnedManager() {
        val coordinator = newCoordinator()
        val initialState = statusStore.currentState()
        val initialInputs = statusStore.currentInputs()

        coordinator.recordObservedWindowState(
            eventType = AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
            packageName = "com.android.settings",
            windowClassName = "com.android.settings.Settings\$WifiSettingsActivity",
        )

        with(factsStore.current().foreground) {
            assertEquals("com.android.settings", hintPackageName)
            assertEquals("com.android.settings.Settings\$WifiSettingsActivity", hintActivityName)
            assertEquals(1L, generation)
        }
        assertEquals(initialState, statusStore.currentState())
        assertEquals(initialInputs, statusStore.currentInputs())
        assertEquals(0, publishedStates.size)

        coordinator.resetForegroundObservationState()

        with(factsStore.current().foreground) {
            assertNull(hintPackageName)
            assertNull(hintActivityName)
            assertEquals(0L, generation)
        }
        assertEquals(initialState, statusStore.currentState())
        assertEquals(initialInputs, statusStore.currentInputs())
        assertEquals(0, publishedStates.size)
    }

    @Test
    fun serverLifecycleMethodsPublishExpectedPhaseSequence() {
        val coordinator = newCoordinator()
        val attachmentController = newAttachmentController(coordinator)
        val service = mock(AccessibilityService::class.java)

        coordinator.markServerRunning()
        attachmentController.registerAccessibilityService(service)
        publishedStates.clear()

        coordinator.markServerStopping()
        coordinator.markServerStopped()

        assertEquals(2, publishedStates.size)
        with(publishedStates[0]) {
            assertEquals(ServerPhase.STOPPING, serverPhase)
            assertFalse(serverRunning)
            assertFalse(accessibilityConnected)
            assertFalse(runtimeReady)
        }
        with(publishedStates[1]) {
            assertEquals(ServerPhase.STOPPED, serverPhase)
            assertFalse(serverRunning)
            assertFalse(accessibilityConnected)
            assertFalse(runtimeReady)
        }
    }

    @Test
    fun registerAndUnregisterAccessibilityServicePublishCompatibleReadinessStates() {
        val context = mock(Context::class.java)
        val applicationContext = mock(Context::class.java)
        val service = mock(AccessibilityService::class.java)
        `when`(context.applicationContext).thenReturn(applicationContext)
        val coordinator =
            newCoordinator(
                deviceTokenStoreAccess =
                    object : DeviceTokenStoreAccess {
                        override fun initialize(context: Context) = Unit

                        override fun loadCurrentToken(): DeviceTokenLoadResult = DeviceTokenLoadResult.Available("token-1")

                        override fun regenerateToken(): String = "token-2"
                    },
                accessibilityProbe = { true },
                serverProbe = { true },
            )
        val attachmentController = newAttachmentController(coordinator)

        coordinator.initialize(context)
        coordinator.markServerRunning()
        publishedStates.clear()

        attachmentController.registerAccessibilityService(service)
        attachmentController.unregisterAccessibilityService()

        assertEquals(2, publishedStates.size)
        with(publishedStates[0]) {
            assertTrue(accessibilityEnabled)
            assertTrue(accessibilityConnected)
            assertEquals(ServerPhase.RUNNING, serverPhase)
            assertTrue(runtimeReady)
        }
        with(publishedStates[1]) {
            assertTrue(accessibilityEnabled)
            assertFalse(accessibilityConnected)
            assertEquals(ServerPhase.RUNNING, serverPhase)
            assertFalse(runtimeReady)
        }
    }

    @Test
    fun registerAndUnregisterAccessibilityServiceAdvanceAttachmentHandleSnapshot() {
        val service = mock(AccessibilityService::class.java)
        val replacementService = mock(AccessibilityService::class.java)
        val coordinator = newCoordinator(accessibilityProbe = { true }, serverProbe = { true })
        val attachmentController = newAttachmentController(coordinator)

        attachmentController.registerAccessibilityService(service)
        val connectedHandle = contextStore.currentAccessibilityAttachmentHandle()
        attachmentController.unregisterAccessibilityService()
        val disconnectedHandle = contextStore.currentAccessibilityAttachmentHandle()
        attachmentController.registerAccessibilityService(replacementService)
        val reconnectedHandle = contextStore.currentAccessibilityAttachmentHandle()

        assertSame(service, connectedHandle.service)
        assertEquals(1L, connectedHandle.generation)
        assertFalse(connectedHandle.revoked)
        assertNull(disconnectedHandle.service)
        assertEquals(2L, disconnectedHandle.generation)
        assertTrue(disconnectedHandle.revoked)
        assertSame(replacementService, reconnectedHandle.service)
        assertEquals(3L, reconnectedHandle.generation)
        assertFalse(reconnectedHandle.revoked)
    }

    @Test
    fun invalidateAccessibilityAttachmentHandleRevokesCurrentHandleWithoutPublishingDisconnectedState() {
        val context = mock(Context::class.java)
        val applicationContext = mock(Context::class.java)
        val service = mock(AccessibilityService::class.java)
        `when`(context.applicationContext).thenReturn(applicationContext)
        val coordinator =
            newCoordinator(
                deviceTokenStoreAccess =
                    object : DeviceTokenStoreAccess {
                        override fun initialize(context: Context) = Unit

                        override fun loadCurrentToken(): DeviceTokenLoadResult = DeviceTokenLoadResult.Available("token-1")

                        override fun regenerateToken(): String = "token-2"
                    },
                accessibilityProbe = { true },
                serverProbe = { true },
            )
        val attachmentController = newAttachmentController(coordinator)

        coordinator.initialize(context)
        coordinator.markServerRunning()
        attachmentController.registerAccessibilityService(service)
        publishedStates.clear()

        attachmentController.invalidateAccessibilityAttachmentHandle()

        with(contextStore.currentAccessibilityAttachmentHandle()) {
            assertNull(this.service)
            assertTrue(revoked)
        }
        with(statusStore.currentState()) {
            assertTrue(accessibilityConnected)
            assertTrue(runtimeReady)
        }
        assertEquals(0, publishedStates.size)
    }

    @Test
    fun registerAccessibilityServiceKeepsProbeOwnedEnabledFalseAndPublishesMaskedDisconnectedState() {
        val context = mock(Context::class.java)
        val applicationContext = mock(Context::class.java)
        val service = mock(AccessibilityService::class.java)
        `when`(context.applicationContext).thenReturn(applicationContext)
        val coordinator =
            newCoordinator(
                deviceTokenStoreAccess =
                    object : DeviceTokenStoreAccess {
                        override fun initialize(context: Context) = Unit

                        override fun loadCurrentToken(): DeviceTokenLoadResult = DeviceTokenLoadResult.Available("token-1")

                        override fun regenerateToken(): String = "token-2"
                    },
                accessibilityProbe = { false },
                serverProbe = { true },
            )
        val attachmentController = newAttachmentController(coordinator)

        coordinator.initialize(context)
        coordinator.markServerRunning()
        attachmentController.registerAccessibilityService(service)
        assertSame(service, contextStore.currentAccessibilityAttachmentHandle().service)
        with(statusStore.currentInputs()) {
            assertFalse("enabled should remain probe-owned when the probe is false", accessibilityEnabled)
            assertTrue("connected should retain lifecycle truth after registration", accessibilityConnected)
            assertEquals(ServerPhase.RUNNING, serverPhase)
        }
        with(statusStore.currentState()) {
            assertFalse("published enabled should reflect the false probe", accessibilityEnabled)
            assertFalse("published connected should be masked off when enabled is false", accessibilityConnected)
            assertEquals(ServerPhase.RUNNING, serverPhase)
            assertFalse(runtimeReady)
        }
        with(publishedStates.last()) {
            assertFalse(accessibilityEnabled)
            assertFalse(accessibilityConnected)
            assertFalse(runtimeReady)
        }
    }

    @Test
    fun registerAccessibilityServiceReconcilesToReadyAfterDelayedVerificationSucceeds() {
        val context = mock(Context::class.java)
        val applicationContext = mock(Context::class.java)
        val service = mock(AccessibilityService::class.java)
        val clock = TestClock()
        val scheduler = TestCooldownScheduler(clock)
        var probeEnabled = false
        `when`(context.applicationContext).thenReturn(applicationContext)
        val coordinator =
            newCoordinator(
                deviceTokenStoreAccess =
                    object : DeviceTokenStoreAccess {
                        override fun initialize(context: Context) = Unit

                        override fun loadCurrentToken(): DeviceTokenLoadResult = DeviceTokenLoadResult.Available("token-1")

                        override fun regenerateToken(): String = "token-2"
                    },
                accessibilityProbe = { probeEnabled },
                serverProbe = { true },
            )
        val attachmentController = newAttachmentController(coordinator, scheduler)

        coordinator.initialize(context)
        coordinator.markServerRunning()
        attachmentController.registerAccessibilityService(service)

        with(statusStore.currentState()) {
            assertFalse(accessibilityEnabled)
            assertFalse(accessibilityConnected)
            assertFalse(runtimeReady)
        }
        assertEquals(1, scheduler.pendingTaskCount)

        probeEnabled = true
        scheduler.advanceBy(100L)

        with(statusStore.currentState()) {
            assertTrue(accessibilityEnabled)
            assertTrue(accessibilityConnected)
            assertTrue(runtimeReady)
        }
        assertEquals(0, scheduler.pendingTaskCount)
    }

    @Test
    fun unregisterAccessibilityServiceCancelsPendingVerification() {
        val context = mock(Context::class.java)
        val applicationContext = mock(Context::class.java)
        val service = mock(AccessibilityService::class.java)
        val clock = TestClock()
        val scheduler = TestCooldownScheduler(clock)
        var probeEnabled = false
        `when`(context.applicationContext).thenReturn(applicationContext)
        val coordinator =
            newCoordinator(
                deviceTokenStoreAccess =
                    object : DeviceTokenStoreAccess {
                        override fun initialize(context: Context) = Unit

                        override fun loadCurrentToken(): DeviceTokenLoadResult = DeviceTokenLoadResult.Available("token-1")

                        override fun regenerateToken(): String = "token-2"
                    },
                accessibilityProbe = { probeEnabled },
                serverProbe = { true },
            )
        val attachmentController = newAttachmentController(coordinator, scheduler)

        coordinator.initialize(context)
        coordinator.markServerRunning()
        attachmentController.registerAccessibilityService(service)
        assertEquals(1, scheduler.pendingTaskCount)

        attachmentController.unregisterAccessibilityService()
        probeEnabled = true
        scheduler.advanceBy(1000L)

        assertEquals(0, scheduler.pendingTaskCount)
        assertTrue(scheduler.cancelledTaskCount >= 1)
        with(statusStore.currentState()) {
            assertFalse(accessibilityConnected)
            assertFalse(runtimeReady)
        }
    }

    @Test
    fun delayedVerificationPreservesErrorsRecordedAfterAttach() {
        val context = mock(Context::class.java)
        val applicationContext = mock(Context::class.java)
        val service = mock(AccessibilityService::class.java)
        val clock = TestClock()
        val scheduler = TestCooldownScheduler(clock)
        var probeEnabled = false
        `when`(context.applicationContext).thenReturn(applicationContext)
        val coordinator =
            newCoordinator(
                deviceTokenStoreAccess =
                    object : DeviceTokenStoreAccess {
                        override fun initialize(context: Context) = Unit

                        override fun loadCurrentToken(): DeviceTokenLoadResult = DeviceTokenLoadResult.Available("token-1")

                        override fun regenerateToken(): String = "token-2"
                    },
                accessibilityProbe = { probeEnabled },
                serverProbe = { true },
            )
        val attachmentController = newAttachmentController(coordinator, scheduler)

        coordinator.initialize(context)
        coordinator.markServerRunning()
        attachmentController.registerAccessibilityService(service)
        coordinator.recordError("request failed")

        probeEnabled = true
        scheduler.advanceBy(100L)

        with(statusStore.currentState()) {
            assertEquals("request failed", lastError)
            assertTrue(accessibilityEnabled)
            assertTrue(accessibilityConnected)
            assertTrue(runtimeReady)
        }
        assertEquals(0, scheduler.pendingTaskCount)
    }

    @Test
    fun unregisterThenReconcileFallsBackToProbeFalseTruth() {
        val context = mock(Context::class.java)
        val applicationContext = mock(Context::class.java)
        val service = mock(AccessibilityService::class.java)
        `when`(context.applicationContext).thenReturn(applicationContext)
        val coordinator =
            newCoordinator(
                deviceTokenStoreAccess =
                    object : DeviceTokenStoreAccess {
                        override fun initialize(context: Context) = Unit

                        override fun loadCurrentToken(): DeviceTokenLoadResult = DeviceTokenLoadResult.Available("token-1")

                        override fun regenerateToken(): String = "token-2"
                    },
                accessibilityProbe = { false },
                serverProbe = { true },
            )
        val attachmentController = newAttachmentController(coordinator)

        coordinator.initialize(context)
        coordinator.markServerRunning()
        attachmentController.registerAccessibilityService(service)

        attachmentController.unregisterAccessibilityService()
        coordinator.reconcileRuntimeState()

        with(statusStore.currentState()) {
            assertFalse("enabled should drop after unregister + false probe", accessibilityEnabled)
            assertFalse("connected should drop after unregister + false probe", accessibilityConnected)
            assertFalse(runtimeReady)
        }
    }

    @Test
    fun reconcileRuntimeStateUsesProbesAndPreservesDiagnostics() {
        val context = mock(Context::class.java)
        val applicationContext = mock(Context::class.java)
        `when`(context.applicationContext).thenReturn(applicationContext)
        var probeServerRunning = true
        val coordinator =
            newCoordinator(
                deviceTokenStoreAccess =
                    object : DeviceTokenStoreAccess {
                        override fun initialize(context: Context) = Unit

                        override fun loadCurrentToken(): DeviceTokenLoadResult = DeviceTokenLoadResult.Available("token-1")

                        override fun regenerateToken(): String = "token-2"
                    },
                accessibilityProbe = { true },
                serverProbe = { probeServerRunning },
            )

        coordinator.initialize(context)
        coordinator.recordRequestSummary("POST /rpc")
        coordinator.recordError("request failed")

        probeServerRunning = false
        coordinator.reconcileRuntimeState()

        with(statusStore.currentState()) {
            assertEquals("token-1", deviceToken)
            assertEquals("POST /rpc", lastRequestSummary)
            assertEquals("request failed", lastError)
            assertEquals(ServerPhase.STOPPED, serverPhase)
            assertFalse(serverRunning)
            assertTrue(accessibilityEnabled)
            assertFalse(runtimeReady)
        }
    }

    @Test
    fun reconcileRuntimeStateDoesNothingWithoutInitializedContext() {
        val coordinator = newCoordinator()

        coordinator.markServerRunning()
        coordinator.reconcileRuntimeState()

        with(statusStore.currentState()) {
            assertTrue(serverRunning)
            assertEquals(ServerPhase.RUNNING, serverPhase)
            assertFalse(accessibilityEnabled)
            assertFalse(runtimeReady)
        }
    }

    @Test
    fun recordRequestSummaryAndErrorUpdateState() {
        val warnings = mutableListOf<String>()
        val coordinator = newCoordinator(warningLogger = warnings::add)

        coordinator.recordRequestSummary("POST /rpc")
        coordinator.recordError("failed to start RPC server")

        assertEquals("POST /rpc", statusStore.currentState().lastRequestSummary)
        assertEquals("failed to start RPC server", statusStore.currentState().lastError)
        assertEquals(listOf("failed to start RPC server"), warnings)
    }

    @Test
    fun initializePublishesBlockedAuthStateWithoutCrashingWhenTokenLoadFails() {
        val context = mock(Context::class.java)
        val applicationContext = mock(Context::class.java)
        `when`(context.applicationContext).thenReturn(applicationContext)
        val coordinator =
            newCoordinator(
                deviceTokenStoreAccess =
                    object : DeviceTokenStoreAccess {
                        override fun initialize(context: Context) = Unit

                        override fun loadCurrentToken(): DeviceTokenLoadResult =
                            DeviceTokenLoadResult.Blocked(DEVICE_TOKEN_DECRYPT_FAILED_MESSAGE)

                        override fun regenerateToken(): String = "token-1"
                    },
                accessibilityProbe = { true },
                serverProbe = { true },
            )

        coordinator.initialize(context)

        with(statusStore.currentState()) {
            assertEquals("", deviceToken)
            assertEquals(DEVICE_TOKEN_DECRYPT_FAILED_MESSAGE, authBlockedMessage)
            assertEquals(DEVICE_TOKEN_DECRYPT_FAILED_MESSAGE, lastError)
            assertEquals(ServerPhase.RUNNING, serverPhase)
            assertTrue(serverRunning)
            assertTrue(accessibilityEnabled)
            assertFalse(runtimeReady)
        }
        assertEquals(2, publishedStates.size)
    }

    @Test
    fun blockedAuthDiagnosticStaysStickyAcrossRuntimeTransitionsUntilRegeneration() {
        val context = mock(Context::class.java)
        val applicationContext = mock(Context::class.java)
        val service = mock(AccessibilityService::class.java)
        `when`(context.applicationContext).thenReturn(applicationContext)
        val coordinator =
            newCoordinator(
                deviceTokenStoreAccess =
                    object : DeviceTokenStoreAccess {
                        override fun initialize(context: Context) = Unit

                        override fun loadCurrentToken(): DeviceTokenLoadResult =
                            DeviceTokenLoadResult.Blocked(DEVICE_TOKEN_DECRYPT_FAILED_MESSAGE)

                        override fun regenerateToken(): String = "token-2"
                    },
                accessibilityProbe = { true },
                serverProbe = { true },
            )
        val attachmentController = newAttachmentController(coordinator)

        coordinator.initialize(context)
        coordinator.markServerRunning()
        attachmentController.registerAccessibilityService(service)
        attachmentController.unregisterAccessibilityService()
        coordinator.reconcileRuntimeState()

        with(statusStore.currentState()) {
            assertEquals(DEVICE_TOKEN_DECRYPT_FAILED_MESSAGE, authBlockedMessage)
            assertEquals(DEVICE_TOKEN_DECRYPT_FAILED_MESSAGE, lastError)
            assertEquals("", deviceToken)
            assertFalse(runtimeReady)
        }

        coordinator.regenerateDeviceToken()

        with(statusStore.currentState()) {
            assertEquals("token-2", deviceToken)
            assertEquals(null, authBlockedMessage)
            assertEquals(null, lastError)
        }
    }

    private fun newCoordinator(
        deviceTokenStoreAccess: DeviceTokenStoreAccess =
            object : DeviceTokenStoreAccess {
                override fun initialize(context: Context) = Unit

                override fun loadCurrentToken(): DeviceTokenLoadResult = DeviceTokenLoadResult.Available("token-1")

                override fun regenerateToken(): String = "token-2"
            },
        accessibilityProbe: (Context) -> Boolean = { false },
        warningLogger: (String) -> Unit = {},
        serverProbe: (Context) -> Boolean = { false },
    ): RuntimeCoordinator {
        val deviceTokenCoordinator = DeviceTokenCoordinator(deviceTokenStoreAccess)
        return RuntimeCoordinator(
            contextStore = contextStore,
            factsStore = factsStore,
            statusStore = statusStore,
            deviceTokenCoordinator = deviceTokenCoordinator,
            mutationLock = mutationLock,
            collaborators =
                RuntimeCoordinatorCollaborators(
                    authCoordinator =
                        RuntimeAuthCoordinator(
                            factsStore = factsStore,
                            statusStore = statusStore,
                            deviceTokenCoordinator = deviceTokenCoordinator,
                        ),
                    probeReconciler =
                        RuntimeProbeReconciler(
                            accessibilityServiceEnabledProbe = accessibilityProbe,
                            serverRunningProbe = serverProbe,
                            warningLogger = warningLogger,
                        ),
                    foregroundObservationManager =
                        ForegroundObservationManager(
                            factsStore = factsStore,
                            foregroundObservationStore = ForegroundObservationStore(),
                        ),
                ),
        )
    }

    private fun newAttachmentController(
        runtimeCoordinator: RuntimeCoordinator,
        scheduler: TestCooldownScheduler? = null,
    ): RuntimeAttachmentController =
        RuntimeAttachmentController(
            contextStore = contextStore,
            statusStore = statusStore,
            mutationLock = mutationLock,
            refreshRuntimeInputs = { accessibilityConnected, baseState ->
                runtimeCoordinator.refreshRuntimeInputs(
                    accessibilityConnected = accessibilityConnected,
                    baseState = baseState,
                )
            },
            verificationScheduler = scheduler ?: TestCooldownScheduler(TestClock()),
        )
}
