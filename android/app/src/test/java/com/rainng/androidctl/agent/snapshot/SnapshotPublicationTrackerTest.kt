package com.rainng.androidctl.agent.snapshot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class SnapshotPublicationTrackerTest {
    private val stateLock = Any()

    @Test
    fun beginAndCompletePublicationTracksInflightSnapshotIds() {
        val tracker = SnapshotPublicationTracker()
        synchronized(stateLock) {
            tracker.beginPublicationLocked(snapshotId = 42L)

            assertTrue(tracker.hasActivePublicationsLocked())
            assertTrue(tracker.isPublicationInFlightLocked(42L))
            assertEquals(setOf(42L), tracker.inFlightSnapshotIdsLocked())

            tracker.completePublicationLocked(snapshotId = 42L, stateLock = stateLock)

            assertFalse(tracker.hasActivePublicationsLocked())
            assertFalse(tracker.isPublicationInFlightLocked(42L))
            assertTrue(tracker.inFlightSnapshotIdsLocked().isEmpty())
        }
    }

    @Test
    fun duplicateSnapshotIdPublicationsStayActiveUntilBothComplete() {
        val tracker = SnapshotPublicationTracker()
        synchronized(stateLock) {
            tracker.beginPublicationLocked(snapshotId = 42L)
            tracker.beginPublicationLocked(snapshotId = 42L)

            assertTrue(tracker.hasActivePublicationsLocked())
            assertTrue(tracker.isPublicationInFlightLocked(42L))

            tracker.completePublicationLocked(snapshotId = 42L, stateLock = stateLock)

            assertTrue(tracker.hasActivePublicationsLocked())
            assertTrue(tracker.isPublicationInFlightLocked(42L))

            tracker.completePublicationLocked(snapshotId = 42L, stateLock = stateLock)

            assertFalse(tracker.hasActivePublicationsLocked())
            assertFalse(tracker.isPublicationInFlightLocked(42L))
        }
    }

    @Test
    fun waitForActivePublicationsBlocksUntilLastPublicationCompletes() {
        val tracker = SnapshotPublicationTracker()
        synchronized(stateLock) {
            tracker.beginPublicationLocked(snapshotId = 42L)
        }
        val waitStarted = CountDownLatch(1)
        val waitFinished = CountDownLatch(1)

        val waitingThread =
            Thread {
                synchronized(stateLock) {
                    waitStarted.countDown()
                    tracker.waitForActivePublicationsLocked(stateLock, timeoutMs = 1_000L)
                    waitFinished.countDown()
                }
            }
        waitingThread.start()

        assertTrue(waitStarted.await(1, TimeUnit.SECONDS))
        assertFalse(waitFinished.await(100, TimeUnit.MILLISECONDS))

        synchronized(stateLock) {
            tracker.completePublicationLocked(snapshotId = 42L, stateLock = stateLock)
        }

        assertTrue(waitFinished.await(1, TimeUnit.SECONDS))
        waitingThread.join(1_000L)
        assertFalse(waitingThread.isAlive)
    }

    @Test
    fun boundedWaitReportsTimeoutAndAbandonedPublicationReleaseIsIgnored() {
        val tracker = SnapshotPublicationTracker()
        synchronized(stateLock) {
            tracker.beginPublicationLocked(snapshotId = 42L)

            val result = tracker.waitForActivePublicationsLocked(stateLock, timeoutMs = 0L)
            tracker.abandonActivePublicationsLocked(stateLock)
            tracker.completePublicationLocked(snapshotId = 42L, stateLock = stateLock)

            assertFalse(result.completed)
            assertEquals(1, result.activePublicationCount)
            assertFalse(tracker.hasActivePublicationsLocked())
            assertFalse(tracker.isPublicationInFlightLocked(42L))
        }
    }

    @Test
    fun timeoutReportsTotalPublicationCountAcrossDuplicateSnapshotIds() {
        val tracker = SnapshotPublicationTracker()
        synchronized(stateLock) {
            tracker.beginPublicationLocked(snapshotId = 42L)
            tracker.beginPublicationLocked(snapshotId = 42L)
            tracker.beginPublicationLocked(snapshotId = 43L)

            val result = tracker.waitForActivePublicationsLocked(stateLock, timeoutMs = 0L)

            assertFalse(result.completed)
            assertEquals(3, result.activePublicationCount)
            assertEquals(setOf(42L, 43L), tracker.inFlightSnapshotIdsLocked())

            tracker.abandonActivePublicationsLocked(stateLock)
        }
    }
}
