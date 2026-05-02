package com.rainng.androidctl.agent.snapshot

import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import java.util.concurrent.ConcurrentLinkedQueue
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference

class SnapshotRegistryTest {
    @Before
    fun setUp() {
        SnapshotRegistry.resetForTest()
    }

    @After
    fun tearDown() {
        SnapshotRegistry.resetForTest()
    }

    @Test
    fun snapshotIds_areMonotonic() {
        val first = SnapshotRegistry.nextSnapshotId()
        val second = SnapshotRegistry.nextSnapshotId()

        assertTrue(second > first)
    }

    @Test
    fun recordsSnapshotForFind() {
        val snapshotId = SnapshotRegistry.nextSnapshotId()
        val record =
            SnapshotRecord(
                snapshotId = snapshotId,
                ridToHandle =
                    mapOf(
                        "w1:0" to
                            SnapshotNodeHandle(
                                path = NodePath(windowId = "w1", childIndices = emptyList()),
                                fingerprint =
                                    NodeFingerprint(
                                        windowId = "w1",
                                        packageName = "com.android.settings",
                                        className = "android.widget.FrameLayout",
                                        resourceId = null,
                                    ),
                            ),
                    ),
            )

        assertTrue(publishCurrent(record))

        val retained = SnapshotRegistry.find(snapshotId)
        assertNotNull(retained)
        assertEquals(snapshotId, retained?.snapshotId)
        assertEquals(
            "w1",
            retained
                ?.ridToHandle
                ?.get("w1:0")
                ?.path
                ?.windowId,
        )
    }

    @Test
    fun findReturnsRetainedNonLatestSnapshot() {
        val first = snapshotRecord(snapshotId = SnapshotRegistry.nextSnapshotId(), rid = "w1:0")
        val second = snapshotRecord(snapshotId = SnapshotRegistry.nextSnapshotId(), rid = "w2:0")

        assertTrue(publishCurrent(first))
        assertTrue(publishCurrent(second))

        val retained = SnapshotRegistry.find(first.snapshotId)
        assertNotNull(retained)
        assertEquals(first.snapshotId, retained?.snapshotId)
        assertEquals(
            "w1",
            retained
                ?.ridToHandle
                ?.get("w1:0")
                ?.path
                ?.windowId,
        )
    }

    @Test
    fun retainsResourceIdMissingFingerprintForStaleTargetReview() {
        val snapshotId = SnapshotRegistry.nextSnapshotId()
        val fingerprint =
            NodeFingerprint(
                windowId = "w1",
                packageName = "com.android.settings",
                className = "android.widget.Button",
                resourceId = null,
            )
        val record =
            SnapshotRecord(
                snapshotId = snapshotId,
                ridToHandle =
                    mapOf(
                        "w1:0" to
                            SnapshotNodeHandle(
                                path = NodePath(windowId = "w1", childIndices = emptyList()),
                                fingerprint = fingerprint,
                            ),
                    ),
            )

        assertTrue(publishCurrent(record))

        assertEquals(
            fingerprint,
            SnapshotRegistry
                .find(snapshotId)
                ?.ridToHandle
                ?.get("w1:0")
                ?.fingerprint,
        )
    }

    @Test
    fun evictsOldestSnapshotBeyondRetainedWindow() {
        val snapshotIds =
            (1..9).map {
                SnapshotRegistry.nextSnapshotId()
            }

        snapshotIds.forEach { snapshotId ->
            assertTrue(
                publishCurrent(
                    snapshotRecord(snapshotId = snapshotId, rid = "w$snapshotId:0"),
                ),
            )
        }

        assertNull(SnapshotRegistry.find(snapshotIds.first()))
        assertEquals(snapshotIds.last(), SnapshotRegistry.find(snapshotIds.last())?.snapshotId)
        assertEquals(snapshotIds[1], SnapshotRegistry.find(snapshotIds[1])?.snapshotId)
    }

    @Test
    fun resetSessionStateAdvancesGenerationAndRejectsCapturedPublication() {
        val generation = SnapshotRegistry.currentGeneration()
        val snapshotId = SnapshotRegistry.nextSnapshotId()

        val result = SnapshotRegistry.resetSessionState()

        assertEquals(
            SnapshotResetResult(completed = true, timedOut = false, activePublicationCount = 0, timeoutMs = 1_000L),
            result,
        )
        assertEquals(generation + 1L, SnapshotRegistry.currentGeneration())
        val stalePublication =
            SnapshotRegistry.beginPublicationIfCurrent(
                generation,
                snapshotRecord(snapshotId = snapshotId, rid = "w1:0"),
            )
        if (stalePublication != null) {
            stalePublication.release()
            fail("stale generation unexpectedly published")
        }
        assertNull(SnapshotRegistry.find(snapshotId))
    }

    @Test
    fun resetSessionStateRejectsNewPublicationWhileWaitingForActivePublication() {
        val activePublication =
            SnapshotRegistry.beginPublicationIfCurrent(
                SnapshotRegistry.currentGeneration(),
                snapshotRecord(snapshotId = SnapshotRegistry.nextSnapshotId(), rid = "w-active:0"),
            )
        assertNotNull(activePublication)
        val resetStarted = CountDownLatch(1)
        val resetFinished = CountDownLatch(1)
        val resetResult = AtomicReference<SnapshotResetResult?>()

        val resetThread =
            Thread {
                resetStarted.countDown()
                resetResult.set(SnapshotRegistry.resetSessionState(timeoutMs = 1_000L))
                resetFinished.countDown()
            }
        resetThread.start()
        assertTrue(resetStarted.await(1, TimeUnit.SECONDS))
        assertFalse(resetFinished.await(100, TimeUnit.MILLISECONDS))

        val blockedPublication =
            SnapshotRegistry.beginPublicationIfCurrent(
                SnapshotRegistry.currentGeneration(),
                snapshotRecord(snapshotId = SnapshotRegistry.nextSnapshotId(), rid = "w-blocked:0"),
            )

        assertNull(blockedPublication)
        activePublication?.release()
        assertTrue(resetFinished.await(1, TimeUnit.SECONDS))
        assertEquals(
            SnapshotResetResult(completed = true, timedOut = false, activePublicationCount = 0, timeoutMs = 1_000L),
            resetResult.get(),
        )
    }

    @Test
    fun resetSessionStateTimesOutAndClearsRetainedSnapshots() {
        val generation = SnapshotRegistry.currentGeneration()
        val snapshotId = SnapshotRegistry.nextSnapshotId()
        val activePublication =
            SnapshotRegistry.beginPublicationIfCurrent(
                generation,
                snapshotRecord(snapshotId = snapshotId, rid = "w-active:0"),
            )
        assertNotNull(activePublication)

        val result = SnapshotRegistry.resetSessionState(timeoutMs = 0L)

        assertEquals(
            SnapshotResetResult(completed = false, timedOut = true, activePublicationCount = 1, timeoutMs = 0L),
            result,
        )
        assertEquals(generation + 1L, SnapshotRegistry.currentGeneration())
        assertNull(SnapshotRegistry.find(snapshotId))

        activePublication?.release()
        assertEquals(
            SnapshotResetResult(completed = true, timedOut = false, activePublicationCount = 0, timeoutMs = 0L),
            SnapshotRegistry.resetSessionState(timeoutMs = 0L),
        )
    }

    @Test
    fun inFlightPublicationIsNotEvictedByRetainedWindowOverflow() {
        val inFlightSnapshotId = SnapshotRegistry.nextSnapshotId()
        val inFlightPublication =
            SnapshotRegistry.beginPublicationIfCurrent(
                SnapshotRegistry.currentGeneration(),
                snapshotRecord(snapshotId = inFlightSnapshotId, rid = "w-in-flight:0"),
            )
        assertNotNull(inFlightPublication)

        repeat(8) { index ->
            val newerSnapshotId = SnapshotRegistry.nextSnapshotId()
            assertTrue(
                publishCurrent(
                    snapshotRecord(snapshotId = newerSnapshotId, rid = "w${index + 2}:0"),
                ),
            )
        }

        assertNotNull(SnapshotRegistry.find(inFlightSnapshotId))
        inFlightPublication?.release()
        assertNotNull(SnapshotRegistry.find(inFlightSnapshotId))
    }

    @Test
    fun newlyPublishedSnapshotIsNotEvictedByRetainedWindowOverflow() {
        val inFlightPublications =
            (1..8).map { index ->
                val inFlightSnapshotId = SnapshotRegistry.nextSnapshotId()
                SnapshotRegistry.beginPublicationIfCurrent(
                    SnapshotRegistry.currentGeneration(),
                    snapshotRecord(snapshotId = inFlightSnapshotId, rid = "w-in-flight:$index"),
                )
            }
        inFlightPublications.forEach { publication ->
            assertNotNull(publication)
        }

        val latestSnapshotId = SnapshotRegistry.nextSnapshotId()
        assertTrue(
            publishCurrent(
                snapshotRecord(snapshotId = latestSnapshotId, rid = "w-new:0"),
            ),
        )

        assertNotNull(SnapshotRegistry.find(latestSnapshotId))
        inFlightPublications.forEach { publication ->
            publication?.release()
        }
    }

    @Test
    fun overlappingResetsKeepBlockingNewPublicationsUntilAllResetsFinish() {
        repeat(100) { iteration ->
            SnapshotRegistry.resetForTest()
            val activePublication =
                SnapshotRegistry.beginPublicationIfCurrent(
                    SnapshotRegistry.currentGeneration(),
                    snapshotRecord(snapshotId = SnapshotRegistry.nextSnapshotId(), rid = "w-active:$iteration"),
                )
            assertNotNull(activePublication)
            val resetStarted = CountDownLatch(2)
            val resetFinished = CountDownLatch(2)

            repeat(2) {
                Thread {
                    resetStarted.countDown()
                    SnapshotRegistry.resetSessionState()
                    resetFinished.countDown()
                }.start()
            }

            assertTrue(resetStarted.await(1, TimeUnit.SECONDS))
            assertFalse(resetFinished.await(100, TimeUnit.MILLISECONDS))
            activePublication?.release()

            val deadlineNanos = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(250)
            while (resetFinished.count > 0 && System.nanoTime() < deadlineNanos) {
                val leakedPublication =
                    SnapshotRegistry.beginPublicationIfCurrent(
                        SnapshotRegistry.currentGeneration(),
                        snapshotRecord(snapshotId = SnapshotRegistry.nextSnapshotId(), rid = "w-leak:$iteration"),
                    )
                if (leakedPublication != null) {
                    if (SnapshotRegistry.resetInProgressForTest()) {
                        leakedPublication.release()
                        fail("publication leaked while overlapping resets were still pending")
                    }
                    leakedPublication.release()
                    break
                }
                Thread.yield()
            }

            assertTrue(resetFinished.await(1, TimeUnit.SECONDS))
        }
    }

    @Test
    fun concurrentPublishFindAndResetDoNotThrowOrDeadlock() {
        val executor = Executors.newFixedThreadPool(4)
        val startGate = CountDownLatch(1)
        val completionGate = CountDownLatch(4)
        val failure = AtomicReference<Throwable?>(null)
        val publishedSnapshotIds = ConcurrentLinkedQueue<Long>()
        val firstPublished = CountDownLatch(1)

        repeat(2) { workerIndex ->
            executor.execute {
                try {
                    startGate.await()
                    repeat(200) {
                        val snapshotId = SnapshotRegistry.nextSnapshotId()
                        val record = snapshotRecord(snapshotId = snapshotId, rid = "w$workerIndex:$it")
                        if (publishCurrent(record)) {
                            publishedSnapshotIds.add(snapshotId)
                            firstPublished.countDown()
                        }
                    }
                } catch (error: Throwable) {
                    failure.compareAndSet(null, error)
                } finally {
                    completionGate.countDown()
                }
            }
        }

        executor.execute {
            try {
                startGate.await()
                if (!firstPublished.await(2, TimeUnit.SECONDS)) {
                    throw AssertionError("expected at least one successful publication before reading")
                }
                repeat(200) {
                    publishedSnapshotIds.forEach { snapshotId ->
                        SnapshotRegistry.find(snapshotId)
                    }
                }
            } catch (error: Throwable) {
                failure.compareAndSet(null, error)
            } finally {
                completionGate.countDown()
            }
        }

        executor.execute {
            try {
                startGate.await()
                repeat(50) {
                    SnapshotRegistry.resetSessionState()
                }
            } catch (error: Throwable) {
                failure.compareAndSet(null, error)
            } finally {
                completionGate.countDown()
            }
        }

        startGate.countDown()
        assertTrue(completionGate.await(5, TimeUnit.SECONDS))
        executor.shutdownNow()

        failure.get()?.let { throw AssertionError("concurrent registry access failed", it) }
    }

    private fun publishCurrent(record: SnapshotRecord): Boolean {
        val publication =
            SnapshotRegistry.beginPublicationIfCurrent(
                SnapshotRegistry.currentGeneration(),
                record,
            ) ?: return false
        publication.release()
        return true
    }

    private fun snapshotRecord(
        snapshotId: Long,
        rid: String,
    ): SnapshotRecord =
        SnapshotRecord(
            snapshotId = snapshotId,
            ridToHandle =
                mapOf(
                    rid to
                        SnapshotNodeHandle(
                            path = NodePath(windowId = rid.substringBefore(':'), childIndices = emptyList()),
                            fingerprint =
                                NodeFingerprint(
                                    windowId = rid.substringBefore(':'),
                                    packageName = "com.android.settings",
                                    className = "android.widget.Button",
                                    resourceId = "android:id/button1",
                                ),
                        ),
                ),
        )
}
