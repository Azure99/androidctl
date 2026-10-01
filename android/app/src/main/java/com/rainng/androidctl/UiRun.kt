package com.rainng.androidctl

import android.os.SystemClock
import org.json.JSONObject
import java.util.concurrent.CountDownLatch

internal class UiRun(
    val command: String,
    val lease: Lease,
    val args: JSONObject,
    val budget: UiBudget,
) {
    val finished = CountDownLatch(1)

    @Volatile var dispatchStarted = false
    var beforeSequence: Long? = null
    var samples = 0
    var snapshotError: String? = null

    private var fact: String? = null
    private val timings = JSONObject()

    fun <T> measure(
        field: String,
        action: () -> T,
    ): T {
        val started = SystemClock.elapsedRealtime()
        return try {
            action()
        } finally {
            synchronized(this) {
                timings.put(field, timings.optLong(field) + SystemClock.elapsedRealtime() - started)
            }
        }
    }

    // Keep the execution fact before fallible observation and attachment delivery.
    @Synchronized fun remember(value: JSONObject) {
        fact = value.toString()
    }

    private fun missingFactOutcome(): String =
        when {
            dispatchStarted -> Outcome.UNKNOWN
            else -> Outcome.NOT_SENT
        }

    @Synchronized fun failure(error: Exception): JSONObject {
        val result =
            fact?.let(::JSONObject)
                ?: outcome(command, missingFactOutcome(), message(error)).put("error", problem(error))
        if (result.getString("result") == Outcome.NOT_SENT) {
            result.put("reason", message(error)).put("error", problem(error))
        }
        return result
    }

    @Synchronized fun response(expired: Boolean = false): JSONObject {
        val value =
            fact?.let(::JSONObject)
                ?: outcome(
                    command,
                    missingFactOutcome(),
                    "Request budget exhausted; snapshot unavailable",
                )
        val data = value.getJSONObject("data")
        for (field in timings.keys()) data.put(field, timings.getLong(field))
        if (expired) {
            if (!data.has("capture") && !data.has("snapshotError")) {
                data.put("snapshotError", "Request budget exhausted before snapshot delivery")
            }
        }
        return value
    }
}
