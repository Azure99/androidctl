package com.rainng.androidctl

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.Base64

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
                    "CHUNK_BYTES" to Limits.CHUNK_BYTES,
                    "OUTPUT_BYTES" to Limits.OUTPUT_BYTES,
                    "MAX_RUN_MS" to Limits.MAX_RUN_MS,
                    "HELPER_PAYLOAD_BYTES" to HELPER_PAYLOAD_BYTES,
                    "PAIRING_NAME_UNITS" to Pairings.PAIRING_NAME_UNITS,
                ),
            "output" to outputChunks(),
            "shellCleanupFailure" to shellCleanupFailure(),
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
        for (section in listOf("output", "shellCleanupFailure")) {
            assertEquals(section, structure(fixture.getJSONObject(section)), structure(produced.getJSONObject(section)))
        }
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

    private fun outputChunks(): JSONObject {
        val store = OutputStore { 0L }
        val job = "jcontract"
        val stdout = ByteArray(Limits.CHUNK_BYTES + 7) { (it % 251).toByte() }
        val stderr = "diagnostic\n".toByteArray()
        store.add(job, CapturedStream(stdout, stdout.size.toLong(), false), CapturedStream(stderr, stderr.size.toLong(), false))
        val chunks = JSONObject()
        for ((stream, bytes) in listOf("stdout" to stdout, "stderr" to stderr)) {
            val responses = JSONArray()
            val received = ByteArrayOutputStream()
            do {
                val data = store.read(job, json("stream" to stream, "offset" to received.size()))
                assertEquals(job, data.getString("job"))
                assertEquals(received.size(), data.getInt("offset"))
                val block = Base64.getDecoder().decode(data.getString("bytes"))
                assertTrue(block.size in 1..Limits.CHUNK_BYTES)
                received.write(block)
                responses.put(outcome("job output", data = data))
            } while (received.size() < bytes.size)
            assertArrayEquals(bytes, received.toByteArray())
            chunks.put(stream, responses)
        }
        return json("job" to job, "metadata" to store.describe(job), "chunks" to chunks)
    }

    private fun shellCleanupFailure(): JSONObject {
        val protocol = ShellProtocol("j-cleanup", OutputStore { 0L }) { }
        protocol.sent = true
        val execution =
            json(
                "event" to "foreground",
                "created" to true,
                "cancelled" to false,
                "reason" to "finished",
                "domainEmpty" to true,
                "rootDone" to true,
                "exitCode" to 7,
                "signal" to 0,
                "signalOK" to 0,
                "signalFailed" to 0,
                "cleanupUnknown" to false,
                "startupUnknown" to false,
                "startupStage" to 0,
                "startupErrno" to 0,
            )
        protocol.accept(ShellFrame('S', execution.toString().toByteArray()))
        val cleanup = json("event" to "helperEnd", "cleanupUnknown" to true, "signalFailed" to 0)
        protocol.accept(ShellFrame('S', cleanup.toString().toByteArray()))
        protocol.ended()
        return protocol.view(ended = true)
    }

    private fun fields(value: JSONObject): Set<String> = value.keys().asSequence().toSet()
}
