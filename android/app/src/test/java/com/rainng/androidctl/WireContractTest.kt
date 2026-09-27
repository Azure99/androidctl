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
import java.io.IOException
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

    @Test fun namedSamplesIgnoreOnlyTheirOuterOrder() {
        val first = json("name" to "first", "nodes" to JSONArray(listOf(1, 2)))
        val second = json("name" to "second", "nodes" to JSONArray(listOf(3)))
        val reviewed = JSONArray(listOf(first, second))
        compareNamedSamples("actionResults", reviewed, JSONArray(listOf(second, first)))

        for (changed in listOf(
            JSONArray(listOf(first)),
            JSONArray(listOf(first, second, json("name" to "extra"))),
            JSONArray(listOf(copy(first).put("nodes", JSONArray(listOf(2, 1))), second)),
        )) {
            val failure = assertThrows(AssertionError::class.java) { compareNamedSamples("actionResults", reviewed, changed) }
            assertTrue(failure.message.orEmpty().contains("actionResults"))
        }
        val duplicates = JSONArray(listOf(first, first, second))
        for ((saved, actual) in listOf(reviewed to duplicates, duplicates to reviewed)) {
            val failure = assertThrows(AssertionError::class.java) { compareNamedSamples("actionResults", saved, actual) }
            assertTrue(failure.message.orEmpty().contains("duplicate name: first"))
        }
        val forms = JSONArray(listOf(json("input" to "&amp;"), json("input" to "&amp;")))
        val failure = assertThrows(AssertionError::class.java) { indexSamples("entityForms", forms, "input") }
        assertTrue(failure.message.orEmpty().contains("duplicate input: &amp;"))
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
            "fileFailures" to fileFailures(),
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
        compareNamedSamples("fileFailures", fixture.getJSONArray("fileFailures"), produced.getJSONArray("fileFailures"))
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

    private fun fileFailures(): JSONArray {
        val beforeOpen = fileFailure("pull", "f1", false, false, false, Rejected("sessionChanged", "Lease changed"))
        val readFailed = fileFailure("pull", "f2", true, true, false, IOException("Read interrupted"))
        val publicationUnknown = fileFailure("push", "f3", true, true, true, IOException("Publication interrupted"))
        val rejected = fileFailure("push", "f4", true, true, true, Rejected("publishFailed", "Target exists"))
        return JSONArray(
            listOf(
                json("name" to "before-open", "expect" to Outcome.NOT_SENT, "response" to beforeOpen),
                json("name" to "read-failed", "expect" to Outcome.FAILED, "response" to readFailed),
                json("name" to "publication-unknown", "expect" to Outcome.UNKNOWN, "response" to publicationUnknown),
                json("name" to "publication-rejected", "expect" to Outcome.FAILED, "response" to rejected),
            ),
        )
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

    private fun indexSamples(
        section: String,
        samples: JSONArray,
        key: String = "name",
    ): Map<String, JSONObject> {
        val names = mutableSetOf<String>()
        val objects = samples.objects()
        for (sample in objects) {
            val name = sample.getString(key)
            assertTrue("$section contains duplicate $key: $name", names.add(name))
        }
        return objects.associateBy { it.getString(key) }
    }

    private fun compareNamedSamples(
        section: String,
        reviewed: JSONArray,
        produced: JSONArray,
    ) {
        val saved = indexSamples("$section reviewed", reviewed)
        val actual = indexSamples("$section produced", produced)
        assertEquals("$section sample names differ from the reviewed contract", saved.keys, actual.keys)
        for ((name, sample) in actual) {
            assertEquals("$section/$name differs from the reviewed contract", structure(saved.getValue(name)), structure(sample))
        }
    }

    private fun fields(value: JSONObject): Set<String> = value.keys().asSequence().toSet()
}
