package com.rainng.androidctl

import android.icu.lang.UCharacter
import org.json.JSONObject
import java.text.Normalizer

internal class ConditionEvaluation(
    val matched: Boolean,
    val predicate: JSONObject?,
)

internal class UiCondition(
    args: JSONObject,
) {
    private val field =
        listOf("text", "app", "gone", "idle").filter(args::has).singleOrNull()
            ?: throw Rejected("condition", "Provide exactly one of --contains, --app, --gone or --idle")
    private val expected: String
    private val selector: UiSelector?
    private val samples = if (field == "idle") SettledSamples(null) else null

    init {
        expected = if (field in listOf("text", "app")) args.getString(field) else ""
        if (field in listOf("text", "app")) {
            if (expected.isEmpty() || field == "text" && folded(expected).isEmpty()) {
                throw Rejected("condition", "Wait condition must not be empty")
            }
        }
        if (field in listOf("gone", "idle") && !args.getBoolean(field)) {
            throw Rejected("condition", "--$field must be enabled")
        }
        if (args.has("selector") && field != "gone") throw Rejected("condition", "Selector fields require --gone")
        selector =
            if (field == "gone") {
                val fields =
                    args.optJSONObject("selector")?.takeIf { it.length() > 0 }
                        ?: throw Rejected("condition", "--gone requires at least one selector field")
                try {
                    UiSelector(fields)
                } catch (error: Rejected) {
                    throw Rejected("condition", message(error))
                }
            } else {
                null
            }
    }

    fun evaluate(tree: JSONObject): ConditionEvaluation =
        when (field) {
            "gone" -> gone(tree)
            "idle" -> idle(tree)
            else -> {
                val predicate = (if (field == "text") text(tree) else app(tree))?.put("condition", field)
                ConditionEvaluation(predicate != null, predicate)
            }
        }

    fun missing(reason: String) {
        samples?.missing(reason)
    }

    @Suppress("UnreachableCode") // Android SDK JSON stubs differ from the real JSON exercised by condition tests.
    private fun gone(tree: JSONObject): ConditionEvaluation {
        val answers = tree.getJSONArray("nodes").objects().map { requireNotNull(selector).matches(it) }
        val matches = answers.count { it == true }
        val unknown = answers.count { it == null }
        val complete = !tree.getBoolean("partial")
        val tested = json("condition" to field, "matches" to matches, "unknown" to unknown, "complete" to complete)
        return ConditionEvaluation(complete && matches == 0 && unknown == 0, tested)
    }

    @Suppress("UnreachableCode") // Android SDK JSON stubs differ from the real JSON exercised by condition tests.
    private fun idle(tree: JSONObject): ConditionEvaluation {
        val stable = requireNotNull(samples)
        stable.accept(tree)
        val matched = stable.stableWithoutBaseline()
        val tested =
            json(
                "condition" to field,
                "sequence" to tree.getLong("sequence"),
                "complete" to !tree.getBoolean("partial"),
                "loading" to UiSemantics.loading(tree.getJSONArray("nodes").objects()),
                "stable" to matched,
            )
        return ConditionEvaluation(matched, tested)
    }

    private fun text(tree: JSONObject): JSONObject? {
        val needle = folded(expected)
        val visible = tree.getJSONArray("nodes").objects().filter { it.getBoolean("visible") }
        for (node in visible) {
            for (name in listOf("text", "description", "stateDescription")) {
                if (node.unavailable(name) || (name == "text" && !realText(node))) continue
                val value = node.text(name)
                if (folded(value).contains(needle)) {
                    return json("field" to name, "matchedText" to value, "windowId" to node.opt("windowId"))
                }
            }
        }
        return null
    }

    private fun realText(node: JSONObject): Boolean = !node.getBoolean("showingHintText")

    @Suppress("UnreachableCode") // SDK nullability analysis differs from the real window JSON tested on device.
    private fun app(tree: JSONObject): JSONObject? {
        val active =
            tree.getJSONArray("windows").objects().filter {
                it.optBoolean("active") && it.text("type") != "inputMethod"
            }
        val window = active.singleOrNull() ?: return null
        if (window.getInt("id") != tree.getJSONObject("scope").getInt("window") ||
            !window.optBoolean("rootAvailable") ||
            window.text("packageName") != expected
        ) {
            return null
        }
        return json("field" to "packageName", "matchedText" to expected, "windowId" to window.getInt("id"))
    }

    companion object {
        fun folded(value: String): String =
            UCharacter
                .foldCase(Normalizer.normalize(value, Normalizer.Form.NFKC), true)
                .replace('-', ' ')
                .split(Regex("[\\s\\u001C-\\u001F]+"))
                .filter { it.isNotEmpty() }
                .joinToString(" ")
    }
}

internal class WaitFailure(
    val result: JSONObject,
    val retryable: Boolean,
)

internal class UiWait(
    args: JSONObject,
) {
    private val condition = UiCondition(args)
    private var predicate: JSONObject? = null

    fun start(): JSONObject = result()

    fun observe(tree: JSONObject): JSONObject {
        val evaluation = condition.evaluate(tree)
        predicate = evaluation.predicate
        return result(evaluation.matched)
    }

    fun observationFailed(error: Exception): WaitFailure {
        condition.missing(message(error))
        val retryable = error is Rejected && error.code in setOf("scopeUnknown", "snapshotBudget", "timeout")
        val result = result(reason = if (retryable) "Observation failed: ${message(error)}" else message(error))
        if (!retryable) result.put("error", problem(error))
        return WaitFailure(result, retryable)
    }

    private fun result(
        matched: Boolean = false,
        reason: String = "Condition was not met within the request budget",
    ): JSONObject =
        outcome(
            "wait",
            if (matched) Outcome.DONE else Outcome.FAILED,
            if (matched) "" else reason,
            JSONObject().apply { predicate?.let { put("predicate", copy(it)) } },
        )
}

internal fun waitUiCondition(
    ui: UiBackend,
    run: UiRun,
): JSONObject {
    val wait = UiWait(run.args)
    var result = wait.start()
    while (run.budget.remaining() > 0) {
        var retryable = true
        result =
            try {
                wait.observe(ui.capture(run))
            } catch (error: Exception) {
                val failure = wait.observationFailed(error)
                retryable = failure.retryable
                failure.result
            }
        run.remember(result)
        if (result.getString("result") == Outcome.DONE || !retryable) return result
        run.budget.pause()
    }
    return result
}
