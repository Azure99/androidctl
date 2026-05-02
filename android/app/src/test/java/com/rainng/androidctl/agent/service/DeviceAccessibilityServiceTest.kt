package com.rainng.androidctl.agent.service

import android.accessibilityservice.AccessibilityService
import android.content.Context
import android.view.accessibility.AccessibilityEvent
import com.rainng.androidctl.agent.events.AccessibilityEventIngress
import com.rainng.androidctl.agent.events.DeviceEventHub
import com.rainng.androidctl.agent.events.EventPollRequest
import com.rainng.androidctl.agent.events.RuntimeStatusPayload
import com.rainng.androidctl.agent.events.TestClock
import com.rainng.androidctl.agent.events.TestCooldownScheduler
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
import org.junit.Before
import org.junit.Test
import org.mockito.Mockito.mock
import java.util.ArrayDeque

class DeviceAccessibilityServiceTest {
    @Before
    fun setUp() {
        DeviceEventHub.resetForTest()
    }

    @After
    fun tearDown() {
        DeviceEventHub.resetForTest()
    }

    @Test
    fun onServiceConnectedInitializesRuntimeBeforeAttachmentAndLifecycleActivation() {
        val deviceService = DeviceAccessibilityService()
        val calls = mutableListOf<String>()
        val context = mock(Context::class.java)
        val attachmentCoordinator =
            object : AccessibilityAttachmentOwner {
                override fun attach(service: AccessibilityService) {
                    assertSame(deviceService, service)
                    calls += "attach"
                }

                override fun detach() {
                    calls += "detach"
                }
            }
        deviceService.runtimeInitializer = { observedContext ->
            assertSame(context, observedContext)
            calls += "initialize"
        }
        deviceService.attachmentCoordinator = attachmentCoordinator
        deviceService.lifecycleGuard =
            object : DeviceAccessibilityLifecycleGuard {
                override fun markRuntimeActive() {
                    calls += "mark"
                }

                override fun teardownRuntimeAttachment(attachmentOwner: AccessibilityAttachmentOwner) {
                    assertSame(attachmentCoordinator, attachmentOwner)
                    calls += "teardown"
                    attachmentOwner.detach()
                }
            }
        deviceService.frameworkHooks = testFrameworkHooks(context)

        DeviceAccessibilityServiceTestHarness.onServiceConnected(deviceService)

        assertEquals(listOf("initialize", "attach", "mark"), calls)
    }

    @Test
    fun onUnbindDetachesRuntimeAttachmentAfterServiceConnection() {
        val service = DeviceAccessibilityService()
        val calls = mutableListOf<String>()
        val context = mock(Context::class.java)
        service.frameworkHooks = testFrameworkHooks(context)
        service.runtimeInitializer = { observedContext ->
            assertSame(context, observedContext)
        }
        service.attachmentCoordinator =
            object : AccessibilityAttachmentOwner {
                override fun attach(service: AccessibilityService) = Unit

                override fun detach() {
                    calls += "detach"
                }
            }
        service.lifecycleGuard = RuntimeAttachmentLifecycleGuard()

        DeviceAccessibilityServiceTestHarness.onServiceConnected(service)
        DeviceAccessibilityServiceTestHarness.onUnbind(service, null)

        assertEquals(listOf("detach"), calls)
    }

    @Test
    fun onDestroyDetachesRuntimeAttachmentAfterServiceConnection() {
        val service = DeviceAccessibilityService()
        val calls = mutableListOf<String>()
        val context = mock(Context::class.java)
        service.frameworkHooks = testFrameworkHooks(context)
        service.runtimeInitializer = { observedContext ->
            assertSame(context, observedContext)
        }
        service.attachmentCoordinator =
            object : AccessibilityAttachmentOwner {
                override fun attach(service: AccessibilityService) = Unit

                override fun detach() {
                    calls += "detach"
                }
            }
        service.lifecycleGuard = RuntimeAttachmentLifecycleGuard()

        DeviceAccessibilityServiceTestHarness.onServiceConnected(service)
        DeviceAccessibilityServiceTestHarness.onDestroy(service)

        assertEquals(listOf("detach"), calls)
    }

    @Test
    fun onDestroyShutsDownEventHubIdempotentlyAndAllowsLazyRestartAfterReconnect() {
        val clock = TestClock()
        val firstScheduler = TestCooldownScheduler(clock)
        val secondScheduler = TestCooldownScheduler(clock)
        val schedulers = ArrayDeque(listOf(firstScheduler, secondScheduler))
        DeviceEventHub.configureForTest(cooldownSchedulerFactory = { schedulers.removeFirst() })
        DeviceEventHub.recordRuntimeStatus(runtimeStatusPayload(runtimeReady = true))

        val service = DeviceAccessibilityService()
        val context = mock(Context::class.java)
        val calls = mutableListOf<String>()
        service.frameworkHooks = testFrameworkHooks(context)
        service.runtimeInitializer = { observedContext ->
            assertSame(context, observedContext)
            calls += "initialize"
        }
        service.attachmentCoordinator =
            object : AccessibilityAttachmentOwner {
                override fun attach(service: AccessibilityService) {
                    calls += "attach"
                }

                override fun detach() {
                    calls += "detach"
                }
            }
        service.lifecycleGuard = RuntimeAttachmentLifecycleGuard()

        DeviceAccessibilityServiceTestHarness.onServiceConnected(service)
        DeviceAccessibilityServiceTestHarness.onDestroy(service)
        DeviceAccessibilityServiceTestHarness.onDestroy(service)

        assertEquals(listOf("initialize", "attach", "detach"), calls)
        assertEquals(1, firstScheduler.shutdownCount)

        DeviceAccessibilityServiceTestHarness.onServiceConnected(service)
        DeviceEventHub.recordRuntimeStatus(runtimeStatusPayload(runtimeReady = false))

        val events =
            DeviceEventHub
                .poll(EventPollRequest(afterSeq = 0L, waitMs = 0L, limit = 20))
                .events

        assertEquals(0, secondScheduler.shutdownCount)
        assertEquals(runtimeStatusPayload(runtimeReady = false), events.single().data)
    }

    @Test
    fun onAccessibilityEventDelegatesThroughIngressBoundary() {
        val service = DeviceAccessibilityService()
        val calls = mutableListOf<String>()
        val event = mock(AccessibilityEvent::class.java)
        service.eventIngress =
            object : AccessibilityEventIngress {
                override fun record(
                    service: AccessibilityService,
                    event: AccessibilityEvent?,
                ) {
                    calls += "ingress"
                }
            }

        service.onAccessibilityEvent(event)

        assertEquals(listOf("ingress"), calls)
    }

    private fun runtimeStatusPayload(runtimeReady: Boolean): RuntimeStatusPayload =
        RuntimeStatusPayload(
            serverRunning = runtimeReady,
            accessibilityEnabled = runtimeReady,
            accessibilityConnected = runtimeReady,
            runtimeReady = runtimeReady,
        )

    private fun testFrameworkHooks(context: Context): DeviceAccessibilityService.FrameworkHooks =
        object : DeviceAccessibilityService.FrameworkHooks {
            override fun runtimeContext(service: DeviceAccessibilityService): Context = context

            override fun info(message: String) = Unit

            override fun warning(message: String) = Unit

            override fun onServiceConnectedSuper(service: DeviceAccessibilityService) = Unit

            override fun onUnbindSuper(
                service: DeviceAccessibilityService,
                intent: android.content.Intent?,
            ): Boolean = false

            override fun onDestroySuper(service: DeviceAccessibilityService) = Unit
        }
}
