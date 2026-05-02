package com.rainng.androidctl.agent.events

import android.view.accessibility.AccessibilityEvent
import com.rainng.androidctl.agent.runtime.AgentRuntimeState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class DeviceEventAggregatorTest {
    @Test
    fun recordsPackageFocusAndImeEvents() {
        val buffer = DeviceEventBuffer(capacity = 16, timestampProvider = { "2026-03-15T00:00:00Z" })
        val aggregator = DeviceEventAggregator(buffer)

        aggregator.recordObservation(
            AccessibilityObservation(
                eventType = AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
                generation = 1L,
                packageName = "com.android.settings",
                activityName = "SettingsActivity",
                imeVisible = false,
                imeWindowId = null,
            ),
        )
        aggregator.recordObservation(
            AccessibilityObservation(
                eventType = AccessibilityEvent.TYPE_VIEW_FOCUSED,
                generation = 1L,
                packageName = "com.android.settings",
                activityName = "SettingsActivity",
                imeVisible = true,
                imeWindowId = "w7",
            ),
        )

        val result = buffer.poll(EventPollRequest(afterSeq = 0L, waitMs = 0L, limit = 20))
        val types = result.events.map(DeviceEvent::type)

        assertEquals(
            listOf(
                "package.changed",
                "window.changed",
                "snapshot.invalidated",
                "focus.changed",
                "ime.changed",
            ),
            types,
        )
        val imeEvent = result.events.first { it.type == "ime.changed" }
        val focusEvent = result.events.first { it.type == "focus.changed" }
        assertEquals(
            ImeChangedPayload(
                visible = true,
                windowId = "w7",
            ),
            imeEvent.data,
        )
        assertEquals(
            FocusChangedPayload(
                packageName = "com.android.settings",
                activityName = "SettingsActivity",
                reason = "viewFocused",
            ),
            focusEvent.data,
        )
    }

    @Test
    fun deduplicatesUnchangedRuntimeStatus() {
        val buffer = DeviceEventBuffer(capacity = 16, timestampProvider = { "2026-03-15T00:00:00Z" })
        val aggregator = DeviceEventAggregator(buffer)
        val state =
            AgentRuntimeState(
                serverRunning = true,
                accessibilityEnabled = true,
                accessibilityConnected = true,
                runtimeReady = true,
            )

        aggregator.recordRuntimeStatus(runtimeStatus(state))
        aggregator.recordRuntimeStatus(runtimeStatus(state))
        aggregator.recordRuntimeStatus(runtimeStatus(state.copy(runtimeReady = false)))

        val result = buffer.poll(EventPollRequest(afterSeq = 0L, waitMs = 0L, limit = 20))

        assertEquals(listOf("runtime.status", "runtime.status"), result.events.map(DeviceEvent::type))
        assertEquals(2L, result.latestSeq)
    }

    @Test
    fun deduplicatesMaskedRuntimeStatusAcrossDiagnosticChanges() {
        val buffer = DeviceEventBuffer(capacity = 16, timestampProvider = { "2026-03-15T00:00:00Z" })
        val aggregator = DeviceEventAggregator(buffer)
        val maskedState =
            AgentRuntimeState(
                serverRunning = true,
                accessibilityEnabled = false,
                accessibilityConnected = false,
                runtimeReady = false,
            )

        aggregator.recordRuntimeStatus(runtimeStatus(maskedState))
        aggregator.recordRuntimeStatus(
            runtimeStatus(
                maskedState.copy(
                    lastError = "probe=false while service is still registered",
                    lastRequestSummary = "GET /runtime",
                ),
            ),
        )

        val result = buffer.poll(EventPollRequest(afterSeq = 0L, waitMs = 0L, limit = 20))

        assertEquals(listOf("runtime.status"), result.events.map(DeviceEvent::type))
        assertEquals(
            RuntimeStatusPayload(
                serverRunning = true,
                accessibilityEnabled = false,
                accessibilityConnected = false,
                runtimeReady = false,
            ),
            result.events.single().data,
        )
    }

    @Test
    fun doesNotEmitPackageChangedWhenResolvedForegroundPackageIsAbsent() {
        val buffer = DeviceEventBuffer(capacity = 16, timestampProvider = { "2026-03-15T00:00:00Z" })
        val aggregator = DeviceEventAggregator(buffer)

        aggregator.recordObservation(
            AccessibilityObservation(
                eventType = AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
                generation = 1L,
                packageName = null,
                activityName = null,
                imeVisible = true,
                imeWindowId = "w7",
            ),
        )

        val result = buffer.poll(EventPollRequest(afterSeq = 0L, waitMs = 0L, limit = 20))

        assertEquals(
            listOf("window.changed", "snapshot.invalidated", "ime.changed"),
            result.events.map(DeviceEvent::type),
        )
    }

    @Test
    fun coalescesInvalidationsWithinCooldownAndAutoPublishesPendingPayload() {
        val clock = TestClock()
        val scheduler = TestCooldownScheduler(clock)
        val buffer = DeviceEventBuffer(capacity = 16, timestampProvider = { "2026-03-15T00:00:00Z" })
        val aggregator =
            DeviceEventAggregator(
                buffer,
                cooldownClockMsProvider = { clock.nowMs },
                cooldownScheduler = scheduler,
            )

        aggregator.recordObservation(
            AccessibilityObservation(
                eventType = AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
                generation = 3L,
                packageName = "com.android.settings",
                activityName = "SettingsActivity",
                imeVisible = false,
                imeWindowId = null,
            ),
        )

        scheduler.advanceTo(50L)
        aggregator.recordObservation(
            AccessibilityObservation(
                eventType = AccessibilityEvent.TYPE_VIEW_SCROLLED,
                generation = 3L,
                packageName = "com.android.settings",
                activityName = "SettingsActivity",
                imeVisible = false,
                imeWindowId = null,
            ),
        )

        val beforeCooldownExpiry = buffer.poll(EventPollRequest(afterSeq = 0L, waitMs = 0L, limit = 20))
        assertEquals(1, beforeCooldownExpiry.events.count { it.type == "snapshot.invalidated" })

        scheduler.advanceTo(150L)

        val afterCooldownExpiry =
            buffer.poll(EventPollRequest(afterSeq = beforeCooldownExpiry.latestSeq, waitMs = 0L, limit = 20))
        val invalidations = afterCooldownExpiry.events.filter { it.type == "snapshot.invalidated" }

        assertEquals(1, invalidations.size)
        assertEquals(
            SnapshotInvalidatedPayload(
                packageName = "com.android.settings",
                reason = "viewScrolled",
            ),
            invalidations.single().data,
        )
    }

    @Test
    fun scheduledInvalidationWaitsForAggregatorMonitorBeforePublishing() {
        val clock = TestClock()
        val scheduler = AsyncCooldownScheduler()
        val buffer = DeviceEventBuffer(capacity = 16, timestampProvider = { "2026-03-15T00:00:00Z" })
        val aggregator =
            DeviceEventAggregator(
                buffer,
                cooldownClockMsProvider = { clock.nowMs },
                cooldownScheduler = scheduler,
            )

        aggregator.recordObservation(
            AccessibilityObservation(
                eventType = AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
                generation = 7L,
                packageName = "com.android.settings",
                activityName = "SettingsActivity",
                imeVisible = false,
                imeWindowId = null,
            ),
        )

        clock.nowMs = 50L
        aggregator.recordObservation(
            AccessibilityObservation(
                eventType = AccessibilityEvent.TYPE_VIEW_SCROLLED,
                generation = 7L,
                packageName = "com.android.settings",
                activityName = "SettingsActivity",
                imeVisible = false,
                imeWindowId = null,
            ),
        )

        val firstPoll = buffer.poll(EventPollRequest(afterSeq = 0L, waitMs = 0L, limit = 20))
        val scheduledRun =
            synchronized(aggregator) {
                clock.nowMs = 150L
                scheduler.runPendingTaskAsync().also { run ->
                    assertTrue(run.started.await(1, TimeUnit.SECONDS))
                    assertFalse(run.completed.await(100L, TimeUnit.MILLISECONDS))

                    val whileLocked =
                        buffer.poll(
                            EventPollRequest(
                                afterSeq = firstPoll.latestSeq,
                                waitMs = 0L,
                                limit = 20,
                            ),
                        )
                    assertFalse(whileLocked.events.any { it.type == "snapshot.invalidated" })
                }
            }

        assertTrue(scheduledRun.completed.await(1, TimeUnit.SECONDS))

        val afterRelease =
            buffer.poll(
                EventPollRequest(
                    afterSeq = firstPoll.latestSeq,
                    waitMs = 0L,
                    limit = 20,
                ),
            )
        val invalidations = afterRelease.events.filter { it.type == "snapshot.invalidated" }

        assertEquals(1, invalidations.size)
        assertEquals(
            SnapshotInvalidatedPayload(
                packageName = "com.android.settings",
                reason = "viewScrolled",
            ),
            invalidations.single().data,
        )
    }

    @Test
    fun generationChangeFlushesPendingInvalidationBeforeEmittingNextGeneration() {
        val clock = TestClock()
        val scheduler = TestCooldownScheduler(clock)
        val buffer = DeviceEventBuffer(capacity = 16, timestampProvider = { "2026-03-15T00:00:00Z" })
        val aggregator =
            DeviceEventAggregator(
                buffer,
                cooldownClockMsProvider = { clock.nowMs },
                cooldownScheduler = scheduler,
            )

        aggregator.recordObservation(
            AccessibilityObservation(
                eventType = AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
                generation = 1L,
                packageName = "com.android.settings",
                activityName = "SettingsActivity",
                imeVisible = false,
                imeWindowId = null,
            ),
        )

        scheduler.advanceTo(50L)
        aggregator.recordObservation(
            AccessibilityObservation(
                eventType = AccessibilityEvent.TYPE_VIEW_SCROLLED,
                generation = 1L,
                packageName = "com.android.settings",
                activityName = "SettingsActivity",
                imeVisible = false,
                imeWindowId = null,
            ),
        )

        scheduler.advanceTo(75L)
        aggregator.recordObservation(
            AccessibilityObservation(
                eventType = AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
                generation = 2L,
                packageName = "com.android.settings",
                activityName = "SearchActivity",
                imeVisible = false,
                imeWindowId = null,
            ),
        )

        val invalidationReasons =
            buffer
                .poll(EventPollRequest(afterSeq = 0L, waitMs = 0L, limit = 20))
                .events
                .filter { it.type == "snapshot.invalidated" }
                .map { (it.data as SnapshotInvalidatedPayload).reason }

        assertEquals(
            listOf("windowStateChanged", "viewScrolled", "windowStateChanged"),
            invalidationReasons,
        )
    }

    @Test
    fun cooldownExpiryPublishesStalePendingAndCurrentInvalidationImmediately() {
        val clock = TestClock()
        val scheduler = TestCooldownScheduler(clock)
        val buffer = DeviceEventBuffer(capacity = 16, timestampProvider = { "2026-03-15T00:00:00Z" })
        val aggregator =
            DeviceEventAggregator(
                buffer,
                cooldownClockMsProvider = { clock.nowMs },
                cooldownScheduler = scheduler,
            )

        aggregator.recordObservation(
            AccessibilityObservation(
                eventType = AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
                generation = 4L,
                packageName = "com.android.settings",
                activityName = "SettingsActivity",
                imeVisible = false,
                imeWindowId = null,
            ),
        )

        scheduler.advanceTo(50L)
        aggregator.recordObservation(
            AccessibilityObservation(
                eventType = AccessibilityEvent.TYPE_VIEW_SCROLLED,
                generation = 4L,
                packageName = "com.android.settings",
                activityName = "SettingsActivity",
                imeVisible = false,
                imeWindowId = null,
            ),
        )

        clock.nowMs = 200L
        aggregator.recordObservation(
            AccessibilityObservation(
                eventType = AccessibilityEvent.TYPE_VIEW_CLICKED,
                generation = 4L,
                packageName = "com.android.settings",
                activityName = "SettingsActivity",
                imeVisible = false,
                imeWindowId = null,
            ),
        )

        val invalidationReasons =
            buffer
                .poll(EventPollRequest(afterSeq = 0L, waitMs = 0L, limit = 20))
                .events
                .filter { it.type == "snapshot.invalidated" }
                .map { (it.data as SnapshotInvalidatedPayload).reason }

        assertEquals(
            listOf("windowStateChanged", "viewScrolled", "viewClicked"),
            invalidationReasons,
        )
    }

    @Test
    fun cancelPendingWorkDropsScheduledInvalidation() {
        val clock = TestClock()
        val scheduler = TestCooldownScheduler(clock)
        val buffer = DeviceEventBuffer(capacity = 16, timestampProvider = { "2026-03-15T00:00:00Z" })
        val aggregator =
            DeviceEventAggregator(
                buffer,
                cooldownClockMsProvider = { clock.nowMs },
                cooldownScheduler = scheduler,
            )

        aggregator.recordObservation(
            AccessibilityObservation(
                eventType = AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
                generation = 5L,
                packageName = "com.android.settings",
                activityName = "SettingsActivity",
                imeVisible = false,
                imeWindowId = null,
            ),
        )

        scheduler.advanceTo(50L)
        aggregator.recordObservation(
            AccessibilityObservation(
                eventType = AccessibilityEvent.TYPE_VIEW_SCROLLED,
                generation = 5L,
                packageName = "com.android.settings",
                activityName = "SettingsActivity",
                imeVisible = false,
                imeWindowId = null,
            ),
        )
        val firstPoll = buffer.poll(EventPollRequest(afterSeq = 0L, waitMs = 0L, limit = 20))

        aggregator.cancelPendingWork()
        scheduler.advanceTo(150L)

        val payload = buffer.poll(EventPollRequest(afterSeq = firstPoll.latestSeq, waitMs = 0L, limit = 20))

        assertFalse(payload.events.any { it.type == "snapshot.invalidated" })
    }

    @Test
    fun closeDropsScheduledInvalidation() {
        val clock = TestClock()
        val scheduler = TestCooldownScheduler(clock)
        val buffer = DeviceEventBuffer(capacity = 16, timestampProvider = { "2026-03-15T00:00:00Z" })
        val aggregator =
            DeviceEventAggregator(
                buffer,
                cooldownClockMsProvider = { clock.nowMs },
                cooldownScheduler = scheduler,
            )

        aggregator.recordObservation(
            AccessibilityObservation(
                eventType = AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
                generation = 6L,
                packageName = "com.android.settings",
                activityName = "SettingsActivity",
                imeVisible = false,
                imeWindowId = null,
            ),
        )

        scheduler.advanceTo(50L)
        aggregator.recordObservation(
            AccessibilityObservation(
                eventType = AccessibilityEvent.TYPE_VIEW_SCROLLED,
                generation = 6L,
                packageName = "com.android.settings",
                activityName = "SettingsActivity",
                imeVisible = false,
                imeWindowId = null,
            ),
        )
        val firstPoll = buffer.poll(EventPollRequest(afterSeq = 0L, waitMs = 0L, limit = 20))

        aggregator.close()
        scheduler.advanceTo(150L)

        val payload = buffer.poll(EventPollRequest(afterSeq = firstPoll.latestSeq, waitMs = 0L, limit = 20))

        assertFalse(payload.events.any { it.type == "snapshot.invalidated" })
    }

    @Test
    fun resetForAttachmentChangeClearsDedupForRuntimeAndForegroundSignals() {
        val buffer = DeviceEventBuffer(capacity = 16, timestampProvider = { "2026-03-15T00:00:00Z" })
        val aggregator = DeviceEventAggregator(buffer)
        val runtimeState =
            AgentRuntimeState(
                serverRunning = true,
                accessibilityEnabled = true,
                accessibilityConnected = true,
                runtimeReady = true,
            )
        val observation =
            AccessibilityObservation(
                eventType = AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
                generation = 1L,
                packageName = "com.android.settings",
                activityName = "SettingsActivity",
                imeVisible = true,
                imeWindowId = "w7",
            )

        aggregator.recordRuntimeStatus(runtimeStatus(runtimeState))
        aggregator.recordObservation(observation)
        val firstPoll = buffer.poll(EventPollRequest(afterSeq = 0L, waitMs = 0L, limit = 20))

        aggregator.resetForAttachmentChange()
        aggregator.recordRuntimeStatus(runtimeStatus(runtimeState))
        aggregator.recordObservation(observation)

        val secondPoll = buffer.poll(EventPollRequest(afterSeq = firstPoll.latestSeq, waitMs = 0L, limit = 20))

        assertEquals(
            listOf(
                "runtime.status",
                "package.changed",
                "window.changed",
                "snapshot.invalidated",
                "ime.changed",
            ),
            secondPoll.events.map(DeviceEvent::type),
        )
    }

    @Test
    fun deduplicatesWindowChangedAcrossReasonVariantsUsingSemanticKey() {
        val buffer = DeviceEventBuffer(capacity = 16, timestampProvider = { "2026-03-15T00:00:00Z" })
        val aggregator = DeviceEventAggregator(buffer)

        aggregator.recordObservation(
            AccessibilityObservation(
                eventType = AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
                generation = 1L,
                packageName = "com.android.settings",
                activityName = "SettingsActivity",
                imeVisible = false,
                imeWindowId = null,
            ),
        )
        aggregator.recordObservation(
            AccessibilityObservation(
                eventType = AccessibilityEvent.TYPE_WINDOWS_CHANGED,
                generation = 2L,
                packageName = "com.android.settings",
                activityName = "SettingsActivity",
                imeVisible = false,
                imeWindowId = null,
            ),
        )

        val windowEvents =
            buffer
                .poll(EventPollRequest(afterSeq = 0L, waitMs = 0L, limit = 20))
                .events
                .filter { it.type == "window.changed" }

        assertEquals(1, windowEvents.size)
        assertEquals(
            WindowChangedPayload(
                packageName = "com.android.settings",
                activityName = "SettingsActivity",
                reason = "windowStateChanged",
            ),
            windowEvents.single().data,
        )
    }

    @Test
    fun focusAndTextSelectionDoNotTriggerSnapshotInvalidation() {
        val buffer = DeviceEventBuffer(capacity = 16, timestampProvider = { "2026-03-15T00:00:00Z" })
        val aggregator = DeviceEventAggregator(buffer)

        aggregator.recordObservation(
            AccessibilityObservation(
                eventType = AccessibilityEvent.TYPE_VIEW_FOCUSED,
                generation = 1L,
                packageName = "com.android.settings",
                activityName = "SettingsActivity",
                imeVisible = false,
                imeWindowId = null,
            ),
        )
        aggregator.recordObservation(
            AccessibilityObservation(
                eventType = AccessibilityEvent.TYPE_VIEW_TEXT_SELECTION_CHANGED,
                generation = 1L,
                packageName = "com.android.settings",
                activityName = "SettingsActivity",
                imeVisible = false,
                imeWindowId = null,
            ),
        )

        val payload = buffer.poll(EventPollRequest(afterSeq = 0L, waitMs = 0L, limit = 20))

        assertFalse(payload.events.any { it.type == "snapshot.invalidated" })
        assertTrue(payload.events.any { it.type == "focus.changed" })
    }

    private class AsyncCooldownScheduler : CooldownScheduler {
        private var pendingTask: (() -> Unit)? = null
        private var cancelled = false

        override fun schedule(
            delayMs: Long,
            task: () -> Unit,
        ): ScheduledTask {
            pendingTask = task
            cancelled = false
            return object : ScheduledTask {
                override fun cancel() {
                    cancelled = true
                    pendingTask = null
                }
            }
        }

        override fun shutdown() {
            cancelled = true
            pendingTask = null
        }

        fun runPendingTaskAsync(): ScheduledTaskRun {
            val task = checkNotNull(pendingTask)
            val started = CountDownLatch(1)
            val completed = CountDownLatch(1)

            Thread {
                try {
                    started.countDown()
                    if (!cancelled) {
                        task()
                    }
                } finally {
                    completed.countDown()
                }
            }.start()

            return ScheduledTaskRun(
                started = started,
                completed = completed,
            )
        }
    }

    private data class ScheduledTaskRun(
        val started: CountDownLatch,
        val completed: CountDownLatch,
    )

    private fun runtimeStatus(state: AgentRuntimeState): RuntimeStatusPayload =
        RuntimeStatusPayload(
            serverRunning = state.serverRunning,
            accessibilityEnabled = state.accessibilityEnabled,
            accessibilityConnected = state.accessibilityConnected,
            runtimeReady = state.runtimeReady,
        )
}
