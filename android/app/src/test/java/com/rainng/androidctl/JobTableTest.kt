package com.rainng.androidctl

import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

class JobTableTest {
    private val owner = SessionOwner("pair", "task", "desktop")

    private class TestJob(
        override val id: String,
        override val owner: SessionOwner,
        override var ended: Boolean = false,
        private val cancelled: () -> Unit = {},
    ) : Job {
        override fun cancel() = cancelled()
    }

    private fun table(): JobTable<TestJob> = JobTable("Job", "Resources still active")

    @Test fun ownershipKeepsPairingAndSessionAfterEntryAuthorization() {
        val sessions = Sessions { 0L }
        val table = table()
        val job = TestJob("j", owner)
        sessions.acquire(owner)
        table.start(job) { }
        val renamed = owner.copy(pairingName = "renamed")
        assertEquals("active", sessions.callerStatus(renamed).getString("callerState"))
        assertSame(job, table.find(renamed, "j"))
        for (other in listOf(owner.copy(pairing = "other"), owner.copy(name = "other"))) {
            assertEquals("jobOwner", assertThrows(Rejected::class.java) { table.find(other, "j") }.code)
        }
        sessions.revoke(owner.pairing)
        assertEquals("revoked", assertThrows(Rejected::class.java) { sessions.requireAuthorized(owner) }.code)
        assertSame(job, table.find(owner, "j"))
        assertEquals("jobUnavailable", assertThrows(Rejected::class.java) { table.find(owner, "missing") }.code)
    }

    @Test fun retentionNeverEvictsLiveJobsAndFailedStartupLeavesNoJob() {
        val table = table()
        val live = TestJob("live", owner)
        table.start(live) { }
        repeat(65) { number ->
            val job = TestJob("$number", owner)
            table.start(job) {
                job.ended = true
                table.trim()
            }
        }
        assertSame(live, table.find(owner, "live"))
        assertEquals("jobUnavailable", assertThrows(Rejected::class.java) { table.find(owner, "0") }.code)
        assertEquals("64", table.find(owner, "64").id)
        assertThrows(IllegalStateException::class.java) { table.start(TestJob("failed", owner)) { error("startup refused") } }
        assertEquals("jobUnavailable", assertThrows(Rejected::class.java) { table.find(owner, "failed") }.code)
    }

    @Test fun workerStopRequiresBothRegisteredJobsAndUnregisteredReservationsToEnd() {
        val sessions = Sessions { 0L }
        val table = table()
        val lease = sessions.acquire(owner)
        val resource = sessions.reserve(lease, ResourceKind.SHELL, changing = true)
        assertEquals("busy", assertThrows(Rejected::class.java) { table.prepareStop { sessions.stop() } }.code)
        val live = TestJob("live", owner)
        table.start(live) { }
        sessions.finished(resource)
        assertEquals("busy", assertThrows(Rejected::class.java) { table.prepareStop { sessions.stop() } }.code)
        live.ended = true
        table.prepareStop { sessions.stop() }
        assertEquals("stopping", assertThrows(Rejected::class.java) { sessions.reserve(lease, ResourceKind.SHELL, changing = true) }.code)
    }

    @Test fun slowCancellationDoesNotHoldTheRegistryLock() {
        val table = table()
        val entered = CountDownLatch(1)
        val release = CountDownLatch(1)
        var otherCancelled = false
        val job =
            TestJob("j", owner) {
                entered.countDown()
                check(release.await(5, TimeUnit.SECONDS))
            }
        table.start(job) { }
        table.start(TestJob("other", owner.copy(pairing = "other")) { otherCancelled = true }) { }
        val executor = Executors.newFixedThreadPool(2)
        try {
            val cancel = executor.submit { table.revoke(owner.pairing) }
            assertTrue(entered.await(5, TimeUnit.SECONDS))
            val query = executor.submit<TestJob> { table.find(owner, "j") }
            assertSame(job, query.get(5, TimeUnit.SECONDS))
            release.countDown()
            cancel.get(5, TimeUnit.SECONDS)
            assertEquals(false, otherCancelled)
        } finally {
            release.countDown()
            executor.shutdown()
            assertTrue(executor.awaitTermination(5, TimeUnit.SECONDS))
        }
    }
}
