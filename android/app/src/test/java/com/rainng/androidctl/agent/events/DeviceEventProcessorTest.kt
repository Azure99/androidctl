package com.rainng.androidctl.agent.events

import android.accessibilityservice.AccessibilityService
import android.view.accessibility.AccessibilityWindowInfo
import com.rainng.androidctl.agent.logging.RateLimitedDiagnosticReporter
import com.rainng.androidctl.agent.runtime.AccessibilityForegroundObservationProvider
import com.rainng.androidctl.agent.runtime.ForegroundObservation
import com.rainng.androidctl.agent.runtime.ObservedWindowState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import org.mockito.Mockito.doReturn
import org.mockito.Mockito.mock
import org.mockito.Mockito.`when`
import java.util.ArrayDeque
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference

class DeviceEventProcessorTest {
    @Test
    fun accessibilityServiceDeviceEventEnvironmentFormatsImeWindowIdForImeWindow() {
        val imeState =
            captureImeState(
                listOf(
                    AccessibilityWindowSnapshot(
                        id = 7,
                        type = AccessibilityWindowInfo.TYPE_INPUT_METHOD,
                    ),
                ),
            )

        assertTrue(imeState.visible)
        assertEquals("w7", imeState.windowId)
    }

    @Test
    fun accessibilityServiceDeviceEventEnvironmentReturnsNullWindowIdWhenImeWindowMissing() {
        val imeState =
            captureImeState(
                listOf(
                    AccessibilityWindowSnapshot(
                        id = 1,
                        type = AccessibilityWindowInfo.TYPE_APPLICATION,
                    ),
                ),
            )

        assertFalse(imeState.visible)
        assertEquals(null, imeState.windowId)
    }

    @Test
    fun accessibilityWindowSnapshotReaderWarnsWhenWindowsFailAndUsesEmptyList() {
        val warningMessages = mutableListOf<String>()
        val service =
            mock(AccessibilityService::class.java).also { service ->
                `when`(service.windows).thenThrow(IllegalStateException("windows unavailable"))
            }
        val reader =
            AccessibilityWindowSnapshotReader(
                diagnosticReporter = testReporter(warningMessages),
            )

        val snapshots = reader.read(service)

        assertEquals(emptyList<AccessibilityWindowSnapshot>(), snapshots)
        assertEquals(
            listOf("accessibility window snapshots unavailable; using empty list"),
            warningMessages,
        )
    }

    @Test
    fun unobservedAccessibilityEventsAreIgnored() {
        var imeLookupCount = 0
        val processor = DeviceEventProcessor()

        processor.recordAccessibilityEvent(
            event =
                ObservedAccessibilityEvent(
                    eventType = 999,
                ),
            environment =
                TestDeviceEventEnvironment(
                    imeStateProvider = {
                        imeLookupCount += 1
                        ImeState(visible = true, windowId = "w9")
                    },
                ),
        )

        val payload = processor.poll(request())

        assertTrue(payload.events.isEmpty())
        assertEquals(0L, payload.latestSeq)
        assertFalse(payload.needResync)
        assertFalse(payload.timedOut)
        assertEquals(0, imeLookupCount)
    }

    @Test
    fun observedEventsUseRuntimeWindowStateWhenAvailable() {
        val processor = DeviceEventProcessor()

        processor.recordAccessibilityEvent(
            event =
                ObservedAccessibilityEvent(
                    eventType = android.view.accessibility.AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
                ),
            environment =
                TestDeviceEventEnvironment(
                    resolvedForegroundState =
                        ObservedWindowState(
                            packageName = "com.runtime.package",
                            activityName = "RuntimeActivity",
                        ),
                ),
        )

        val eventsByType = eventsByType(processor.poll(request()))

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
    fun observedEventsFollowForegroundObservationWhenPackageIsAbsent() {
        val processor = DeviceEventProcessor()

        processor.recordAccessibilityEvent(
            event =
                ObservedAccessibilityEvent(
                    eventType = android.view.accessibility.AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
                ),
            environment = TestDeviceEventEnvironment(),
        )

        val eventsByType = eventsByType(processor.poll(request()))

        assertFalse(eventsByType.containsKey("package.changed"))
        assertEquals(
            WindowChangedPayload(
                packageName = null,
                activityName = null,
                reason = "windowStateChanged",
            ),
            eventsByType.getValue("window.changed").data,
        )
    }

    @Test
    fun imeVisibilityDoesNotOverrideResolvedForegroundPackage() {
        val processor = DeviceEventProcessor()

        processor.recordAccessibilityEvent(
            event =
                ObservedAccessibilityEvent(
                    eventType = android.view.accessibility.AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
                ),
            environment =
                TestDeviceEventEnvironment(
                    resolvedForegroundState =
                        ObservedWindowState(
                            packageName = "com.android.settings",
                            activityName = "SettingsActivity",
                        ),
                    imeStateProvider = { ImeState(visible = true, windowId = "w7") },
                ),
        )

        val eventsByType = eventsByType(processor.poll(request()))

        assertEquals(
            PackageChangedPayload(
                packageName = "com.android.settings",
                activityName = "SettingsActivity",
            ),
            eventsByType.getValue("package.changed").data,
        )
        assertEquals(
            ImeChangedPayload(
                visible = true,
                windowId = "w7",
            ),
            eventsByType.getValue("ime.changed").data,
        )
    }

    @Test
    fun imeVisibilityTracksLatestRefreshableObservation() {
        val imeStates =
            ArrayDeque(
                listOf(
                    ImeState(visible = true, windowId = "w7"),
                    ImeState(visible = false, windowId = null),
                ),
            )
        val processor = DeviceEventProcessor()
        val environment =
            TestDeviceEventEnvironment(
                imeStateProvider = { imeStates.removeFirst() },
            )

        processor.recordAccessibilityEvent(
            event =
                ObservedAccessibilityEvent(
                    eventType = android.view.accessibility.AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
                ),
            environment = environment,
        )
        val firstPoll = processor.poll(request())

        processor.recordAccessibilityEvent(
            event =
                ObservedAccessibilityEvent(
                    eventType = android.view.accessibility.AccessibilityEvent.TYPE_WINDOWS_CHANGED,
                ),
            environment = environment,
        )

        val secondPoll = processor.poll(request(afterSeq = firstPoll.latestSeq))

        assertEquals(
            ImeChangedPayload(
                visible = false,
                windowId = null,
            ),
            eventsByType(secondPoll).getValue("ime.changed").data,
        )
    }

    @Test
    fun invalidationPreservesNullPackageNameAsExplicitJsonNull() {
        val processor = DeviceEventProcessor()

        processor.recordAccessibilityEvent(
            event =
                ObservedAccessibilityEvent(
                    eventType = android.view.accessibility.AccessibilityEvent.TYPE_VIEW_CLICKED,
                ),
            environment = TestDeviceEventEnvironment(),
        )

        val invalidation =
            eventsByType(processor.poll(request()))
                .getValue("snapshot.invalidated")
                .data

        assertEquals(
            SnapshotInvalidatedPayload(
                packageName = null,
                reason = "viewClicked",
            ),
            invalidation,
        )
    }

    @Test
    fun recordRuntimeStatusForwardsProjectedRuntimeSnapshotsIntoBuffer() {
        val processor = DeviceEventProcessor()

        processor.recordRuntimeStatus(
            RuntimeStatusPayload(
                serverRunning = true,
                accessibilityEnabled = true,
                accessibilityConnected = true,
                runtimeReady = true,
            ),
        )

        val runtimeStatus = eventsByType(processor.poll(request())).getValue("runtime.status").data

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
    fun pollReturnsBufferOutputUnchangedForRequest() {
        val buffer = DeviceEventBuffer(timestampProvider = { "2026-03-15T00:00:00Z" })
        buffer.publish(
            PackageChangedPayload(
                packageName = "com.example",
                activityName = null,
            ),
        )
        val processor =
            DeviceEventProcessor(
                buffer = buffer,
                aggregator = DeviceEventAggregator(buffer),
            )

        val request = request(limit = 10)
        val expected = buffer.poll(request)
        val actual = processor.poll(request)

        assertEquals(expected, actual)
    }

    @Test
    fun pollDoesNotFlushPendingInvalidationBeforeCooldownExpires() {
        val clock = TestClock()
        val scheduler = TestCooldownScheduler(clock)
        val buffer = DeviceEventBuffer(timestampProvider = { "2026-03-15T00:00:00Z" })
        val aggregator =
            DeviceEventAggregator(
                buffer,
                cooldownClockMsProvider = { clock.nowMs },
                cooldownScheduler = scheduler,
            )
        val processor =
            DeviceEventProcessor(
                buffer = buffer,
                aggregator = aggregator,
            )
        val environment =
            TestDeviceEventEnvironment(
                resolvedForegroundState =
                    ObservedWindowState(
                        packageName = "com.android.settings",
                        activityName = "SettingsActivity",
                    ),
                generation = 3L,
            )

        processor.recordAccessibilityEvent(
            event =
                ObservedAccessibilityEvent(
                    eventType = android.view.accessibility.AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
                ),
            environment = environment,
        )

        val firstPoll = processor.poll(request())

        scheduler.advanceTo(50L)
        processor.recordAccessibilityEvent(
            event =
                ObservedAccessibilityEvent(
                    eventType = android.view.accessibility.AccessibilityEvent.TYPE_VIEW_SCROLLED,
                ),
            environment = environment,
        )

        val payload = processor.poll(request(afterSeq = firstPoll.latestSeq))

        assertTrue(payload.events.isEmpty())
    }

    @Test
    fun autoPublishedInvalidationWakesBlockingPoll() {
        val clock = TestClock()
        val scheduler = TestCooldownScheduler(clock)
        val buffer =
            DeviceEventBuffer(
                timestampProvider = { "2026-03-15T00:00:00Z" },
            )
        val aggregator =
            DeviceEventAggregator(
                buffer,
                cooldownClockMsProvider = { clock.nowMs },
                cooldownScheduler = scheduler,
            )
        val processor =
            DeviceEventProcessor(
                buffer = buffer,
                aggregator = aggregator,
            )
        val environment =
            TestDeviceEventEnvironment(
                resolvedForegroundState =
                    ObservedWindowState(
                        packageName = "com.android.settings",
                        activityName = "SettingsActivity",
                    ),
                generation = 7L,
            )

        processor.recordAccessibilityEvent(
            event =
                ObservedAccessibilityEvent(
                    eventType = android.view.accessibility.AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
                ),
            environment = environment,
        )
        val firstPoll = processor.poll(request())

        scheduler.advanceTo(50L)
        processor.recordAccessibilityEvent(
            event =
                ObservedAccessibilityEvent(
                    eventType = android.view.accessibility.AccessibilityEvent.TYPE_VIEW_SCROLLED,
                ),
            environment = environment,
        )

        val executor = Executors.newSingleThreadExecutor()
        val pollThread = AtomicReference<Thread?>()
        try {
            val future =
                executor.submit<EventPollResult> {
                    pollThread.set(Thread.currentThread())
                    processor.poll(
                        request(
                            afterSeq = firstPoll.latestSeq,
                            waitMs = 1000L,
                        ),
                    )
                }

            waitForPollThreadWaiting(pollThread)
            scheduler.advanceTo(150L)

            val payload = future.get(1L, TimeUnit.SECONDS)
            val invalidationReasons =
                payload.events
                    .filter { it.type == "snapshot.invalidated" }
                    .map { (it.data as SnapshotInvalidatedPayload).reason }

            assertEquals(listOf("viewScrolled"), invalidationReasons)
        } finally {
            executor.shutdownNow()
        }
    }

    @Test
    fun resetForAttachmentChangeClearsOldEventsAndMarksPreResetCursorForResync() {
        val processor = DeviceEventProcessor()

        processor.recordAccessibilityEvent(
            event =
                ObservedAccessibilityEvent(
                    eventType = android.view.accessibility.AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
                ),
            environment =
                TestDeviceEventEnvironment(
                    resolvedForegroundState =
                        ObservedWindowState(
                            packageName = "com.android.settings",
                            activityName = "SettingsActivity",
                        ),
                    generation = 2L,
                ),
        )
        val firstPoll = processor.poll(request())

        processor.resetForAttachmentChange()

        val payload = processor.poll(request(afterSeq = firstPoll.latestSeq))

        assertTrue(payload.events.isEmpty())
        assertTrue(payload.needResync)
        assertTrue(payload.latestSeq > firstPoll.latestSeq)
    }

    @Test
    fun resetForAttachmentChangeDoesNotInjectRuntimeStatusButLaterPublicationMayRepublish() {
        val processor = DeviceEventProcessor()

        processor.recordRuntimeStatus(
            RuntimeStatusPayload(
                serverRunning = true,
                accessibilityEnabled = true,
                accessibilityConnected = true,
                runtimeReady = true,
            ),
        )
        val firstPoll = processor.poll(request())

        processor.resetForAttachmentChange()

        val resetPayload = processor.poll(request(afterSeq = firstPoll.latestSeq))

        assertTrue(resetPayload.events.isEmpty())
        assertTrue(resetPayload.needResync)

        processor.recordRuntimeStatus(
            RuntimeStatusPayload(
                serverRunning = true,
                accessibilityEnabled = true,
                accessibilityConnected = true,
                runtimeReady = true,
            ),
        )

        val republishedPayload = processor.poll(request(afterSeq = resetPayload.latestSeq))

        assertEquals(listOf("runtime.status"), republishedPayload.events.map(DeviceEvent::type))
    }

    @Test
    fun resetForAttachmentChangeAllowsSameForegroundAndImeSignalsToBePublishedAgain() {
        val processor = DeviceEventProcessor()
        val environment =
            TestDeviceEventEnvironment(
                resolvedForegroundState =
                    ObservedWindowState(
                        packageName = "com.android.settings",
                        activityName = "SettingsActivity",
                    ),
                generation = 4L,
                imeStateProvider = { ImeState(visible = true, windowId = "w7") },
            )

        processor.recordRuntimeStatus(
            RuntimeStatusPayload(
                serverRunning = true,
                accessibilityEnabled = true,
                accessibilityConnected = true,
                runtimeReady = true,
            ),
        )
        processor.recordAccessibilityEvent(
            event =
                ObservedAccessibilityEvent(
                    eventType = android.view.accessibility.AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
                ),
            environment = environment,
        )
        val firstPoll = processor.poll(request())

        processor.resetForAttachmentChange()
        processor.recordRuntimeStatus(
            RuntimeStatusPayload(
                serverRunning = true,
                accessibilityEnabled = true,
                accessibilityConnected = true,
                runtimeReady = true,
            ),
        )
        processor.recordAccessibilityEvent(
            event =
                ObservedAccessibilityEvent(
                    eventType = android.view.accessibility.AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
                ),
            environment = environment,
        )

        val payload = processor.poll(request(afterSeq = firstPoll.latestSeq))

        assertEquals(
            listOf(
                "runtime.status",
                "package.changed",
                "window.changed",
                "snapshot.invalidated",
                "ime.changed",
            ),
            payload.events.map(DeviceEvent::type),
        )
    }

    @Test
    fun resetForAttachmentChangeWakesBlockingPollWithNeedResync() {
        val buffer = DeviceEventBuffer()
        val processor = DeviceEventProcessor(buffer = buffer)

        processor.recordAccessibilityEvent(
            event =
                ObservedAccessibilityEvent(
                    eventType = android.view.accessibility.AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
                ),
            environment =
                TestDeviceEventEnvironment(
                    resolvedForegroundState =
                        ObservedWindowState(
                            packageName = "com.android.settings",
                            activityName = "SettingsActivity",
                        ),
                    generation = 5L,
                ),
        )
        val firstPoll = processor.poll(request())

        val executor = Executors.newSingleThreadExecutor()
        val pollThread = AtomicReference<Thread?>()
        try {
            val future =
                executor.submit<EventPollResult> {
                    pollThread.set(Thread.currentThread())
                    processor.poll(
                        request(
                            afterSeq = firstPoll.latestSeq,
                            waitMs = 1000L,
                        ),
                    )
                }

            waitForPollThreadWaiting(pollThread)
            processor.resetForAttachmentChange()

            val payload = future.get(1L, TimeUnit.SECONDS)

            assertTrue(payload.events.isEmpty())
            assertTrue(payload.needResync)
            assertTrue(payload.latestSeq > firstPoll.latestSeq)
        } finally {
            executor.shutdownNow()
        }
    }

    @Test
    fun resetForAttachmentChangeAllowsNextSessionToIgnoreStaleForegroundHints() {
        val processor = DeviceEventProcessor()

        processor.recordAccessibilityEvent(
            event =
                ObservedAccessibilityEvent(
                    eventType = android.view.accessibility.AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
                ),
            environment =
                TestDeviceEventEnvironment(
                    resolvedForegroundState =
                        ObservedWindowState(
                            packageName = "com.android.settings",
                            activityName = "SettingsActivity",
                        ),
                    generation = 1L,
                ),
        )
        processor.poll(request())

        processor.resetForAttachmentChange()
        processor.recordAccessibilityEvent(
            event =
                ObservedAccessibilityEvent(
                    eventType = android.view.accessibility.AccessibilityEvent.TYPE_VIEW_CLICKED,
                ),
            environment =
                TestDeviceEventEnvironment(
                    resolvedForegroundState = ObservedWindowState(),
                    generation = 0L,
                ),
        )

        val payload = processor.poll(request(afterSeq = 0L))
        val eventsByType = eventsByType(payload)

        assertFalse(eventsByType.containsKey("package.changed"))
        assertEquals(
            SnapshotInvalidatedPayload(
                packageName = null,
                reason = "viewClicked",
            ),
            eventsByType.getValue("snapshot.invalidated").data,
        )
    }

    private fun request(
        afterSeq: Long = 0L,
        waitMs: Long = 0L,
        limit: Int = 50,
    ): EventPollRequest =
        EventPollRequest(
            afterSeq = afterSeq,
            waitMs = waitMs,
            limit = limit,
        )

    private fun captureImeState(windowSnapshots: List<AccessibilityWindowSnapshot>): ImeState {
        val service = mock(AccessibilityService::class.java)
        val foregroundObservationProvider = mock(AccessibilityForegroundObservationProvider::class.java)
        val windowSnapshotReader = mock(AccessibilityWindowSnapshotReader::class.java)

        doReturn(ForegroundObservation()).`when`(foregroundObservationProvider).observe()
        doReturn(windowSnapshots).`when`(windowSnapshotReader).read(service)

        return AccessibilityServiceDeviceEventEnvironment
            .capture(
                service = service,
                foregroundObservationProvider = foregroundObservationProvider,
                windowSnapshotReader = windowSnapshotReader,
            ).currentImeState()
    }

    private fun eventsByType(payload: EventPollResult): Map<String, DeviceEvent> = payload.events.associateBy(DeviceEvent::type)

    private fun waitForPollThreadWaiting(threadRef: AtomicReference<Thread?>) {
        val deadlineNanos = System.nanoTime() + TimeUnit.SECONDS.toNanos(1L)
        var lastState: Thread.State? = null
        while (System.nanoTime() < deadlineNanos) {
            val thread = threadRef.get()
            if (thread != null) {
                lastState = thread.state
                if (lastState == Thread.State.WAITING || lastState == Thread.State.TIMED_WAITING) {
                    return
                }
            }
            Thread.sleep(10L)
        }
        fail("poll thread did not enter WAITING/TIMED_WAITING within 1s; lastState=$lastState")
    }

    private fun testReporter(warningMessages: MutableList<String> = mutableListOf()): RateLimitedDiagnosticReporter =
        RateLimitedDiagnosticReporter(
            cooldownMs = 100L,
            clockMs = { 0L },
            warningLogger = { message, _ -> warningMessages += message },
        )

    private class TestDeviceEventEnvironment(
        private val resolvedForegroundState: ObservedWindowState = ObservedWindowState(),
        private val generation: Long = 0L,
        private val imeStateProvider: () -> ImeState = { ImeState(visible = false, windowId = null) },
    ) : DeviceEventEnvironment {
        override fun foregroundObservation(): ForegroundObservation =
            ForegroundObservation(
                state = resolvedForegroundState,
                generation = generation,
            )

        override fun currentImeState(): ImeState = imeStateProvider()
    }
}
