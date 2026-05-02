package com.rainng.androidctl.agent.snapshot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test

class RetainedSnapshotStoreTest {
    private val stateLock = Any()

    @Test
    fun evictsOldestNonInflightSnapshotOnly() {
        val store = RetainedSnapshotStore(limit = 2)
        synchronized(stateLock) {
            store.putLocked(snapshotRecord(1L), inFlightSnapshotIds = emptySet())
            store.putLocked(snapshotRecord(2L), inFlightSnapshotIds = emptySet())
            store.putLocked(snapshotRecord(3L), inFlightSnapshotIds = setOf(2L))

            assertNull(store.findLocked(1L))
            assertNotNull(store.findLocked(2L))
            assertNotNull(store.findLocked(3L))
        }
    }

    @Test
    fun putDoesNotEvictNewSnapshotWhenOlderRetainedSnapshotsAreAllInflight() {
        val store = RetainedSnapshotStore(limit = 2)
        synchronized(stateLock) {
            store.putLocked(snapshotRecord(1L), inFlightSnapshotIds = emptySet())
            store.putLocked(snapshotRecord(2L), inFlightSnapshotIds = emptySet())
            store.putLocked(snapshotRecord(3L), inFlightSnapshotIds = setOf(1L, 2L))

            assertNotNull(store.findLocked(1L))
            assertNotNull(store.findLocked(2L))
            assertNotNull(store.findLocked(3L))
            assertEquals(3L, store.findLocked(3L)?.snapshotId)
        }
    }

    private fun snapshotRecord(snapshotId: Long): SnapshotRecord =
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
                                    packageName = "pkg",
                                    className = "android.widget.TextView",
                                    resourceId = null,
                                ),
                        ),
                ),
        )
}
