package com.rainng.androidctl.agent.runtime

import android.accessibilityservice.AccessibilityService
import android.content.Context
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.mockito.Mockito.mock

class RuntimeAccessTest {
    @Before
    fun setUp() {
        AgentRuntimeBridge.resetForTest()
    }

    @After
    fun tearDown() {
        AgentRuntimeBridge.resetForTest()
    }

    @Test
    fun graphRuntimeAccessComposesFactsWithContextAndAttachmentProviders() {
        val appContext = mock(Context::class.java)
        val service = mock(AccessibilityService::class.java)
        val access =
            GraphRuntimeAccess(
                runtimeFactsProvider = {
                    RuntimeFacts(
                        serverPhase = ServerPhase.RUNNING,
                        auth =
                            AuthFacts(
                                currentToken = "token-1",
                                blocked = false,
                                blockedMessage = null,
                                available = true,
                            ),
                        accessibilityEnabled = true,
                        accessibilityAttached = true,
                    )
                },
                applicationContextProvider = { appContext },
                attachmentHandleProvider =
                    AccessibilityAttachmentHandleProvider {
                        AccessibilityAttachmentHandleSnapshot(
                            service = service,
                            generation = 7L,
                            revoked = false,
                        )
                    },
            )

        val readiness = access.readiness()
        val attachmentHandle = access.currentAccessibilityAttachmentHandle()

        assertTrue(readiness.ready)
        assertEquals("token-1", access.currentDeviceToken())
        assertSame(appContext, access.applicationContext())
        assertEquals(7L, attachmentHandle.generation)
        assertEquals(false, attachmentHandle.revoked)
        assertSame(service, attachmentHandle.service)
        assertSame(service, access.currentAccessibilityService())
    }

    @Test
    fun graphRuntimeAccessRetainsRevokedSnapshotButMasksServiceHandle() {
        val service = mock(AccessibilityService::class.java)
        val access =
            GraphRuntimeAccess(
                runtimeFactsProvider = { RuntimeFacts() },
                applicationContextProvider = { null },
                attachmentHandleProvider =
                    AccessibilityAttachmentHandleProvider {
                        AccessibilityAttachmentHandleSnapshot(
                            service = service,
                            generation = 11L,
                            revoked = true,
                        )
                    },
            )

        val attachmentHandle = access.currentAccessibilityAttachmentHandle()

        assertEquals(11L, attachmentHandle.generation)
        assertEquals(true, attachmentHandle.revoked)
        assertSame(service, attachmentHandle.service)
        assertNull(access.currentAccessibilityService())
    }
}
