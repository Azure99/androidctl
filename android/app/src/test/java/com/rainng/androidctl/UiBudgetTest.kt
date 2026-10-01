package com.rainng.androidctl

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class UiBudgetTest {
    @Test fun sampleAdmissionHonorsBothTheSettleAndDeliveryCutoffs() {
        var now = 1000L
        val budget = UiBudget(6000) { now }
        assertTrue(budget.remaining() < 5000)
        assertTrue(budget.canSample(200, until = 1200))
        assertFalse(budget.canSample(201, until = 1200))
        budget.reserveDelivery(Limits.CHUNK_BYTES)
        val smallDeliveryDeadline = budget.deadline
        budget.reserveDelivery(2 * Limits.CHUNK_BYTES)
        assertTrue(budget.deadline < smallDeliveryDeadline)
        now = budget.deadline - 100
        assertTrue(budget.canSample(100, budget.deadline))
        assertFalse(budget.canSample(101, until = 9000))
        assertTrue(budget.responseRemaining() > budget.remaining())
        now = budget.deadline + 1
        assertFalse(budget.canSample(0, budget.deadline))
        assertEquals(0L, budget.remaining())
        assertTrue(budget.responseRemaining() > 0)
        assertEquals("timeout", assertThrows(Rejected::class.java) { budget.check() }.code)
    }

    @Test fun handoffTimeIsNotGrantedAgainToUiWorkOrDelivery() {
        var now = 4000L
        val budget = UiBudget(5000) { now }
        assertEquals(800L, budget.remaining())
        assertEquals(800L, budget.responseRemaining())
        budget.reserveDelivery(Limits.CHUNK_BYTES)
        now = 4650L
        assertEquals(0L, budget.remaining())
        assertEquals(150L, budget.responseRemaining())
        assertEquals("timeout", assertThrows(Rejected::class.java) { budget.check() }.code)
        now = 5000L
        assertEquals(0L, budget.responseRemaining())
    }

    @Test fun expiredArrivalCannotCreateANewUiBudget() {
        for (now in listOf(5000L, 5001L)) {
            assertEquals("timeout", assertThrows(Rejected::class.java) { UiBudget(5000) { now } }.code)
        }
    }
}
