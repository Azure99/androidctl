package com.rainng.androidctl.agent.snapshot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SnapshotGenerationGateTest {
    private val stateLock = Any()

    @Test
    fun beginResetAdvancesGenerationAndBlocksPublicationUntilResetFinishes() {
        val gate = SnapshotGenerationGate()
        synchronized(stateLock) {
            val generation = gate.currentGenerationLocked()

            assertTrue(gate.canPublishLocked(generation))
            gate.beginResetLocked()

            assertEquals(generation + 1L, gate.currentGenerationLocked())
            assertFalse(gate.canPublishLocked(generation))
            assertFalse(gate.canPublishLocked(generation + 1L))

            gate.finishResetLocked()
            assertTrue(gate.canPublishLocked(generation + 1L))
        }
    }

    @Test
    fun overlappingResetsKeepPublicationBlockedUntilAllFinish() {
        val gate = SnapshotGenerationGate()
        synchronized(stateLock) {
            gate.beginResetLocked()
            gate.beginResetLocked()
            val currentGeneration = gate.currentGenerationLocked()

            assertEquals(2L, currentGeneration)
            assertFalse(gate.canPublishLocked(currentGeneration))

            gate.finishResetLocked()
            assertFalse(gate.canPublishLocked(currentGeneration))

            gate.finishResetLocked()
            assertTrue(gate.canPublishLocked(currentGeneration))
        }
    }

    @Test
    fun snapshotIdsRemainMonotonicAcrossResets() {
        val gate = SnapshotGenerationGate()
        val first = gate.nextSnapshotId()

        synchronized(stateLock) {
            gate.beginResetLocked()
        }
        val second = gate.nextSnapshotId()

        assertTrue(second > first)
    }
}
