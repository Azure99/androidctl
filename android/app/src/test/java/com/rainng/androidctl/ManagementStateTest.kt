package com.rainng.androidctl

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ManagementStateTest {
    @Test fun workerSnapshotRetainsManagementFactsWithoutSharingMutableJson() {
        val source = worker()
        val value = WorkerStatus.fromJson(source)
        assertJson(source, value.toJson())
        assertEquals("0.3.0", value.version)
        assertEquals(2000, value.uid)
        assertEquals("worker", value.workerId)
        assertEquals(SessionStatus("task", "host", 3, 1, 0, 7), value.session)
        assertEquals(listOf("job"), value.helpers.active)
        assertEquals(mapOf("session-expiry" to "failed"), value.diagnostics.maintenance)
        value.toJson().getJSONObject("session").put("leaseId", 999)
        assertEquals(7L, value.session.leaseId)
        assertEquals(value, WorkerStatus.fromJson(worker()))
        source.getJSONObject("session").put("name", "replacement")
        source.getJSONObject("helpers").getJSONArray("active").put("new-job")
        source.getJSONObject("diagnostics").getJSONObject("maintenance").put("new-cleanup", "failed")
        assertJson(worker(), value.toJson())
        assertNotEquals(value, WorkerStatus.fromJson(source))
    }

    @Test fun idleAndHelperFailureRetainNullAndAbsentFields() {
        val source =
            worker().apply {
                getJSONObject("session").put("name", JSONObject.NULL).put("leaseId", JSONObject.NULL).remove("pairingName")
                getJSONObject("helpers").remove("pids")
                getJSONObject("helpers").put("error", JSONObject.NULL)
            }
        val result = WorkerStatus.fromJson(source).toJson()
        assertJson(source, result)
        assertTrue(result.getJSONObject("session").has("leaseId"))
        assertTrue(result.getJSONObject("session").isNull("leaseId"))
        assertFalse(result.getJSONObject("helpers").has("pids"))
        assertTrue(result.getJSONObject("helpers").has("error"))
        assertTrue(result.getJSONObject("helpers").isNull("error"))
        assertFalse(result.getJSONObject("session").has("pairingName"))
    }

    @Test fun managementDumpPreservesLifecycleAndNetworkShapes() {
        val stopped =
            JSONObject(
                """{"gateway":{"state":"stopped","error":null,"port":null,"connections":null,
            "clients":null,"activeCalls":null,"activeQueries":null,"queuedCalls":null,
            "queuedQueries":null,"tlsActive":null,"tlsQueued":null},
            "executor":null,"error":null,"deviceId":null,"pairings":null,"pendingRevocations":[]}""",
            )
        assertJson(stopped, ManagementState().toJson(null))
        val starting = ManagementState(gateway = GatewayStatus(GatewayState.STARTING)).toJson(null)
        assertTrue(starting.getJSONObject("gateway").isNull("error"))
        assertTrue(starting.has("executor"))
        assertTrue(starting.isNull("executor"))
        assertEquals("starting", starting.getJSONObject("gateway").getString("state"))
        val stopping = GatewayStatus(GatewayState.STOPPING).toJson()
        assertEquals("stopping", stopping.getString("state"))
        assertTrue(stopping.has("error"))
        assertTrue(stopping.isNull("error"))
        val failed = stopped.getJSONObject("gateway").put("state", "failed").put("error", "listener failed")
        assertJson(failed, GatewayStatus(GatewayState.FAILED, "listener failed").toJson())
        val network = GatewayStatus(GatewayState.LISTENING, traffic = GatewayTraffic(2, 2, 0, 1, 0, 0, 0, 0))
        val snapshot =
            ManagementState(
                network,
                WorkerStatus.fromJson(worker()),
                deviceId = "device",
                pendingRevocations = listOf("old"),
            )
        val dump = snapshot.toJson(listOf(PairingSummary("pair", "host", 123)))
        assertEquals("listening", dump.getJSONObject("gateway").getString("state"))
        assertTrue(dump.getJSONObject("gateway").isNull("error"))
        assertEquals(Limits.PORT, dump.getJSONObject("gateway").getInt("port"))
        assertEquals(1, dump.getJSONObject("gateway").getInt("activeQueries"))
        assertEquals("pair", dump.getJSONArray("pairings").getJSONObject(0).getString("id"))
        assertEquals("old", dump.getJSONArray("pendingRevocations").getString(0))
        val stoppedNetwork = snapshot.copy(gateway = network.copy(state = GatewayState.STOPPED))
        val duringRenewal = stoppedNetwork.toJson(emptyList())
        assertEquals("stopped", duringRenewal.getJSONObject("gateway").getString("state"))
        assertJson(dump.getJSONObject("executor"), duringRenewal.getJSONObject("executor"))
        assertEquals("device", duringRenewal.getString("deviceId"))
        assertEquals(0, duringRenewal.getJSONArray("pairings").length())
    }

    private fun assertJson(
        expected: JSONObject,
        actual: JSONObject,
    ) = assertEquals(structure(expected), structure(actual))

    private fun worker(): JSONObject =
        JSONObject(
            """
            {"version":"0.3.0","uid":2000,"workerId":"worker","worker":"ready",
             "session":{"name":"task","pairingName":"host","idleSeconds":3,"shell":1,"file":0,"leaseId":7},
             "helpers":{"pids":[1234],"active":["job"]},
             "diagnostics":{"maintenance":{"session-expiry":"failed"}}}
            """.trimIndent(),
        )
}
