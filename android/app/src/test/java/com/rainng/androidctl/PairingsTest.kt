package com.rainng.androidctl

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertThrows
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.io.IOException

class PairingsTest {
    @get:Rule val temporary = TemporaryFolder()

    @Test fun sameDigitsDoNotReviveAnOldAuthenticatedWindow() {
        var now = 0L
        val pairings = Pairings(File(temporary.root, "pairs"), { now }, { "000042" })
        pairings.open()
        assertEquals("000042", pairings.display()!!.code)
        assertEquals(600L, pairings.display()!!.remainingSeconds)
        val old = pairings.acceptCode("000042")!!
        assertSame(old, pairings.acceptCode("000042"))
        pairings.open()
        assertEquals("pairingExpired", assertThrows(Rejected::class.java) { pairings.register(old, "c".repeat(40), "host") }.code)
        val current = pairings.acceptCode("000042")!!
        now = 600000L
        assertNull(pairings.display())
        assertNull(pairings.acceptCode("000042"))
        assertEquals("pairingExpired", assertThrows(Rejected::class.java) { pairings.register(current, "c".repeat(40), "host") }.code)
    }

    @Test fun fifthErrorInvalidatesAlreadyAuthenticatedTickets() {
        val pairings = Pairings(File(temporary.root, "pairs"), { 0L }, { "000042" })
        pairings.open()
        val ticket = pairings.acceptCode("000042")!!
        repeat(4) {
            assertNull(pairings.acceptCode(""))
            assertNull(pairings.acceptCode("999999"))
        }
        assertSame(ticket, pairings.acceptCode("000042"))
        assertNull(pairings.acceptCode("999999"))
        assertNull(pairings.display())
        assertEquals("pairingExpired", assertThrows(Rejected::class.java) { pairings.register(ticket, "c".repeat(40), "host") }.code)
        pairings.open()
        assertNotNull(pairings.acceptCode("000042"))
        pairings.closeWindow()
        assertNull(pairings.display())
    }

    @Test fun registrationStorageFailureStillConsumesTheWindow() {
        val pairings = Pairings(File(temporary.root, "missing/parent/pairs"), { 0L }, { "000042" })
        pairings.open()
        val ticket = pairings.acceptCode("000042")!!
        assertThrows(IOException::class.java) { pairings.register(ticket, "c".repeat(40), "host") }
        assertNull(pairings.acceptCode("000042"))
        assertNull(pairings.display())
        assertEquals("pairingStore", assertThrows(Rejected::class.java) { pairings.open() }.code)
    }
}
