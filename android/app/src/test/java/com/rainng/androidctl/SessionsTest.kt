package com.rainng.androidctl

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNotSame
import org.junit.Assert.assertSame
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class SessionsTest {
    private val first = SessionOwner("pair-a", "checkout", "workstation")
    private val second = first.copy(name = "research")

    @Test fun localAndCallerStatusExposeDistinctFacts() {
        val sessions = Sessions({ 0L }) { }
        val idle = sessions.localStatus()
        assertTrue(idle.has("name") && idle.isNull("name"))
        assertTrue(idle.has("pairingName") && idle.isNull("pairingName"))
        assertTrue(idle.has("leaseId") && idle.isNull("leaseId"))
        assertFalse(idle.has("callerState"))
        val caller = sessions.callerStatus(first)
        assertFalse(caller.has("leaseId"))
        assertEquals("none", caller.getString("callerState"))
        val lease = sessions.acquire(first)
        assertEquals(lease.id, sessions.localStatus().getLong("leaseId"))
        val held = sessions.callerStatus(first)
        assertFalse(held.has("leaseId"))
        assertEquals("active", held.getString("callerState"))
        assertEquals(first.pairingName, held.getString("pairingName"))
    }

    @Test fun automaticAcquisitionReusesLeaseWhileExplicitOpenClearsIt() {
        var now = 0L
        val cleared = mutableListOf<Lease>()
        lateinit var closing: Resource
        lateinit var sessions: Sessions
        sessions =
            Sessions({ now }) { lease ->
                assertFalse(sessions.isCurrent(lease))
                cleared.add(lease)
                closing = sessions.closing(lease)
            }
        val original = sessions.open(first, idleMs = 1000)
        now = 10
        assertSame(original, sessions.acquire(first.copy(pairingName = "renamed")))
        assertEquals(first.pairingName, original.owner.pairingName)
        assertEquals(1000L, original.idleMs)
        assertEquals(now, original.touched)
        assertTrue(cleared.isEmpty())

        val reopened = sessions.open(first, idleMs = 2000)
        assertNotSame(original, reopened)
        assertTrue(sessions.isCurrent(reopened))
        assertEquals(listOf(original), cleared)
        assertEquals(2000L, reopened.idleMs)
        assertEquals("busy", assertThrows(Rejected::class.java) { sessions.reserve(reopened, ResourceKind.UI, changing = false) }.code)
        sessions.finished(closing)
        sessions.finished(sessions.reserve(reopened, ResourceKind.UI, changing = false))
    }

    @Test fun statusViewsShowActualOwnerAndExpireLease() {
        var now = 0L
        val cleared = mutableListOf<Lease>()
        val sessions = Sessions({ now }) { cleared.add(it) }
        val original = sessions.open(first, idleMs = 10)
        val otherCaller = sessions.callerStatus(second)
        assertEquals(first.name, otherCaller.getString("name"))
        assertEquals("none", otherCaller.getString("callerState"))
        now = 10
        val local = sessions.localStatus()
        assertTrue(local.isNull("name"))
        assertTrue(local.isNull("leaseId"))
        assertEquals(listOf(original), cleared)

        val next = sessions.open(first, idleMs = 10)
        now = 20
        val caller = sessions.callerStatus(first)
        assertTrue(caller.isNull("name"))
        assertFalse(caller.has("leaseId"))
        assertEquals("none", caller.getString("callerState"))
        assertEquals(listOf(original, next), cleared)
    }

    @Test fun busyNamesEscapeFormattingWithoutChangingIdentity() {
        val sessions = Sessions({ 0L }) { }
        val owner = first.copy(name = "task\u202e", pairingName = "desk\u2066")
        sessions.acquire(owner)
        val failure = assertThrows(Rejected::class.java) { sessions.acquire(second) }
        assertEquals("busy", failure.code)
        assertTrue(failure.message.contains("desk\\u2066/task\\u202e"))
        assertEquals(owner.name, sessions.localStatus()["name"])
        assertEquals(owner.pairingName, sessions.localStatus()["pairingName"])
    }

    @Test fun leaseSeparatesSessionsAndPairings() {
        var time = 0L
        val sessions = Sessions({ time }) { }
        sessions.acquire(first)
        time = 12000
        assertEquals(12L, sessions.callerStatus(first)["idleSeconds"])
        val failure = assertThrows(Rejected::class.java) { sessions.acquire(second) }
        assertEquals("busy", failure.code)
        assertTrue(failure.message.contains("workstation/checkout idle=12s"))
        assertEquals("busy", assertThrows(Rejected::class.java) { sessions.takeover(first.copy(pairing = "pair-b")) }.code)
        assertEquals("checkout", sessions.takeover(second))
        assertEquals("research", sessions.localStatus()["name"])
    }

    @Test fun localReleasePreventsDispatchAndRequiresExplicitReopen() {
        val sessions = Sessions({ 0L }) { }
        val renamed = first.copy(pairingName = "renamed workstation")
        assertEquals("none", sessions.callerStatus(first)["callerState"])
        val old = sessions.acquire(first)
        assertEquals("active", sessions.callerStatus(first)["callerState"])
        sessions.releaseLocal(old.id)
        assertEquals("released", sessions.callerStatus(renamed)["callerState"])
        assertEquals("none", sessions.callerStatus(second)["callerState"])
        assertEquals("sessionChanged", assertThrows(Rejected::class.java) { sessions.requireCurrent(old) }.code)
        assertEquals("sessionReleased", assertThrows(Rejected::class.java) { sessions.acquire(renamed) }.code)
        val next = sessions.acquire(second)
        assertEquals("sessionChanged", assertThrows(Rejected::class.java) { sessions.releaseLocal(old.id) }.code)
        assertEquals("busy", assertThrows(Rejected::class.java) { sessions.open(first) }.code)
        assertTrue(sessions.isCurrent(next))
        sessions.close(second)
        assertEquals("sessionReleased", assertThrows(Rejected::class.java) { sessions.acquire(first) }.code)
        assertNotEquals(old.id, sessions.open(renamed).id)
        assertEquals("active", sessions.callerStatus(first)["callerState"])
        assertEquals("none", Sessions({ 0L }) { }.callerStatus(first)["callerState"])
    }

    @Test fun uiOwnershipQueryExpiresEvenWithoutCallerAndTracksAllUiResources() {
        var now = 0L
        var cleared = 0
        val sessions = Sessions({ now }) { cleared++ }
        sessions.open(first, idleMs = 10)
        assertTrue(sessions.ownsIdleUi(first.copy(pairingName = "other display name")))
        assertFalse(sessions.ownsIdleUi(second))
        now = 10
        assertFalse(sessions.ownsIdleUi(null))
        assertEquals(1, cleared)
        val lease = sessions.acquire(first)
        for (kind in listOf(ResourceKind.SHELL, ResourceKind.FILE)) {
            val resource = sessions.reserve(lease, kind, changing = true)
            assertTrue(sessions.ownsIdleUi(first))
            sessions.finished(resource)
        }
        val ui = sessions.reserve(lease, ResourceKind.UI, changing = false)
        assertFalse(sessions.ownsIdleUi(first))
        sessions.finished(ui)
        val closing = sessions.closing(lease)
        sessions.releaseLocal(lease.id)
        sessions.acquire(second)
        assertFalse(sessions.ownsIdleUi(second))
        sessions.finished(closing)
        assertTrue(sessions.ownsIdleUi(second))
    }

    @Test fun lateResourceKeepsMutexButCannotPublishIntoNewLease() {
        val sessions = Sessions({ 0L }) { }
        val old = sessions.acquire(first)
        val resource = sessions.reserve(old, ResourceKind.UI, changing = true)
        sessions.releaseLocal(old.id)
        val next = sessions.acquire(second)
        var written = false
        assertEquals("sessionChanged", assertThrows(Rejected::class.java) { sessions.withCurrentLease(old) { written = true } }.code)
        assertFalse(written)
        assertEquals("busy", assertThrows(Rejected::class.java) { sessions.reserve(next, ResourceKind.UI, changing = false) }.code)
        sessions.finished(resource)
        sessions.withCurrentLease(next) { written = true }
        assertTrue(written)
        sessions.finished(sessions.reserve(next, ResourceKind.UI, changing = true))
    }

    @Test fun revokedReleasedResourcesRemainBusyUntilTheyActuallyFinish() {
        lateinit var sessions: Sessions
        lateinit var closing: Resource
        sessions = Sessions({ 0L }) { closing = sessions.closing(it) }
        val old = sessions.acquire(first)
        val file = sessions.reserve(old, ResourceKind.FILE, changing = true)
        sessions.releaseLocal(old.id)
        val allowed = first.copy(pairing = "pair-b")
        val next = sessions.acquire(allowed)
        val removed = sessions.retainedPairings() - setOf(allowed.pairing)
        assertEquals(setOf(first.pairing), removed)
        removed.forEach(sessions::revoke)
        assertEquals("revoked", assertThrows(Rejected::class.java) { sessions.requireAuthorized(first) }.code)
        assertTrue(sessions.isCurrent(next))
        assertEquals("busy", assertThrows(Rejected::class.java) { sessions.requireIdle() }.code)
        sessions.finished(file)
        assertEquals("busy", assertThrows(Rejected::class.java) { sessions.requireIdle() }.code)
        sessions.finished(closing)
        sessions.requireIdle()
        assertEquals(setOf(allowed.pairing), sessions.retainedPairings())
        sessions.finished(sessions.reserve(next, ResourceKind.UI, changing = true))
    }

    @Test fun idleStartsAfterActualResourceEnds() {
        var time = 0L
        var cleared = 0
        val sessions = Sessions({ time }) { cleared++ }
        val lease = sessions.open(first, idleMs = 1000)
        val task = sessions.reserve(lease, ResourceKind.FILE, changing = true)
        time = 9000
        sessions.expire()
        assertEquals(0, cleared)
        assertEquals("busy", assertThrows(Rejected::class.java) { sessions.close(first) }.code)
        assertEquals("busy", assertThrows(Rejected::class.java) { sessions.takeover(second) }.code)
        sessions.finished(task)
        time = 9999
        sessions.expire()
        assertEquals(0, cleared)
        time = 10000
        sessions.expire()
        assertEquals(1, cleared)
    }

    @Test fun shellFrontBlocksMutationButAllowsSameLeaseReadOnlyUi() {
        val sessions = Sessions({ 0L }) { }
        val lease = sessions.acquire(first)
        val shell = sessions.reserve(lease, ResourceKind.SHELL, changing = true)
        assertEquals("busy", assertThrows(Rejected::class.java) { sessions.reserve(lease, ResourceKind.UI, changing = true) }.code)
        val snapshot = sessions.reserve(lease, ResourceKind.UI, changing = false)
        sessions.finished(snapshot)
        sessions.revoke(first.pairing)
        assertEquals("revoked", assertThrows(Rejected::class.java) { sessions.requireCurrent(lease) }.code)
        assertEquals("revoked", assertThrows(Rejected::class.java) { sessions.requireAuthorized(first) }.code)
        sessions.finished(shell)
        sessions.requireIdle()
    }

    @Test fun stoppingRejectsAlreadyAcquiredButNotReservedWork() {
        val sessions = Sessions({ 0L }) { }
        val lease = sessions.acquire(first)
        val resource = sessions.reserve(lease, ResourceKind.FILE, changing = true)
        assertEquals("busy", assertThrows(Rejected::class.java) { sessions.stop() }.code)
        sessions.finished(resource)
        sessions.stop()
        assertEquals("stopping", assertThrows(Rejected::class.java) { sessions.acquire(first) }.code)
        assertEquals("stopping", assertThrows(Rejected::class.java) { sessions.reserve(lease, ResourceKind.SHELL, changing = true) }.code)
        var sent = false
        assertEquals("stopping", assertThrows(Rejected::class.java) { sessions.withCurrentLease(lease) { sent = true } }.code)
        assertFalse(sent)
        sessions.requireAuthorized(first)
        assertEquals(first.name, sessions.callerStatus(second)["name"])
        assertEquals("none", sessions.callerStatus(second)["callerState"])
        assertEquals(lease.id, sessions.localStatus()["leaseId"])
    }
}
