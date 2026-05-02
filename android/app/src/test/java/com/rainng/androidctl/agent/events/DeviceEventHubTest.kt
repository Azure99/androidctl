package com.rainng.androidctl.agent.events

import android.accessibilityservice.AccessibilityService
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import com.rainng.androidctl.agent.runtime.AgentRuntimeBridge
import com.rainng.androidctl.agent.runtime.ForegroundObservation
import com.rainng.androidctl.agent.runtime.ObservedWindowState
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.mockito.Mockito.mock
import org.mockito.Mockito.verify
import org.mockito.Mockito.`when`
import java.util.ArrayDeque
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class DeviceEventHubTest {
    @Before
    fun setUp() {
        AgentRuntimeBridge.resetForTest()
        DeviceEventHub.resetForTest()
    }

    @After
    fun tearDown() {
        DeviceEventHub.resetForTest()
        AgentRuntimeBridge.resetForTest()
    }

    @Test
    fun recordAccessibilityObservationUsesCapturedRuntimeState() {
        AgentRuntimeBridge.recordObservedWindowState(
            eventType = AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
            packageName = "com.runtime.package",
            windowClassName = "RuntimeActivity",
        )
        val service = mock(AccessibilityService::class.java)
        val window = mock(AccessibilityWindowInfo::class.java)
        val root = mock(AccessibilityNodeInfo::class.java)
        `when`(root.packageName).thenReturn("com.runtime.package")
        `when`(window.type).thenReturn(AccessibilityWindowInfo.TYPE_APPLICATION)
        `when`(window.layer).thenReturn(1)
        `when`(window.isActive).thenReturn(true)
        `when`(window.isFocused).thenReturn(true)
        `when`(window.root).thenReturn(root)
        `when`(service.windows).thenReturn(listOf(window))

        DeviceEventHub.recordAccessibilityObservation(
            event =
                ObservedAccessibilityEvent(
                    eventType = AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
                ),
            environment = AccessibilityServiceDeviceEventEnvironment.capture(service),
        )

        val eventsByType = eventsByType(poll())
        assertEquals(
            PackageChangedPayload(
                packageName = "com.runtime.package",
                activityName = "RuntimeActivity",
            ),
            eventsByType.getValue("package.changed").data,
        )
        assertEquals(
            WindowChangedPayload(
                packageName = "com.runtime.package",
                activityName = "RuntimeActivity",
                reason = "windowStateChanged",
            ),
            eventsByType.getValue("window.changed").data,
        )
    }

    @Test
    fun observedEventsResolveForegroundStateFromAccessibilityWindows() {
        val service = mock(AccessibilityService::class.java)
        `when`(service.windows).thenReturn(null)

        DeviceEventHub.recordAccessibilityObservation(
            event =
                ObservedAccessibilityEvent(
                    eventType = AccessibilityEvent.TYPE_VIEW_CLICKED,
                ),
            environment = AccessibilityServiceDeviceEventEnvironment.capture(service),
        )

        assertTrue(poll().events.isNotEmpty())
        verify(service).windows
    }

    @Test
    fun recordRuntimeStatusPublishesRuntimeStatusSnapshot() {
        DeviceEventHub.recordRuntimeStatus(
            RuntimeStatusPayload(
                serverRunning = true,
                accessibilityEnabled = true,
                accessibilityConnected = true,
                runtimeReady = true,
            ),
        )

        val runtimeStatus = eventsByType(poll()).getValue("runtime.status").data

        assertEquals(
            RuntimeStatusPayload(
                serverRunning = true,
                accessibilityEnabled = true,
                accessibilityConnected = true,
                runtimeReady = true,
            ),
            runtimeStatus,
        )
    }

    @Test
    fun resetForAttachmentChangeClearsOldEventsAndForcesResyncForExistingCursor() {
        DeviceEventHub.recordRuntimeStatus(
            RuntimeStatusPayload(
                serverRunning = true,
                accessibilityEnabled = true,
                accessibilityConnected = true,
                runtimeReady = true,
            ),
        )
        val firstPoll = poll()

        DeviceEventHub.resetForAttachmentChange()

        val payload = poll(afterSeq = firstPoll.latestSeq)

        assertTrue(payload.events.isEmpty())
        assertTrue(payload.needResync)
        assertTrue(payload.latestSeq > firstPoll.latestSeq)
    }

    @Test
    fun resetForAttachmentChangeDoesNotLeakForegroundHintsIntoNextSession() {
        AgentRuntimeBridge.recordObservedWindowState(
            eventType = AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
            packageName = "com.android.settings",
            windowClassName = "com.android.settings.SettingsActivity",
        )
        DeviceEventHub.resetForAttachmentChange()
        AgentRuntimeBridge.resetForegroundObservationState()

        val service = mock(AccessibilityService::class.java)
        val window = mock(AccessibilityWindowInfo::class.java)
        val root = mock(AccessibilityNodeInfo::class.java)
        `when`(root.packageName).thenReturn(null)
        `when`(window.type).thenReturn(AccessibilityWindowInfo.TYPE_APPLICATION)
        `when`(window.layer).thenReturn(1)
        `when`(window.isActive).thenReturn(true)
        `when`(window.isFocused).thenReturn(true)
        `when`(window.root).thenReturn(root)
        `when`(service.windows).thenReturn(listOf(window))

        DeviceEventHub.recordAccessibilityObservation(
            event =
                ObservedAccessibilityEvent(
                    eventType = AccessibilityEvent.TYPE_VIEW_CLICKED,
                ),
            environment = AccessibilityServiceDeviceEventEnvironment.capture(service),
        )

        val eventsByType = eventsByType(poll())

        assertFalse(eventsByType.containsKey("package.changed"))
        assertEquals(
            SnapshotInvalidatedPayload(
                packageName = null,
                reason = "viewClicked",
            ),
            eventsByType.getValue("snapshot.invalidated").data,
        )
    }

    @Test
    fun shutdownClosesSchedulerOnceAndCancelsPendingCooldownWork() {
        val clock = TestClock()
        val scheduler = TestCooldownScheduler(clock)
        configureCooldownHub(clock) { scheduler }

        schedulePendingInvalidation(clock, generation = 1L)

        assertEquals(1, scheduler.pendingTaskCount)

        DeviceEventHub.shutdown()
        DeviceEventHub.shutdown()

        assertEquals(1, scheduler.shutdownCount)
        assertEquals(1, scheduler.cancelledTaskCount)
        assertEquals(0, scheduler.pendingTaskCount)
    }

    @Test
    fun resetForAttachmentChangeCancelsCooldownWithoutShuttingDownScheduler() {
        val clock = TestClock()
        val scheduler = TestCooldownScheduler(clock)
        configureCooldownHub(clock) { scheduler }

        schedulePendingInvalidation(clock, generation = 1L)
        DeviceEventHub.resetForAttachmentChange()

        assertEquals(0, scheduler.shutdownCount)
        assertEquals(1, scheduler.cancelledTaskCount)
        assertEquals(0, scheduler.pendingTaskCount)

        clock.nowMs = 75L
        recordInvalidation(AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED, generation = 2L)
        clock.nowMs = 100L
        recordInvalidation(AccessibilityEvent.TYPE_VIEW_SCROLLED, generation = 2L)

        assertEquals(2, scheduler.scheduleCount)
        assertEquals(1, scheduler.pendingTaskCount)
    }

    @Test
    fun recordAfterShutdownUsesFreshProcessorAndScheduler() {
        val clock = TestClock()
        val firstScheduler = TestCooldownScheduler(clock)
        val secondScheduler = TestCooldownScheduler(clock)
        val schedulers = ArrayDeque(listOf(firstScheduler, secondScheduler))
        configureCooldownHub(clock) { schedulers.removeFirst() }

        schedulePendingInvalidation(clock, generation = 1L)
        DeviceEventHub.shutdown()

        assertEquals(1, firstScheduler.shutdownCount)

        DeviceEventHub.recordRuntimeStatus(
            RuntimeStatusPayload(
                serverRunning = true,
                accessibilityEnabled = true,
                accessibilityConnected = true,
                runtimeReady = true,
            ),
        )

        val runtimePayload = eventsByType(poll()).getValue("runtime.status").data

        assertEquals(
            RuntimeStatusPayload(
                serverRunning = true,
                accessibilityEnabled = true,
                accessibilityConnected = true,
                runtimeReady = true,
            ),
            runtimePayload,
        )

        clock.nowMs = 75L
        recordInvalidation(AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED, generation = 2L)
        clock.nowMs = 100L
        recordInvalidation(AccessibilityEvent.TYPE_VIEW_SCROLLED, generation = 2L)

        assertEquals(1, firstScheduler.scheduleCount)
        assertEquals(0, secondScheduler.shutdownCount)
        assertEquals(1, secondScheduler.pendingTaskCount)
    }

    @Test
    fun shutdownCanCloseCurrentProcessorWhileAccessibilityEnvironmentIsResolving() {
        val clock = TestClock()
        val firstScheduler = TestCooldownScheduler(clock)
        val secondScheduler = TestCooldownScheduler(clock)
        val schedulers = ArrayDeque(listOf(firstScheduler, secondScheduler))
        configureCooldownHub(clock) { schedulers.removeFirst() }
        val environment =
            BlockingDeviceEventEnvironment(
                resolvedForegroundState =
                    ObservedWindowState(
                        packageName = "com.android.settings",
                        activityName = "SettingsActivity",
                    ),
                generation = 1L,
            )
        val recordThread =
            Thread {
                DeviceEventHub.recordAccessibilityObservation(
                    event =
                        ObservedAccessibilityEvent(
                            eventType = AccessibilityEvent.TYPE_VIEW_CLICKED,
                        ),
                    environment = environment,
                )
            }

        recordThread.start()
        assertTrue(environment.awaitForegroundObservation())

        DeviceEventHub.shutdown()

        assertEquals(1, firstScheduler.shutdownCount)

        environment.releaseForegroundObservation()
        recordThread.join(1_000L)

        assertFalse(recordThread.isAlive)
        assertTrue(poll().events.isEmpty())
        assertEquals(0, secondScheduler.shutdownCount)
    }

    private fun poll(
        afterSeq: Long = 0L,
        waitMs: Long = 0L,
        limit: Int = 50,
    ): EventPollResult =
        DeviceEventHub.poll(
            EventPollRequest(
                afterSeq = afterSeq,
                waitMs = waitMs,
                limit = limit,
            ),
        )

    private fun eventsByType(payload: EventPollResult): Map<String, DeviceEvent> = payload.events.associateBy(DeviceEvent::type)

    private fun configureCooldownHub(
        clock: TestClock,
        schedulerFactory: () -> CooldownScheduler,
    ) {
        DeviceEventHub.configureForTest(
            cooldownSchedulerFactory = schedulerFactory,
            processorFactory = { scheduler ->
                val buffer = DeviceEventBuffer(timestampProvider = { "2026-03-15T00:00:00Z" })
                DeviceEventProcessor(
                    buffer = buffer,
                    aggregator =
                        DeviceEventAggregator(
                            buffer = buffer,
                            cooldownClockMsProvider = { clock.nowMs },
                            cooldownScheduler = scheduler,
                        ),
                )
            },
        )
    }

    private fun schedulePendingInvalidation(
        clock: TestClock,
        generation: Long,
    ) {
        recordInvalidation(AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED, generation = generation)
        clock.nowMs = 50L
        recordInvalidation(AccessibilityEvent.TYPE_VIEW_SCROLLED, generation = generation)
    }

    private fun recordInvalidation(
        eventType: Int,
        generation: Long,
    ) {
        DeviceEventHub.recordAccessibilityObservation(
            event =
                ObservedAccessibilityEvent(
                    eventType = eventType,
                ),
            environment =
                TestDeviceEventEnvironment(
                    resolvedForegroundState =
                        ObservedWindowState(
                            packageName = "com.android.settings",
                            activityName = "SettingsActivity",
                        ),
                    generation = generation,
                ),
        )
    }

    private class TestDeviceEventEnvironment(
        private val resolvedForegroundState: ObservedWindowState,
        private val generation: Long,
    ) : DeviceEventEnvironment {
        override fun foregroundObservation(): ForegroundObservation =
            ForegroundObservation(
                state = resolvedForegroundState,
                generation = generation,
            )

        override fun currentImeState(): ImeState = ImeState(visible = false, windowId = null)
    }

    private class BlockingDeviceEventEnvironment(
        private val resolvedForegroundState: ObservedWindowState,
        private val generation: Long,
    ) : DeviceEventEnvironment {
        private val foregroundObservationStarted = CountDownLatch(1)
        private val foregroundObservationReleased = CountDownLatch(1)

        fun awaitForegroundObservation(): Boolean = foregroundObservationStarted.await(1, TimeUnit.SECONDS)

        fun releaseForegroundObservation() {
            foregroundObservationReleased.countDown()
        }

        override fun foregroundObservation(): ForegroundObservation {
            foregroundObservationStarted.countDown()
            assertTrue(foregroundObservationReleased.await(1, TimeUnit.SECONDS))
            return ForegroundObservation(
                state = resolvedForegroundState,
                generation = generation,
            )
        }

        override fun currentImeState(): ImeState = ImeState(visible = false, windowId = null)
    }
}
