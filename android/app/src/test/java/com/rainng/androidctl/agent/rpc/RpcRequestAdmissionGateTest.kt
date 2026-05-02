package com.rainng.androidctl.agent.rpc

import fi.iki.elonen.NanoHTTPD
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class RpcRequestAdmissionGateTest {
    @Test
    fun enterOrRejectReturnsStoppingResponseAfterBeginShutdown() {
        val gate = RpcRequestAdmissionGate()
        gate.beginShutdown()

        val response =
            gate.enterOrReject {
                NanoHTTPD.newFixedLengthResponse(
                    NanoHTTPD.Response.Status.OK,
                    "application/json",
                    """{"ok":false}""",
                )
            }

        assertTrue(response != null)
        assertEquals(NanoHTTPD.Response.Status.OK, response?.status)
    }

    @Test
    fun enterOrRejectAdmitsBeforeShutdownAndLeaveAllowsQuiescence() {
        val gate = RpcRequestAdmissionGate()

        val response = gate.enterOrReject { error("should not reject") }
        assertNull(response)

        gate.leave()
        assertTrue(gate.awaitQuiescence(10L))
    }
}
