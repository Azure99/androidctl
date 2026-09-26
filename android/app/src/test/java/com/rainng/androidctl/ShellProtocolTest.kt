package com.rainng.androidctl

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.DataInputStream
import java.io.EOFException
import java.io.IOException
import java.util.Base64

class ShellProtocolTest {
    private fun event(): JSONObject =
        json(
            "created" to true,
            "cancelled" to false,
            "reason" to "finished",
            "domainEmpty" to true,
            "rootDone" to true,
            "exitCode" to 0,
            "signal" to 0,
            "signalOK" to 0,
            "signalFailed" to 0,
            "cleanupUnknown" to false,
            "startupUnknown" to false,
            "startupStage" to 0,
            "startupErrno" to 0,
        )

    @Test fun cancellationSeparatesSignalFailureFromPendingReap() {
        val pending =
            event()
                .put("cancelled", true)
                .put("reason", "cancelled")
                .put("domainEmpty", false)
                .put("exitCode", -1)
                .put("signal", 15)
        val submitted = foreground(pending).view(ended = false)
        assertEquals("failed", submitted.getString("result"))
        assertFalse(submitted.has("exit"))
        assertEquals("cancelled; cleanup pending", submitted.getString("reason"))
        val failedSignal = foreground(pending.put("signalFailed", 1))
        assertEquals(3, failedSignal.view(ended = false).getInt("exit"))
        failedSignal.accept(json("event" to "helperEnd", "cleanupUnknown" to false, "signalFailed" to 0))
        failedSignal.ended()
        assertEquals(3, failedSignal.view(ended = true).getInt("exit"))
        assertEquals("UNKNOWN", foreground(pending.put("cleanupUnknown", true)).view(ended = false).getString("result"))
    }

    @Test fun exitZeroWithoutKnownDomainEndIsNotCompletion() {
        assertEquals("done", foreground(event()).view(ended = false).getString("result"))
        val nonzeroExit = foreground(event().put("exitCode", 7)).view(ended = false)
        assertEquals("failed", nonzeroExit.getString("result"))
        assertEquals("exit=7", nonzeroExit.getString("reason"))
        assertEquals("UNKNOWN", foreground(event().put("domainEmpty", false)).view(ended = false).getString("result"))
        assertEquals("UNKNOWN", foreground(event().put("startupUnknown", true)).view(ended = false).getString("result"))
    }

    @Test fun signalTerminationReportsSignalWithoutChangingExecution() {
        val execution = event().put("exitCode", -1).put("signal", 15)
        val result = foreground(execution).view(ended = false)
        assertEquals("failed", result.getString("result"))
        assertEquals("signal=15", result.getString("reason"))
        assertEquals(execution.toString(), result.getJSONObject("data").getJSONObject("execution").toString())
    }

    @Test fun signalDoesNotOverrideUnknownExecutionReason() {
        val execution = event().put("exitCode", -1).put("signal", 15).put("domainEmpty", false)
        val result = foreground(execution).view(ended = false)
        assertEquals("UNKNOWN", result.getString("result"))
        assertEquals("Execution or cleanup could not be confirmed", result.getString("reason"))
    }

    @Test fun lateCleanupChangesTheReasonWithoutChangingExecution() {
        for (unknown in listOf(false, true)) {
            val protocol = foreground(event().put("cancelled", true).put("reason", "cancelled").put("domainEmpty", false))
            val first = protocol.view(ended = false)
            protocol.accept(json("event" to "helperEnd", "cleanupUnknown" to unknown, "signalFailed" to 0))
            protocol.ended()
            val result = protocol.view(ended = true)
            assertEquals("cancelled; cleanup " + if (unknown) "unknown" else "finished", result.getString("reason"))
            assertEquals("failed", result.getString("result"))
            assertEquals(
                first.getJSONObject("data").getJSONObject("execution").toString(),
                result.getJSONObject("data").getJSONObject("execution").toString(),
            )
            assertEquals("cancelled; cleanup pending", first.getString("reason"))
        }
    }

    @Test fun finalCleanupDiagnosticsDoNotRewriteTheForegroundFact() {
        for (unknown in listOf(false, true)) {
            for (signalFailed in listOf(0, 1)) {
                val protocol = ShellProtocol("j", OutputStore { 0L }) { }
                protocol.sent = true
                val execution = event().put("event", "foreground").put("exitCode", 7)
                protocol.accept(execution)
                val first = protocol.view(ended = false)
                assertFalse(first.has("exit"))
                val report =
                    json("event" to "helperEnd", "cleanupUnknown" to unknown, "signalFailed" to signalFailed)
                protocol.accept(report)
                protocol.ended()
                val final = protocol.view(ended = true)
                assertEquals("failed", final.getString("result"))
                assertEquals(unknown || signalFailed > 0, final.has("exit"))
                if (final.has("exit")) assertEquals(3, final.getInt("exit"))
                val data = final.getJSONObject("data")
                assertEquals(if (unknown) "unknown" else "finished", data.getString("cleanup"))
                assertEquals(execution.toString(), data.getJSONObject("execution").toString())
                assertEquals(report.toString(), data.getJSONObject("cleanupReport").toString())
                assertFalse(first.has("exit"))
            }
        }
    }

    @Test fun contradictoryForegroundEventsKeepTheFirstExecutionAndReleaseForegroundOnce() {
        var released = 0
        val protocol = ShellProtocol("j", OutputStore { 0L }) { released++ }
        protocol.sent = true
        val execution = event().put("event", "foreground")
        protocol.accept(execution)
        val first = protocol.view(ended = false)
        assertEquals(1, released)
        assertEquals("pending", first.getJSONObject("data").getString("cleanup"))
        assertTrue(first.getJSONObject("data").getBoolean("helperAlive"))

        protocol.accept(event().put("event", "foreground").put("exitCode", 7))
        protocol.accept(json("event" to "helperEnd", "cleanupUnknown" to false, "signalFailed" to 0))
        protocol.ended()
        val result = protocol.view(ended = true)
        assertEquals("done", result.getString("result"))
        assertEquals(structure(execution), structure(result.getJSONObject("data").getJSONObject("execution")))
        assertEquals("finished", result.getJSONObject("data").getString("cleanup"))
        assertEquals("pending", first.getJSONObject("data").getString("cleanup"))
        assertEquals(1, released)
    }

    private fun frame(
        kind: Char,
        bytes: ByteArray,
    ): ByteArray =
        "ACF1".toByteArray() +
            byteArrayOf(kind.code.toByte(), (bytes.size shr 16).toByte(), (bytes.size shr 8).toByte(), bytes.size.toByte()) +
            bytes

    private fun input(bytes: ByteArray): DataInputStream = DataInputStream(ByteArrayInputStream(bytes))

    private fun ShellProtocol.accept(event: JSONObject): Boolean = accept(ShellFrame('S', event.toString().toByteArray()))

    private fun foreground(event: JSONObject): ShellProtocol =
        ShellProtocol("j", OutputStore { 0L }) { }.apply {
            sent = true
            accept(event.put("event", "foreground"))
        }

    private fun summary(size: Int = 1): JSONObject =
        json(
            "event" to "outputSummary",
            "saved" to JSONArray(listOf(size, 1)),
            "read" to JSONArray(listOf(size, 1)),
            "cut" to JSONArray(listOf(false, false)),
        )

    @Test fun frameReaderDistinguishesCleanEofFromInvalidOrTruncatedFrames() {
        val bytes = byteArrayOf(0, -1, 7)
        val stream = input(frame('O', bytes))
        val received = requireNotNull(readShellFrame(stream))
        assertEquals('O', received.kind)
        assertArrayEquals(bytes, received.payload)
        assertNull(readShellFrame(stream))
        val valid = frame('O', bytes)
        for (broken in listOf(valid.copyOf(3), valid.copyOf(valid.size - 1))) {
            assertThrows(EOFException::class.java) { readShellFrame(input(broken)) }
        }
        for (invalid in listOf(valid.copyOf().apply { this[0] = 0 }, frame('O', ByteArray(2041)))) {
            val error = assertThrows(IOException::class.java) { readShellFrame(input(invalid)) }
            assertEquals(IOException::class.java, error.javaClass)
        }
        val protocol = ShellProtocol("j", OutputStore { 0L }) { }
        assertThrows(IOException::class.java) { protocol.accept(requireNotNull(readShellFrame(input(frame('X', byteArrayOf()))))) }
    }

    @Test fun fullHelperStreamKeepsForegroundOutputAndCleanupAsSeparateFacts() {
        var now = 0L
        val outputs = OutputStore { now }
        var finished = 0
        val protocol = ShellProtocol("j", outputs) { finished++ }
        assertTrue(protocol.accept(json("event" to "ready", "pid" to 123, "slot" to 1)))
        protocol.sent = true
        protocol.accept(event().put("event", "foreground"))
        assertEquals(1, finished)
        val foreground = protocol.view(ended = false)
        assertEquals("done", foreground.getString("result"))
        assertTrue(foreground.getJSONObject("data").getBoolean("helperAlive"))
        assertEquals("pending", foreground.getJSONObject("data").getString("cleanup"))
        val stream =
            input(
                frame('S', summary().toString().toByteArray()) + frame('O', byteArrayOf(65)) + frame('E', byteArrayOf(66)) +
                    frame('S', json("event" to "outputEnd").toString().toByteArray()),
            )
        while (true) {
            val frame = readShellFrame(stream) ?: break
            protocol.accept(frame)
        }
        protocol.accept(json("event" to "helperEnd", "cleanupUnknown" to false, "signalFailed" to 0))
        protocol.ended()
        val result = protocol.view(ended = true)
        assertEquals("done", result.getString("result"))
        assertEquals("finished", result.getJSONObject("data").getString("cleanup"))
        assertFalse(result.getJSONObject("data").getBoolean("helperAlive"))
        assertEquals(1, finished)
        assertArrayEquals(
            byteArrayOf(65),
            Base64.getDecoder().decode(outputs.read("j", json("stream" to "stdout", "offset" to 0)).getString("bytes")),
        )
        assertArrayEquals(
            byteArrayOf(66),
            Base64.getDecoder().decode(outputs.read("j", json("stream" to "stderr", "offset" to 0)).getString("bytes")),
        )
        now = 120001L
        val expired = protocol.view(ended = true)
        assertEquals("expired", expired.getJSONObject("data").getJSONObject("output").getString("state"))
    }

    @Test fun outputRequiresOneSummaryMatchingItsBoundedStreams() {
        fun protocol() = ShellProtocol("j", OutputStore { 0L }) { }
        assertThrows(IOException::class.java) { protocol().accept(ShellFrame('O', byteArrayOf(1))) }
        assertThrows(IOException::class.java) { protocol().accept(json("event" to "outputEnd")) }
        val duplicate = protocol()
        duplicate.accept(summary())
        assertThrows(IOException::class.java) { duplicate.accept(summary()) }
        assertThrows(IOException::class.java) { duplicate.accept(json("event" to "outputEnd")) }
        val bounded = protocol()
        bounded.accept(summary(131072))
        repeat(64) { bounded.accept(ShellFrame('O', ByteArray(2040))) }
        bounded.accept(ShellFrame('O', ByteArray(512)))
        assertThrows(IOException::class.java) { bounded.accept(ShellFrame('O', byteArrayOf(1))) }
    }

    @Test fun duplicateOutputEndFailsWithoutReplacingCapturedOutputOrExecution() {
        val outputs = OutputStore { 0L }
        val protocol = ShellProtocol("j", outputs) { }
        protocol.sent = true
        val execution = event().put("event", "foreground")
        protocol.accept(execution)
        protocol.accept(summary())
        protocol.accept(ShellFrame('O', byteArrayOf(65)))
        protocol.accept(ShellFrame('E', byteArrayOf(66)))
        protocol.accept(json("event" to "outputEnd"))
        val captured = requireNotNull(outputs.describe("j"))
        val error = assertThrows(IOException::class.java) { protocol.accept(json("event" to "outputEnd")) }
        protocol.failed(error)
        protocol.ended()
        val result = protocol.view(ended = true)
        assertEquals("done", result.getString("result"))
        assertEquals(3, result.getInt("exit"))
        val data = result.getJSONObject("data")
        assertEquals(structure(execution), structure(data.getJSONObject("execution")))
        assertEquals("unknown", data.getString("cleanup"))
        assertEquals(structure(captured), structure(data.getJSONObject("output")))
        for ((name, expected) in listOf("stdout" to byteArrayOf(65), "stderr" to byteArrayOf(66))) {
            assertArrayEquals(
                expected,
                Base64.getDecoder().decode(outputs.read("j", json("stream" to name, "offset" to 0)).getString("bytes")),
            )
        }
    }

    @Test fun invalidReadOrCutFailsAtCaptureAndPreservesKnownExecution() {
        for (field in listOf("read", "cut")) {
            for (known in listOf(false, true)) {
                val outputs = OutputStore { 0L }
                var finished = 0
                val protocol = ShellProtocol("j", outputs) { finished++ }
                protocol.sent = true
                val execution = event().put("event", "foreground")
                if (known) protocol.accept(execution)
                protocol.accept(summary().apply { getJSONArray(field).put(1, "invalid") })
                protocol.accept(ShellFrame('O', byteArrayOf(65)))
                protocol.accept(ShellFrame('E', byteArrayOf(66)))
                val error = assertThrows(org.json.JSONException::class.java) { protocol.accept(json("event" to "outputEnd")) }
                assertNull(outputs.describe("j"))
                protocol.failed(error)
                protocol.ended()
                val result = protocol.view(ended = true)
                assertEquals(if (known) "done" else "UNKNOWN", result.getString("result"))
                assertEquals(3, result.getInt("exit"))
                assertEquals(1, finished)
                val data = result.getJSONObject("data")
                if (known) assertEquals(structure(execution), structure(data.getJSONObject("execution")))
                assertEquals("unknown", data.getString("cleanup"))
                assertEquals("unavailable", data.getJSONObject("output").getString("state"))
            }
        }
    }

    @Test fun lateFrameFailureCannotReplaceKnownExecutionOrClaimCleanupFinished() {
        var finished = 0
        val protocol = ShellProtocol("j", OutputStore { 0L }) { finished++ }
        protocol.sent = true
        protocol.accept(event().put("event", "foreground"))
        protocol.failed(EOFException("truncated frame"))
        protocol.cleanupFailed(IOException())
        protocol.cleanupFailed(EOFException("close failed"))
        protocol.ended()
        val result = protocol.view(ended = true)
        assertEquals("done", result.getString("result"))
        assertEquals(3, result.getInt("exit"))
        assertEquals("unknown", result.getJSONObject("data").getString("cleanup"))
        assertEquals(
            "java.io.EOFException: truncated frame; java.io.IOException; java.io.EOFException: close failed",
            result.getJSONObject("data").getString("cleanupError"),
        )
        assertEquals(1, finished)
    }

    @Test fun cancellationFailureWithoutMessageStillReportsUnknownCleanup() {
        val protocol = foreground(event())
        protocol.cleanupFailed(IOException())
        protocol.accept(json("event" to "helperEnd", "cleanupUnknown" to false, "signalFailed" to 0))
        protocol.ended()
        val result = protocol.view(ended = true)
        assertEquals("done", result.getString("result"))
        assertEquals(3, result.getInt("exit"))
        assertEquals("unknown", result.getJSONObject("data").getString("cleanup"))
        assertEquals("java.io.IOException", result.getJSONObject("data").getString("cleanupError"))
    }

    @Test fun protocolFailureBeforeDispatchDoesNotInventCleanupFailure() {
        val protocol = ShellProtocol("j", OutputStore { 0L }) { }
        protocol.failed(IOException())
        protocol.ended()
        val result = protocol.view(ended = true)
        assertEquals("not sent", result.getString("result"))
        assertEquals("IOException", result.getString("reason"))
        assertFalse(result.has("exit"))
        assertFalse(result.getJSONObject("data").has("cleanupError"))
        assertEquals("finished", result.getJSONObject("data").getString("cleanup"))
    }

    @Test fun earlyRejectionAndObserverLossDoNotRequireFullOutputTail() {
        for (sent in listOf(false, true)) {
            val protocol = ShellProtocol("j", OutputStore { 0L }) { }
            protocol.sent = sent
            protocol.accept(json("event" to "rejected", "reason" to "lease ended"))
            protocol.ended()
            val result = protocol.view(ended = true)
            assertEquals(if (sent) "UNKNOWN" else "not sent", result.getString("result"))
            assertEquals(if (sent) "unknown" else "finished", result.getJSONObject("data").getString("cleanup"))
            assertEquals("unavailable", result.getJSONObject("data").getJSONObject("output").getString("state"))
        }
    }

    @Test fun readyRetainsSupervisorAndSignalDiagnosticsRetainDecodedCommand() {
        val protocol = ShellProtocol("j", OutputStore { 0L }) { }
        val ready = json("event" to "ready", "pid" to 123, "slot" to 1)
        assertTrue(protocol.accept(ready))
        assertEquals(structure(ready), structure(protocol.view(ended = false).getJSONObject("data").getJSONObject("helper")))
        protocol.sent = true
        assertThrows(IOException::class.java) { protocol.accept(json("event" to "ready", "pid" to 123, "slot" to 1)) }
        protocol.accept(json("event" to "signalError", "commandHex" to "7368002d63", "errno" to 1))
        protocol.accept(event().put("event", "foreground").put("exitCode", 7))
        val result = protocol.view(ended = false)
        assertEquals("failed", result.getString("result"))
        assertEquals(3, result.getInt("exit"))
        val signal = result.getJSONObject("data").getJSONArray("signalErrors").getJSONObject(0)
        assertEquals("sh -c", signal.getString("command"))
        assertFalse(signal.has("commandHex"))
    }
}
