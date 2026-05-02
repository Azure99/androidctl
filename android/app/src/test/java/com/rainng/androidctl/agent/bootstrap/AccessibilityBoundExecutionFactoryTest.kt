package com.rainng.androidctl.agent.bootstrap

import android.accessibilityservice.AccessibilityService
import com.rainng.androidctl.agent.errors.DeviceRpcException
import com.rainng.androidctl.agent.errors.RpcErrorCode
import com.rainng.androidctl.agent.rpc.RpcEnvironment
import com.rainng.androidctl.agent.runtime.AccessibilityAttachmentHandleSnapshot
import com.rainng.androidctl.agent.runtime.RuntimeAccess
import com.rainng.androidctl.agent.runtime.RuntimeReadiness
import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
import org.junit.Assert.fail
import org.junit.Test
import org.mockito.Mockito.mock

class AccessibilityBoundExecutionFactoryTest {
    @Test
    fun bindCapturesAttachmentAtBindingTime() {
        val firstService = mock(AccessibilityService::class.java)
        val attachmentHandle =
            AccessibilityAttachmentHandleSnapshot(
                service = firstService,
                generation = 1L,
                revoked = false,
            )

        val boundCall =
            AccessibilityBoundExecutionFactory(
                RpcEnvironment(
                    runtimeAccess =
                        runtimeAccess {
                            attachmentHandle
                        },
                ),
            ).bind { service -> service }

        assertSame(firstService, boundCall())
    }

    @Test
    fun bindRejectsReattachedHandleAtExecutionTime() {
        val firstService = mock(AccessibilityService::class.java)
        val secondService = mock(AccessibilityService::class.java)
        var attachmentHandle =
            AccessibilityAttachmentHandleSnapshot(
                service = firstService,
                generation = 1L,
                revoked = false,
            )

        val boundCall =
            AccessibilityBoundExecutionFactory(
                RpcEnvironment(
                    runtimeAccess =
                        runtimeAccess {
                            attachmentHandle
                        },
                ),
            ).bind { service -> service }

        attachmentHandle =
            AccessibilityAttachmentHandleSnapshot(
                service = secondService,
                generation = 2L,
                revoked = false,
            )

        assertRuntimeNotReady {
            boundCall()
        }
    }

    @Test
    fun bindRejectsRevokedHandleAtExecutionTime() {
        val firstService = mock(AccessibilityService::class.java)
        var attachmentHandle =
            AccessibilityAttachmentHandleSnapshot(
                service = firstService,
                generation = 1L,
                revoked = false,
            )

        val boundCall =
            AccessibilityBoundExecutionFactory(
                RpcEnvironment(
                    runtimeAccess =
                        runtimeAccess {
                            attachmentHandle
                        },
                ),
            ).bind { service -> service }

        attachmentHandle =
            AccessibilityAttachmentHandleSnapshot(
                service = null,
                generation = 2L,
                revoked = true,
            )

        assertRuntimeNotReady {
            boundCall()
        }
    }

    @Test
    fun bindRejectsClearedServiceAtExecutionTime() {
        val firstService = mock(AccessibilityService::class.java)
        var attachmentHandle =
            AccessibilityAttachmentHandleSnapshot(
                service = firstService,
                generation = 1L,
                revoked = false,
            )

        val boundCall =
            AccessibilityBoundExecutionFactory(
                RpcEnvironment(
                    runtimeAccess =
                        runtimeAccess {
                            attachmentHandle
                        },
                ),
            ).bind { service -> service }

        attachmentHandle =
            AccessibilityAttachmentHandleSnapshot(
                service = null,
                generation = 1L,
                revoked = false,
            )

        assertRuntimeNotReady {
            boundCall()
        }
    }

    @Test
    fun bindRejectsRevokedAttachmentAtBindTime() {
        assertRuntimeNotReady {
            AccessibilityBoundExecutionFactory(
                RpcEnvironment(
                    runtimeAccess =
                        runtimeAccess {
                            AccessibilityAttachmentHandleSnapshot(
                                generation = 2L,
                                service = null,
                                revoked = true,
                            )
                        },
                ),
            ).bind { "never-called" }
        }
    }

    private fun runtimeAccess(handleProvider: () -> AccessibilityAttachmentHandleSnapshot): RuntimeAccess =
        object : RuntimeAccess {
            override fun readiness(): RuntimeReadiness = RuntimeReadiness(true, true)

            override fun currentDeviceToken(): String = "device-token"

            override fun applicationContext() = null

            override fun currentAccessibilityService(): AccessibilityService? = handleProvider().service

            override fun currentAccessibilityAttachmentHandle(): AccessibilityAttachmentHandleSnapshot = handleProvider()
        }

    private fun assertRuntimeNotReady(block: () -> Unit) {
        try {
            block()
            fail("expected DeviceRpcException")
        } catch (error: DeviceRpcException) {
            assertEquals(RpcErrorCode.RUNTIME_NOT_READY, error.code)
            assertEquals(true, error.retryable)
        }
    }
}
