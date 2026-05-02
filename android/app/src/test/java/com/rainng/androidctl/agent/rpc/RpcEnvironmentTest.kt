package com.rainng.androidctl.agent.rpc

import android.accessibilityservice.AccessibilityService
import android.content.Context
import com.rainng.androidctl.BuildConfig
import com.rainng.androidctl.agent.auth.DeviceTokenLoadResult
import com.rainng.androidctl.agent.runtime.AgentRuntimeBridge
import com.rainng.androidctl.agent.runtime.DeviceTokenStoreAccess
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
import org.junit.Before
import org.junit.Test
import org.mockito.Mockito.mock
import org.mockito.Mockito.`when`

class RpcEnvironmentTest {
    @Before
    fun setUp() {
        AgentRuntimeBridge.resetForTest()
    }

    @After
    fun tearDown() {
        AgentRuntimeBridge.resetForTest()
    }

    @Test
    fun defaultRuntimeAccessFollowsLatestBridgeGraphAfterReset() {
        val firstContext = mock(Context::class.java)
        val firstApplicationContext = mock(Context::class.java)
        val firstService = mock(AccessibilityService::class.java)
        `when`(firstContext.applicationContext).thenReturn(firstApplicationContext)
        configureBridge(firstContext, "token-1")
        AgentRuntimeBridge.registerAccessibilityService(firstService)
        val environment = RpcEnvironment()

        assertEquals("token-1", environment.runtimeAccess.currentDeviceToken())
        assertSame(firstApplicationContext, environment.runtimeAccess.applicationContext())
        assertSame(firstService, environment.runtimeAccess.currentAccessibilityService())

        AgentRuntimeBridge.resetForTest()

        val secondContext = mock(Context::class.java)
        val secondApplicationContext = mock(Context::class.java)
        val secondService = mock(AccessibilityService::class.java)
        `when`(secondContext.applicationContext).thenReturn(secondApplicationContext)
        configureBridge(secondContext, "token-2")
        AgentRuntimeBridge.registerAccessibilityService(secondService)

        assertEquals("token-2", environment.runtimeAccess.currentDeviceToken())
        assertEquals("token-2", environment.expectedTokenProvider())
        assertSame(secondApplicationContext, environment.runtimeAccess.applicationContext())
        assertSame(secondService, environment.runtimeAccess.currentAccessibilityService())
    }

    @Test
    fun defaultVersionProviderUsesBuildConfigVersionName() {
        assertEquals(BuildConfig.VERSION_NAME, RpcEnvironment().versionProvider())
    }

    private fun configureBridge(
        context: Context,
        token: String,
    ) {
        AgentRuntimeBridge.deviceTokenStoreAccess =
            object : DeviceTokenStoreAccess {
                override fun initialize(context: Context) = Unit

                override fun loadCurrentToken(): DeviceTokenLoadResult = DeviceTokenLoadResult.Available(token)

                override fun regenerateToken(): String = token
            }
        AgentRuntimeBridge.accessibilityServiceEnabledProbe = { true }
        AgentRuntimeBridge.serverRunningProbe = { true }
        AgentRuntimeBridge.initialize(context)
    }
}
