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
        val sessions = Sessions { 0L }
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

    @Test fun automaticAcquisitionReusesLeaseWhileExplicitOpenReplacesIt() {
        var now = 0L
        val sessions = Sessions { now }
        val original = sessions.open(first, idleMs = 1000)
        now = 10
        assertSame(original, sessions.acquire(first.copy(pairingName = "renamed")))
        assertEquals(first.pairingName, original.owner.pairingName)
        assertEquals(1000L, original.idleMs)
        assertEquals(now, original.touched)

        val reopened = sessions.open(first, idleMs = 2000)
        assertNotSame(original, reopened)
        assertTrue(sessions.isCurrent(reopened))
        assertEquals(2000L, reopened.idleMs)
    }

    @Test fun statusViewsShowActualOwnerAndExpireLease() {
        var now = 0L
        val sessions = Sessions { now }
        sessions.open(first, idleMs = 10)
        val otherCaller = sessions.callerStatus(second)
        assertEquals(first.name, otherCaller.getString("name"))
        assertEquals("none", otherCaller.getString("callerState"))
        now = 10
        val local = sessions.localStatus()
        assertTrue(local.isNull("name"))
        assertTrue(local.isNull("leaseId"))

        sessions.open(first, idleMs = 10)
        now = 20
        val caller = sessions.callerStatus(first)
        assertTrue(caller.isNull("name"))
        assertFalse(caller.has("leaseId"))
        assertEquals("none", caller.getString("callerState"))
    }

    @Test fun busyNamesEscapeFormattingWithoutChangingIdentity() {
        val sessions = Sessions { 0L }
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
        val sessions = Sessions { time }
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
        val sessions = Sessions { 0L }
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
        assertEquals("none", Sessions { 0L }.callerStatus(first)["callerState"])
    }

    @Test fun revokedPairingLosesItsLeaseAndAuthorization() {
        val sessions = Sessions { 0L }
        val lease = sessions.acquire(first)
        assertEquals(setOf(first.pairing), sessions.retainedPairings())
        sessions.revoke(first.pairing)
        assertEquals("revoked", assertThrows(Rejected::class.java) { sessions.requireCurrent(lease) }.code)
        assertEquals("revoked", assertThrows(Rejected::class.java) { sessions.requireAuthorized(first) }.code)
        assertTrue(sessions.retainedPairings().isEmpty())
        assertTrue(sessions.localStatus().isNull("name"))
    }

    @Test fun idleStartsAfterActualResourceEnds() {
        var time = 0L
        val sessions = Sessions { time }
        val lease = sessions.open(first, idleMs = 1000)
        val task = sessions.reserve(lease, ResourceKind.SHELL, changing = true)
        time = 9000
        sessions.expire()
        assertEquals(first.name, sessions.callerStatus(first)["name"])
        assertEquals("active", sessions.callerStatus(first)["callerState"])
        assertEquals(lease.id, sessions.localStatus()["leaseId"])
        assertEquals("busy", assertThrows(Rejected::class.java) { sessions.close(first) }.code)
        assertEquals("busy", assertThrows(Rejected::class.java) { sessions.takeover(second) }.code)
        sessions.finished(task)
        time = 9999
        sessions.expire()
        assertEquals(first.name, sessions.callerStatus(first)["name"])
        assertEquals("active", sessions.callerStatus(first)["callerState"])
        assertEquals(lease.id, sessions.localStatus()["leaseId"])
        time = 10000
        sessions.expire()
        assertEquals("none", sessions.callerStatus(first)["callerState"])
        assertTrue(sessions.callerStatus(first).isNull("name"))
        assertTrue(sessions.localStatus().isNull("leaseId"))
    }

    @Test fun stoppingRejectsAlreadyAcquiredButNotReservedWork() {
        val sessions = Sessions { 0L }
        val lease = sessions.acquire(first)
        val resource = sessions.reserve(lease, ResourceKind.SHELL, changing = true)
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
