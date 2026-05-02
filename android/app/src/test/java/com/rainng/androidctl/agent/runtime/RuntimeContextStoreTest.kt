package com.rainng.androidctl.agent.runtime

import android.accessibilityservice.AccessibilityService
import android.content.Context
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test
import org.mockito.Mockito.mock
import org.mockito.Mockito.`when`

class RuntimeContextStoreTest {
    @Test
    fun storesApplicationContextAccessibilityServiceAndReset() {
        val store = RuntimeContextStore()
        val context = mock(Context::class.java)
        val applicationContext = mock(Context::class.java)
        val service = mock(AccessibilityService::class.java)
        `when`(context.applicationContext).thenReturn(applicationContext)

        store.setApplicationContext(context.applicationContext)
        store.registerAccessibilityService(service)

        assertSame(applicationContext, store.applicationContext())
        with(store.currentAccessibilityAttachmentHandle()) {
            assertSame(service, this.service)
            assertEquals(1L, generation)
            assertFalse(revoked)
        }

        store.reset()

        assertNull(store.applicationContext())
        with(store.currentAccessibilityAttachmentHandle()) {
            assertNull(this.service)
            assertEquals(0L, generation)
            assertFalse(revoked)
        }
    }

    @Test
    fun invalidateAccessibilityAttachmentHandleRevokesCurrentServiceAndAdvancesGeneration() {
        val activationTimes = ArrayDeque(listOf(100L, 200L))
        val store = RuntimeContextStore { activationTimes.removeFirst() }
        val service = mock(AccessibilityService::class.java)

        store.registerAccessibilityService(service)
        val connectedHandle = store.currentAccessibilityAttachmentHandle()

        store.invalidateAccessibilityAttachmentHandle()

        val invalidatedHandle = store.currentAccessibilityAttachmentHandle()
        assertSame(service, connectedHandle.service)
        assertFalse(connectedHandle.revoked)
        assertEquals(1L, connectedHandle.generation)
        assertEquals(100L, connectedHandle.activationUptimeMillis)
        assertNull(invalidatedHandle.service)
        assertTrue(invalidatedHandle.revoked)
        assertEquals(2L, invalidatedHandle.generation)
        assertEquals(200L, invalidatedHandle.activationUptimeMillis)
    }

    @Test
    fun registerAccessibilityServiceDoesNotFutureFenceDifferentServiceReattachWhenClockDoesNotMove() {
        val store = RuntimeContextStore { 100L }
        val firstService = mock(AccessibilityService::class.java)
        val secondService = mock(AccessibilityService::class.java)

        store.registerAccessibilityService(firstService)
        store.unregisterAccessibilityService()
        store.registerAccessibilityService(secondService)

        val reattachedHandle = store.currentAccessibilityAttachmentHandle()
        assertSame(secondService, reattachedHandle.service)
        assertFalse(reattachedHandle.revoked)
        assertEquals(3L, reattachedHandle.generation)
        assertEquals(100L, reattachedHandle.activationUptimeMillis)
    }

    @Test
    fun registerAccessibilityServiceAppliesSingleFutureFenceForSameServiceReattachWhenClockDoesNotMove() {
        val store = RuntimeContextStore { 100L }
        val service = mock(AccessibilityService::class.java)

        store.registerAccessibilityService(service)
        store.unregisterAccessibilityService()
        store.registerAccessibilityService(service)

        val reattachedHandle = store.currentAccessibilityAttachmentHandle()
        assertSame(service, reattachedHandle.service)
        assertFalse(reattachedHandle.revoked)
        assertEquals(3L, reattachedHandle.generation)
        assertEquals(101L, reattachedHandle.activationUptimeMillis)
    }
}
