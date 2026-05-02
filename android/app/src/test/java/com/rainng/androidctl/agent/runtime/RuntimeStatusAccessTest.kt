package com.rainng.androidctl.agent.runtime

import android.content.Context
import com.rainng.androidctl.agent.auth.DeviceTokenLoadResult
import kotlinx.coroutines.flow.MutableStateFlow
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
import org.junit.Before
import org.junit.Test
import org.mockito.Mockito.mock
import org.mockito.Mockito.`when`

class RuntimeStatusAccessTest {
    @Before
    fun setUp() {
        AgentRuntimeBridge.resetForTest()
    }

    @After
    fun tearDown() {
        AgentRuntimeBridge.resetForTest()
    }

    @Test
    fun agentRuntimeBridgeRuntimeStatusRoleExposesStateAndUiActions() {
        val context = mock(Context::class.java)
        val applicationContext = mock(Context::class.java)
        `when`(context.applicationContext).thenReturn(applicationContext)
        AgentRuntimeBridge.deviceTokenStoreAccess =
            object : DeviceTokenStoreAccess {
                override fun initialize(context: Context) = Unit

                override fun loadCurrentToken(): DeviceTokenLoadResult = DeviceTokenLoadResult.Available("token-1")

                override fun regenerateToken(): String = "token-2"
            }
        AgentRuntimeBridge.accessibilityServiceEnabledProbe = { true }
        AgentRuntimeBridge.serverRunningProbe = { true }
        val access = AgentRuntimeBridge.runtimeStatusAccessRole

        access.initialize(context)
        access.regenerateDeviceToken()
        access.refreshStatus()

        assertSame(AgentRuntimeBridge.state, access.state)
        assertSame(applicationContext, AgentRuntimeBridge.applicationContext())
        assertEquals("token-2", access.state.value.deviceToken)
        assertEquals(true, access.state.value.accessibilityEnabled)
    }

    @Test
    fun graphRuntimeStatusAccessDelegatesLifecycleOperations() {
        val state = MutableStateFlow(AgentRuntimeState())
        val context = mock(Context::class.java)
        var initializedContext: Context? = null
        var initializedTokenContext: Context? = null
        var refreshCount = 0
        var regenerateCount = 0
        var replacedToken: String? = null
        val access =
            GraphRuntimeStatusAccess(
                state = state,
                runtimeLifecycle =
                    object : RuntimeLifecycle {
                        override fun initialize(context: Context) {
                            initializedContext = context
                        }

                        override fun initializeWithDeviceToken(
                            context: Context,
                            token: String,
                        ) {
                            initializedTokenContext = context
                            replacedToken = token
                        }

                        override fun markServerRunning(
                            host: String,
                            port: Int,
                        ) = Unit

                        override fun markServerStopping() = Unit

                        override fun markServerStopped() = Unit

                        override fun reconcileRuntimeState() {
                            refreshCount += 1
                        }

                        override fun recordRequestSummary(summary: String) = Unit

                        override fun recordError(message: String) = Unit

                        override fun regenerateDeviceToken() {
                            regenerateCount += 1
                        }

                        override fun replaceDeviceToken(token: String) {
                            replacedToken = token
                        }
                    },
            )

        access.initialize(context)
        access.initializeWithDeviceToken(context = context, token = "host-token")
        access.refreshStatus()
        access.regenerateDeviceToken()
        access.replaceDeviceToken("manual-token")

        assertSame(state, access.state)
        assertSame(context, initializedContext)
        assertSame(context, initializedTokenContext)
        assertEquals(1, refreshCount)
        assertEquals(1, regenerateCount)
        assertEquals("manual-token", replacedToken)
    }
}
