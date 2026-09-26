package com.rainng.androidctl

import org.json.JSONArray
import org.json.JSONObject
import java.io.DataInputStream
import java.io.IOException

internal class ShellFrame(
    val kind: Char,
    val payload: ByteArray,
)

internal class ShellProtocol(
    private val id: String,
    private val outputs: OutputStore,
    private val releaseForeground: () -> Unit,
) {
    private val signals = JSONArray()
    private val streams = mutableListOf(BoundedBytes(Limits.OUTPUT_BYTES), BoundedBytes(Limits.OUTPUT_BYTES))
    private var executionResult: JSONObject? = null
    private var helperEnd = false
    private var outputSeen = false
    private var cleanupReport: JSONObject? = null
    private var cleanupError: String? = null
    private var outputSummary: JSONObject? = null
    private var helperIdentity: JSONObject? = null
    var sent = false

    fun accept(frame: ShellFrame): Boolean {
        when (frame.kind) {
            'S' -> return event(JSONObject(String(frame.payload, Charsets.UTF_8)))
            'O', 'E' -> {
                if (outputSummary == null || outputSeen) throw IOException("Output outside capture")
                val stream = streams[if (frame.kind == 'O') 0 else 1]
                stream.write(frame.payload)
            }
            else -> throw IOException("Unknown helper frame kind")
        }
        return false
    }

    private fun event(event: JSONObject): Boolean {
        when (event.getString("event")) {
            "ready" -> {
                if (sent) throw IOException("Duplicate helper ready")
                helperIdentity = event
                return true
            }
            "rejected", "helperError" ->
                recordExecutionResult(
                    outcome(
                        "shell",
                        Outcome.undelivered(sent),
                        event.optString("reason", event.optString("stage", "Helper unavailable")),
                        json("job" to id, "helper" to event),
                    ),
                )
            "foreground" -> recordExecutionResult(outcome("shell", shellState(event), data = json("job" to id, "execution" to event)))
            "signalError" -> signalError(event)
            "outputSummary" -> {
                if (outputSummary != null) throw IOException("Duplicate output summary")
                outputSummary = event
            }
            "outputEnd" -> capture()
            "helperEnd" -> {
                helperEnd = true
                cleanupReport = event
            }
            else -> throw IOException("Unknown helper frame")
        }
        return false
    }

    private fun capture() {
        if (outputSeen) throw IOException("Duplicate output end")
        val details = outputSummary ?: throw IOException("Output summary missing")
        val stdoutBytes = streams[0].toByteArray()
        val stderrBytes = streams[1].toByteArray()
        val saved = details.getJSONArray("saved")
        if (stdoutBytes.size != saved.getInt(0) || stderrBytes.size != saved.getInt(1)) {
            throw IOException("Output size mismatch")
        }
        val read = details.getJSONArray("read")
        val cut = details.getJSONArray("cut")
        val stdout = CapturedStream(bytes = stdoutBytes, read = read.getLong(0), cut = cut.getBoolean(0))
        val stderr = CapturedStream(bytes = stderrBytes, read = read.getLong(1), cut = cut.getBoolean(1))
        outputs.add(id, stdout, stderr)
        streams.clear()
        outputSeen = true
    }

    private fun signalError(event: JSONObject) {
        if (signals.length() >= MAX_SIGNAL_ERRORS) return
        val hex = event.getString("commandHex")
        event.remove("commandHex")
        event.put(
            "command",
            hex
                .chunked(HEX_BYTE)
                .map { it.toInt(HEX_BASE).toByte() }
                .toByteArray()
                .toString(Charsets.UTF_8)
                .replace('\u0000', ' '),
        )
        signals.put(event)
    }

    private fun recordExecutionResult(value: JSONObject) {
        if (executionResult != null) return
        executionResult = value
        releaseForeground()
    }

    fun failed(error: Exception) {
        val reason = message(error)
        if (sent) cleanupFailed(error)
        recordExecutionResult(outcome("shell", Outcome.undelivered(sent), reason, json("job" to id)))
    }

    fun cleanupFailed(error: Exception) {
        cleanupError = listOfNotNull(cleanupError, error.toString()).joinToString("; ")
    }

    fun ended() {
        streams.clear()
        val execution = executionResult?.optJSONObject("data")?.optJSONObject("execution")
        if (!sent || execution?.optBoolean("created", true) == false) helperEnd = true
        if (!helperEnd && sent) cleanupError = cleanupError ?: "Helper ended without its final cleanup report"
        if (executionResult == null) {
            recordExecutionResult(
                outcome(
                    "shell",
                    Outcome.undelivered(sent),
                    cleanupError ?: "Helper ended before returning a result",
                    json("job" to id),
                ),
            )
        }
    }

    @Suppress("CyclomaticComplexMethod") // Execution, cleanup and cached output are independently retained facts.
    fun view(ended: Boolean): JSONObject {
        val value = executionResult?.let(::copy) ?: outcome("shell", Outcome.RUNNING, data = json("job" to id))
        val data = value.getJSONObject("data")
        val cleanupUnknown = cleanupError != null || cleanupReport?.getBoolean("cleanupUnknown") == true
        val cleanup =
            when {
                cleanupUnknown -> "unknown"
                helperEnd -> "finished"
                else -> "pending"
            }
        val execution = data.optJSONObject("execution")
        execution?.let {
            value.put("reason", shellReason(it, value.getString("result"), cleanup))
        }
        if (cleanupUnknown) value.put("exit", UNKNOWN_EXIT)
        data.put("cleanup", cleanup).put("helperAlive", !ended).put("signalErrors", JSONArray(signals.toString()))
        cleanupError?.let { data.put("cleanupError", it) }
        cleanupReport?.let { data.put("cleanupReport", copy(it)) }
        helperIdentity?.let { data.put("helper", copy(it)) }
        val signalFailed =
            signals.length() > 0 ||
                (execution?.getInt("signalFailed") ?: 0) > 0 ||
                (cleanupReport?.getInt("signalFailed") ?: 0) > 0
        if (value.getString("result") == Outcome.FAILED && signalFailed) {
            value.put("exit", UNKNOWN_EXIT)
        }
        data.put(
            "output",
            outputs.describe(id) ?: json(
                "state" to
                    when {
                        outputSeen -> "expired"
                        ended -> "unavailable"
                        else -> "pending"
                    },
            ),
        )
        return value
    }

    private companion object {
        const val MAX_SIGNAL_ERRORS = 64
        const val HEX_BYTE = 2
        const val HEX_BASE = 16
    }
}

internal fun shellState(event: JSONObject): String =
    when {
        !event.getBoolean("created") -> Outcome.NOT_SENT
        event.getBoolean("cleanupUnknown") -> Outcome.UNKNOWN
        event.getBoolean("cancelled") -> Outcome.FAILED
        event.getBoolean("startupUnknown") || !event.getBoolean("rootDone") || !event.getBoolean("domainEmpty") -> Outcome.UNKNOWN
        event.getInt("startupStage") != 0 || event.getInt("signal") != 0 || event.getInt("exitCode") != 0 -> Outcome.FAILED
        else -> Outcome.DONE
    }

internal fun shellReason(
    event: JSONObject,
    result: String,
    cleanup: String,
): String? =
    when {
        !event.getBoolean("created") -> event.getString("reason")
        event.getBoolean("cancelled") -> event.getString("reason") + "; cleanup " + cleanup
        result == Outcome.UNKNOWN -> "Execution or cleanup could not be confirmed"
        event.getInt("startupStage") != 0 -> "Shell startup failed"
        event.getInt("signal") != 0 -> "signal=" + event.getInt("signal")
        event.getInt("exitCode") != 0 -> "exit=" + event.getInt("exitCode")
        else -> null
    }

@Suppress("MagicNumber") // ACF1 has fixed byte offsets and a three-byte length.
internal fun readShellFrame(input: DataInputStream): ShellFrame? {
    val prefix = ByteArray(FRAME_HEADER_BYTES)
    val first = input.read()
    if (first == -1) return null
    prefix[0] = first.toByte()
    input.readFully(prefix, 1, prefix.size - 1)
    if (String(prefix, 0, FRAME_MAGIC.length, Charsets.US_ASCII) != FRAME_MAGIC) throw IOException("Invalid helper frame")
    val length =
        ((prefix[FRAME_LENGTH_HIGH_OFFSET].toInt() and BYTE_MASK) shl 16) or
            ((prefix[FRAME_LENGTH_MIDDLE_OFFSET].toInt() and BYTE_MASK) shl 8) or
            (prefix[FRAME_LENGTH_LOW_OFFSET].toInt() and BYTE_MASK)
    if (length !in 0..HELPER_PAYLOAD_BYTES) throw IOException("Invalid helper frame length")
    return ShellFrame(kind = prefix[FRAME_KIND_OFFSET].toInt().toChar(), payload = ByteArray(length).also(input::readFully))
}

private const val FRAME_MAGIC = "ACF1"
private const val FRAME_HEADER_BYTES = 8
private const val FRAME_KIND_OFFSET = 4
private const val FRAME_LENGTH_HIGH_OFFSET = 5
private const val FRAME_LENGTH_MIDDLE_OFFSET = 6
private const val FRAME_LENGTH_LOW_OFFSET = 7
private const val BYTE_MASK = 255
internal const val HELPER_PAYLOAD_BYTES = 2040
private const val UNKNOWN_EXIT = 3
