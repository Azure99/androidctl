package com.rainng.androidctl.agent.runtime

import android.accessibilityservice.AccessibilityService
import android.content.Context
import android.view.accessibility.AccessibilityEvent
import com.rainng.androidctl.agent.auth.DeviceTokenLoadResult
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.mockito.Mockito.mock
import org.mockito.Mockito.`when`
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class AgentRuntimeBridgeTest {
    @Before
    fun setUp() {
        AgentRuntimeBridge.resetForTest()
    }

    @After
    fun tearDown() {
        AgentRuntimeBridge.resetForTest()
    }

    @Test
    fun compositionRootExposesRuntimeContextAndState() {
        val context = mock(Context::class.java)
        val applicationContext = mock(Context::class.java)
        val service = mock(AccessibilityService::class.java)
        `when`(context.applicationContext).thenReturn(applicationContext)
        AgentRuntimeBridge.deviceTokenStoreAccess =
            object : DeviceTokenStoreAccess {
                override fun initialize(context: Context) = Unit

                override fun loadCurrentToken(): DeviceTokenLoadResult = DeviceTokenLoadResult.Available("token-1")

                override fun regenerateToken(): String = "token-2"
            }
        AgentRuntimeBridge.accessibilityServiceEnabledProbe = { true }
        AgentRuntimeBridge.serverRunningProbe = { true }

        AgentRuntimeBridge.initialize(context)
        AgentRuntimeBridge.markServerRunning()
        AgentRuntimeBridge.registerAccessibilityService(service)

        assertSame(applicationContext, AgentRuntimeBridge.applicationContext())
        assertSame(service, AgentRuntimeBridge.currentAccessibilityService())
        assertEquals("token-1", AgentRuntimeBridge.state.value.deviceToken)
        assertEquals(ServerPhase.RUNNING, AgentRuntimeBridge.state.value.serverPhase)
    }

    @Test
    fun foregroundObservationRolesRemainAvailableThroughCompositionRoot() {
        val stateAccess = AgentRuntimeBridge.foregroundObservationStateAccessRole

        AgentRuntimeBridge.recordObservedWindowState(
            eventType = AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
            packageName = "com.android.settings",
            windowClassName = "com.android.settings.Settings\$WifiSettingsActivity",
        )

        assertEquals("com.android.settings", AgentRuntimeBridge.currentForegroundHintState.fallbackPackageName)
        assertEquals(1L, AgentRuntimeBridge.currentForegroundGeneration)
        assertEquals("com.android.settings", stateAccess.foregroundHintState().fallbackPackageName)
        assertEquals(1L, stateAccess.foregroundGeneration())
        with(AgentRuntimeBridge.currentRuntimeFacts().foreground) {
            assertEquals("com.android.settings", hintPackageName)
            assertEquals("com.android.settings.Settings\$WifiSettingsActivity", hintActivityName)
            assertEquals(1L, generation)
        }

        AgentRuntimeBridge.resetForegroundObservationState()

        assertNull(AgentRuntimeBridge.currentForegroundHintState.fallbackPackageName)
        assertEquals(0L, AgentRuntimeBridge.currentForegroundGeneration)
        assertNull(stateAccess.foregroundHintState().fallbackPackageName)
        assertEquals(0L, stateAccess.foregroundGeneration())
        with(AgentRuntimeBridge.currentRuntimeFacts().foreground) {
            assertNull(hintPackageName)
            assertNull(hintActivityName)
            assertEquals(0L, generation)
        }
    }

    @Test
    fun registerAccessibilityServiceClearsRevokedBeforeConnectedStatePublishes() {
        val context = mock(Context::class.java)
        val applicationContext = mock(Context::class.java)
        val firstService = mock(AccessibilityService::class.java)
        val secondService = mock(AccessibilityService::class.java)
        `when`(context.applicationContext).thenReturn(applicationContext)
        AgentRuntimeBridge.deviceTokenStoreAccess =
            object : DeviceTokenStoreAccess {
                override fun initialize(context: Context) = Unit

                override fun loadCurrentToken(): DeviceTokenLoadResult = DeviceTokenLoadResult.Available("token-1")

                override fun regenerateToken(): String = "token-2"
            }
        AgentRuntimeBridge.accessibilityServiceEnabledProbe = { true }
        AgentRuntimeBridge.serverRunningProbe = { true }
        var revokedWhenConnected: Boolean? = null
        AgentRuntimeBridge.setRuntimeStateRecorderForTest { state ->
            if (state.accessibilityConnected) {
                revokedWhenConnected = AgentRuntimeBridge.currentAccessibilityAttachmentHandle().revoked
            }
        }

        AgentRuntimeBridge.initialize(context)
        AgentRuntimeBridge.markServerRunning()
        AgentRuntimeBridge.registerAccessibilityService(firstService)
        AgentRuntimeBridge.unregisterAccessibilityService()
        revokedWhenConnected = null

        AgentRuntimeBridge.registerAccessibilityService(secondService)

        assertEquals(false, revokedWhenConnected)
        assertFalse(AgentRuntimeBridge.currentAccessibilityAttachmentHandle().revoked)
        assertSame(secondService, AgentRuntimeBridge.currentAccessibilityService())
    }

    @Test
    fun unregisterAccessibilityServiceMarksRevokedBeforeDisconnectedStatePublishes() {
        val context = mock(Context::class.java)
        val applicationContext = mock(Context::class.java)
        val service = mock(AccessibilityService::class.java)
        `when`(context.applicationContext).thenReturn(applicationContext)
        AgentRuntimeBridge.deviceTokenStoreAccess =
            object : DeviceTokenStoreAccess {
                override fun initialize(context: Context) = Unit

                override fun loadCurrentToken(): DeviceTokenLoadResult = DeviceTokenLoadResult.Available("token-1")

                override fun regenerateToken(): String = "token-2"
            }
        AgentRuntimeBridge.accessibilityServiceEnabledProbe = { true }
        AgentRuntimeBridge.serverRunningProbe = { true }
        var revokedWhenDisconnected: Boolean? = null
        AgentRuntimeBridge.setRuntimeStateRecorderForTest { state ->
            if (!state.accessibilityConnected) {
                revokedWhenDisconnected = AgentRuntimeBridge.currentAccessibilityAttachmentHandle().revoked
            }
        }

        AgentRuntimeBridge.initialize(context)
        AgentRuntimeBridge.markServerRunning()
        AgentRuntimeBridge.registerAccessibilityService(service)
        revokedWhenDisconnected = null

        AgentRuntimeBridge.unregisterAccessibilityService()

        assertEquals(true, revokedWhenDisconnected)
        assertTrue(AgentRuntimeBridge.currentAccessibilityAttachmentHandle().revoked)
        assertNull(AgentRuntimeBridge.currentAccessibilityService())
    }

    @Test
    fun resetForegroundObservationStateStaysCanonicalAgainstConcurrentObservation() {
        val updaterEntered = CountDownLatch(1)
        val allowUpdaterComplete = CountDownLatch(1)
        AgentRuntimeBridge.foregroundHintUpdater =
            { current, eventType, packageName, windowClassName, generation ->
                updaterEntered.countDown()
                assertTrue(allowUpdaterComplete.await(5, TimeUnit.SECONDS))
                ForegroundHintTracker.update(
                    current = current,
                    eventType = eventType,
                    packageName = packageName,
                    windowClassName = windowClassName,
                    generation = generation,
                )
            }

        val eventThread =
            Thread {
                AgentRuntimeBridge.recordObservedWindowState(
                    eventType = AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
                    packageName = "com.android.settings",
                    windowClassName = "com.android.settings.Settings\$WifiSettingsActivity",
                )
            }
        eventThread.start()
        assertTrue(updaterEntered.await(5, TimeUnit.SECONDS))

        val resetThread = Thread { AgentRuntimeBridge.resetForegroundObservationState() }
        resetThread.start()
        allowUpdaterComplete.countDown()

        eventThread.join(5000)
        resetThread.join(5000)

        assertFalse(eventThread.isAlive)
        assertFalse(resetThread.isAlive)
        assertEquals(0L, AgentRuntimeBridge.currentForegroundGeneration)
        assertNull(AgentRuntimeBridge.currentForegroundHintState.fallbackPackageName)
        assertEquals(0L, AgentRuntimeBridge.currentRuntimeFacts().foreground.generation)
        assertNull(AgentRuntimeBridge.currentRuntimeFacts().foreground.hintPackageName)
    }
}
