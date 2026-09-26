package com.rainng.androidctl

import android.os.SystemClock
import org.json.JSONArray
import org.json.JSONObject

internal class CapturedStream(
    val bytes: ByteArray,
    val read: Long,
    val cut: Boolean,
) {
    val sha256 = sha256(bytes)
}

internal class OutputStore(
    private val now: () -> Long = SystemClock::elapsedRealtime,
) {
    private data class Entry(
        val stdout: CapturedStream,
        val stderr: CapturedStream,
        val at: Long,
    )

    private val items = LinkedHashMap<String, Entry>()

    @Synchronized fun add(
        id: String,
        stdout: CapturedStream,
        stderr: CapturedStream,
    ) {
        expire()
        items[id] = Entry(stdout, stderr, now())
        while (items.size > Limits.OUTPUTS) items.remove(items.keys.first())
    }

    @Synchronized fun describe(id: String): JSONObject? {
        expire()
        val entry: Entry = items[id] ?: return null
        return json(
            "state" to "available",
            "streams" to
                JSONArray(
                    listOf("stdout" to entry.stdout, "stderr" to entry.stderr).map { (name, stream) ->
                        json(
                            "stream" to name,
                            "size" to stream.bytes.size,
                            "sha256" to stream.sha256,
                            "read" to stream.read,
                            "truncated" to (stream.read > stream.bytes.size || stream.cut),
                        )
                    },
                ),
        )
    }

    @Synchronized fun read(
        id: String,
        args: JSONObject,
    ): JSONObject {
        expire()
        val entry: Entry = items[id] ?: throw Rejected("outputExpired", "Output is no longer retained; the command was not repeated")
        val stream =
            when (args.getString("stream")) {
                "stdout" -> entry.stdout
                "stderr" -> entry.stderr
                else -> throw Rejected("stream", "Choose stdout or stderr")
            }
        val bytes = stream.bytes
        val offset = args.getInt("offset")
        return json(
            "job" to id,
            "offset" to offset,
            "size" to bytes.size,
            "sha256" to stream.sha256,
            "bytes" to encodeChunk(bytes, offset),
        )
    }

    private fun expire() {
        val now = now()
        items.entries.removeAll { now - it.value.at > Limits.OUTPUT_MS }
    }
}
