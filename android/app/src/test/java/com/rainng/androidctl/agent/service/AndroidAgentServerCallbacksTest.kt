package com.rainng.androidctl.agent.service

import android.content.Context
import com.rainng.androidctl.agent.auth.DeviceTokenLoadResult
import com.rainng.androidctl.agent.runtime.AgentRuntimeBridge
import com.rainng.androidctl.agent.runtime.DeviceTokenStoreAccess
import com.rainng.androidctl.agent.runtime.ServerPhase
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
import org.junit.Before
import org.junit.Test
import org.mockito.Mockito.mock
import org.mockito.Mockito.`when`

class AndroidAgentServerCallbacksTest {
    @Before
    fun setUp() {
        AgentRuntimeBridge.resetForTest()
    }

    @After
    fun tearDown() {
        AgentRuntimeBridge.resetForTest()
    }

    @Test
    fun defaultCallbacksFollowLatestBridgeLifecycleAfterReset() {
        val context = mock(Context::class.java)
        val applicationContext = mock(Context::class.java)
        `when`(context.applicationContext).thenReturn(applicationContext)
        val callbacks = AndroidRuntimeCallbacks(context)

        AgentRuntimeBridge.resetForTest()
        AgentRuntimeBridge.deviceTokenStoreAccess =
            object : DeviceTokenStoreAccess {
                override fun initialize(context: Context) = Unit

                override fun loadCurrentToken(): DeviceTokenLoadResult = DeviceTokenLoadResult.Available("token-1")

                override fun regenerateToken(): String = "token-2"
            }
        AgentRuntimeBridge.accessibilityServiceEnabledProbe = { false }
        AgentRuntimeBridge.serverRunningProbe = { false }
        AgentRuntimeBridge.warningLogger = {}

        callbacks.initialize()
        callbacks.markServerRunning()
        callbacks.recordRequestSummary("latest-summary")
        callbacks.recordError("latest-error")
        callbacks.markServerStopping()
        callbacks.markServerStopped()

        assertSame(applicationContext, AgentRuntimeBridge.applicationContext())
        assertEquals(ServerPhase.STOPPED, AgentRuntimeBridge.state.value.serverPhase)
        assertEquals("latest-summary", AgentRuntimeBridge.state.value.lastRequestSummary)
        assertEquals("latest-error", AgentRuntimeBridge.state.value.lastError)
    }
}
