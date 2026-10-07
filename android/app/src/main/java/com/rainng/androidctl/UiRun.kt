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
    val requestedWindow: Int? get() = args.optJSONObject("scope")?.takeIf { it.has("window") }?.getInt("window")
    val finished = CountDownLatch(1)

    @Volatile var dispatchStarted = false
        private set
    var beforeSequence: Long? = null
    var samples = 0
    var snapshotError: String? = null
    var absentWindow: Int? = null

    private var fact: String? = null
    private var responded = false
    private val timings = JSONObject()
    private var resolved: JSONObject? = null
    private var selected: JSONObject? = null

    @Synchronized fun resolve(
        method: String,
        target: JSONObject,
    ) {
        resolved = json("method" to method, "target" to targetFacts(target))
    }

    @Synchronized fun select(
        action: String,
        target: JSONObject? = null,
        key: String? = null,
    ) {
        selected = selection(action, target, key)
    }

    private fun withActionFacts(value: JSONObject): JSONObject {
        if (command in RpcMethods.UI_READ_ONLY) return value
        val data = value.getJSONObject("data")
        data.put("dispatchStarted", dispatchStarted)
        resolved?.let { data.put("resolved", copy(it)) }
        if (command == "fill") return value
        val choice = selected
        if (choice != null) {
            data.put("selected", copy(choice))
            val target = choice.optJSONObject("target")
            if (target != null && !data.has("verificationTarget") && choice.getString("action") in listOf("fill", "check", "uncheck")) {
                data.put("verificationTarget", json("ref" to target.getString("ref")))
            }
        }
        return value
    }

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

    @Synchronized fun begin(pending: JSONObject?) {
        budget.check()
        if (responded) throw Rejected("requestEnded", "Request has already returned")
        dispatchStarted = true
        fact = pending?.toString()
    }

    // Keep the execution fact before fallible observation and attachment delivery.
    @Synchronized fun remember(value: JSONObject) {
        fact = value.toString()
    }

    private fun missingFactOutcome(error: Exception? = null): String =
        when {
            dispatchStarted -> Outcome.UNKNOWN
            command == "wait" && (error as? Rejected)?.code != "condition" -> Outcome.FAILED
            else -> Outcome.NOT_SENT
        }

    @Synchronized fun failure(error: Exception): JSONObject {
        val result =
            fact?.let(::JSONObject)
                ?: outcome(command, missingFactOutcome(error), message(error)).put("error", problem(error))
        if (result.getString("result") == Outcome.NOT_SENT) {
            result.put("reason", message(error)).put("error", problem(error))
        }
        return withActionFacts(result)
    }

    @Synchronized fun response(expired: Boolean = false): JSONObject {
        responded = true
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
            if (command == "screenshot" || args.optBoolean("screenshot")) {
                data.put("imageError", "Request budget exhausted before image delivery")
            }
        }
        return withActionFacts(value)
    }

    companion object {
        fun selection(
            action: String,
            target: JSONObject? = null,
            key: String? = null,
        ): JSONObject =
            json("target" to target?.let(::targetFacts), "action" to action).apply {
                if (key != null) put("key", key)
            }

        fun targetFacts(node: JSONObject): JSONObject =
            json(
                "ref" to node.getString("ref"),
                "role" to UiSemantics.role(node).wire,
                "name" to UiSelector.summaryText(node.getJSONObject("view").getString("name")),
                "windowId" to node.getInt("windowId"),
                "bounds" to node.get("bounds"),
            )
    }
}
