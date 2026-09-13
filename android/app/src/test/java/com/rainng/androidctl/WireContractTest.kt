package com.rainng.androidctl

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class WireContractTest {
    @Test fun deviceProjectionAndTransferMatchTheSharedContract() {
        val fixture = javaClass.getResourceAsStream("/wire-contract.json")!!.bufferedReader().use { JSONObject(it.readText()) }
        val produced = generateContract()
        System.getenv("ANDROIDCTL_CONTRACT_OUTPUT")?.let { exportCandidate(produced, it) }
        compareContract(fixture, produced)
    }

    @Test fun contractComparisonRejectsAnExtraTopLevelSection() {
        val fixture = javaClass.getResourceAsStream("/wire-contract.json")!!.bufferedReader().use { JSONObject(it.readText()) }
        val produced = generateContract().put("extra", JSONObject())
        val failure = assertThrows(AssertionError::class.java) { compareContract(fixture, produced) }
        assertTrue(failure.message.orEmpty().contains("Contract sections differ"))
    }

    private fun generateContract(): JSONObject =
        json(
            "limits" to
                json(
                    "MESSAGE_BYTES" to Limits.MESSAGE_BYTES,
                    "JSON_UTF8_BYTES" to Limits.JSON_UTF8_BYTES,
                    "PAIRING_NAME_UNITS" to Pairings.PAIRING_NAME_UNITS,
                ),
            "queryMethods" to JSONArray(RpcMethods.QUERY_METHODS.sorted()),
        )

    private fun exportCandidate(
        produced: JSONObject,
        path: String,
    ): Nothing {
        File(path).writeText(produced.toString(2))
        throw AssertionError(
            "Contract candidate exported to ${File(path).absolutePath}; the reviewed contract was not validated. " +
                "Unset ANDROIDCTL_CONTRACT_OUTPUT and rerun the tests.",
        )
    }

    private fun compareContract(
        fixture: JSONObject,
        produced: JSONObject,
    ) {
        assertEquals("Contract sections differ from the reviewed contract", fields(fixture), fields(produced))
        assertEquals(
            "Shared limits differ from the reviewed contract",
            structure(fixture.getJSONObject("limits")),
            structure(produced.getJSONObject("limits")),
        )
        assertEquals(
            "Query methods differ from the reviewed contract",
            structure(fixture.getJSONArray("queryMethods")),
            structure(produced.getJSONArray("queryMethods")),
        )
    }

    private fun fields(value: JSONObject): Set<String> = value.keys().asSequence().toSet()
}
