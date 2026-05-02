package com.rainng.androidctl.agent.bootstrap

import android.accessibilityservice.AccessibilityService
import android.content.Context
import android.content.res.Resources
import android.util.DisplayMetrics
import com.rainng.androidctl.BuildConfig
import com.rainng.androidctl.agent.auth.DeviceTokenLoadResult
import com.rainng.androidctl.agent.runtime.AgentRuntimeBridge
import com.rainng.androidctl.agent.runtime.DeviceTokenStoreAccess
import com.rainng.androidctl.agent.screenshot.ScreenshotTaskRunner
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import org.mockito.Mockito.mock
import org.mockito.Mockito.`when`
import java.util.concurrent.AbstractExecutorService
import java.util.concurrent.TimeUnit

class AndroidDeviceRpcFactoryTest {
    @Before
    fun setUp() {
        AgentRuntimeBridge.resetForTest()
    }

    @After
    fun tearDown() {
        AgentRuntimeBridge.resetForTest()
    }

    @Test
    fun createRequestHandlerDefaultEnvironmentFollowsLatestBridgeGraphAfterReset() {
        val firstContext = mock(Context::class.java)
        val firstApplicationContext = mock(Context::class.java)
        `when`(firstContext.applicationContext).thenReturn(firstApplicationContext)
        configureBridge(
            context = firstContext,
            token = "token-1",
            accessibilityEnabled = false,
            accessibilityService = null,
        )
        val methodExecutor = DirectExecutorService()
        val handler = AndroidDeviceRpcFactory.createRequestHandler(methodExecutor = methodExecutor)

        AgentRuntimeBridge.resetForTest()

        val secondContext = mock(Context::class.java)
        val secondApplicationContext = mock(Context::class.java)
        val secondService = mock(AccessibilityService::class.java)
        val resources = mock(Resources::class.java)
        val displayMetrics =
            DisplayMetrics().apply {
                widthPixels = 1080
                heightPixels = 2400
                densityDpi = 420
            }
        `when`(secondContext.applicationContext).thenReturn(secondApplicationContext)
        `when`(secondService.resources).thenReturn(resources)
        `when`(resources.displayMetrics).thenReturn(displayMetrics)
        configureBridge(
            context = secondContext,
            token = "token-2",
            accessibilityEnabled = true,
            accessibilityService = secondService,
        )

        val payload =
            JSONObject(
                handler.handle(
                    headers = mapOf("authorization" to "Bearer token-2"),
                    rawBody =
                        """
                        {
                          "id":"req-snapshot",
                          "method":"snapshot.get",
                          "params":{"includeInvisible":true,"includeSystemWindows":true}
                        }
                        """.trimIndent(),
                ),
            )

        assertEquals(false, payload.getBoolean("ok"))
        assertEquals("NO_ACTIVE_WINDOW", payload.getJSONObject("error").getString("code"))
    }

    @Test
    fun createRequestHandlerDefaultEnvironmentReturnsBuildConfigVersionFromMetaGet() {
        val context = mock(Context::class.java)
        val applicationContext = mock(Context::class.java)
        `when`(context.applicationContext).thenReturn(applicationContext)
        configureBridge(
            context = context,
            token = "token-1",
            accessibilityEnabled = false,
            accessibilityService = null,
        )
        val screenshotTaskRunner =
            ScreenshotTaskRunner(
                executor = DirectExecutorService(),
                timeoutMs = 1000L,
            )
        val handler =
            AndroidDeviceRpcFactory.createRequestHandler(
                methodExecutor = DirectExecutorService(),
                screenshotTaskRunner = screenshotTaskRunner,
            )

        try {
            val payload =
                JSONObject(
                    handler.handle(
                        headers = mapOf("authorization" to "Bearer token-1"),
                        rawBody =
                            """
                            {
                              "id":"req-meta",
                              "method":"meta.get",
                              "params":{}
                            }
                            """.trimIndent(),
                    ),
                )

            assertEquals(true, payload.getBoolean("ok"))
            assertEquals(BuildConfig.VERSION_NAME, payload.getJSONObject("result").getString("version"))
        } finally {
            handler.shutdown(force = true)
        }
    }

    @Test
    fun requestHandlerOwnsScreenshotRunnerAndRestartUsesFreshRunner() {
        val firstScreenshotExecutor = RecordingShutdownExecutorService(awaitResult = false)
        val secondScreenshotExecutor = RecordingShutdownExecutorService(awaitResult = true)
        val firstHandler =
            AndroidDeviceRpcFactory.createRequestHandler(
                methodExecutor = DirectExecutorService(),
                screenshotTaskRunner =
                    ScreenshotTaskRunner(
                        executor = firstScreenshotExecutor,
                        timeoutMs = 1000L,
                    ),
            )
        val secondHandler =
            AndroidDeviceRpcFactory.createRequestHandler(
                methodExecutor = DirectExecutorService(),
                screenshotTaskRunner =
                    ScreenshotTaskRunner(
                        executor = secondScreenshotExecutor,
                        timeoutMs = 1000L,
                    ),
            )

        firstHandler.shutdown(force = true)

        assertEquals(1, firstScreenshotExecutor.shutdownCalls)
        assertEquals(1, firstScreenshotExecutor.shutdownNowCalls)
        assertEquals(0, secondScreenshotExecutor.shutdownCalls)
        assertEquals(0, secondScreenshotExecutor.shutdownNowCalls)

        secondHandler.shutdown(force = false)

        assertEquals(1, secondScreenshotExecutor.shutdownCalls)
        assertEquals(0, secondScreenshotExecutor.shutdownNowCalls)
    }

    private fun configureBridge(
        context: Context,
        token: String,
        accessibilityEnabled: Boolean,
        accessibilityService: AccessibilityService?,
    ) {
        AgentRuntimeBridge.deviceTokenStoreAccess =
            object : DeviceTokenStoreAccess {
                override fun initialize(context: Context) = Unit

                override fun loadCurrentToken(): DeviceTokenLoadResult = DeviceTokenLoadResult.Available(token)

                override fun regenerateToken(): String = token
            }
        AgentRuntimeBridge.accessibilityServiceEnabledProbe = { accessibilityEnabled }
        AgentRuntimeBridge.serverRunningProbe = { true }
        AgentRuntimeBridge.initialize(context)
        AgentRuntimeBridge.markServerRunning()
        accessibilityService?.let(AgentRuntimeBridge::registerAccessibilityService)
    }

    private class DirectExecutorService : AbstractExecutorService() {
        private var shutdown = false

        override fun shutdown() {
            shutdown = true
        }

        override fun shutdownNow(): MutableList<Runnable> {
            shutdown = true
            return mutableListOf()
        }

        override fun isShutdown(): Boolean = shutdown

        override fun isTerminated(): Boolean = shutdown

        override fun awaitTermination(
            timeout: Long,
            unit: TimeUnit,
        ): Boolean = shutdown

        override fun execute(command: Runnable) {
            command.run()
        }
    }

    private class RecordingShutdownExecutorService(
        private val awaitResult: Boolean,
    ) : AbstractExecutorService() {
        var shutdownCalls: Int = 0
        var shutdownNowCalls: Int = 0

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
        ): Boolean = awaitResult

        override fun execute(command: Runnable) {
            command.run()
        }
    }
}
