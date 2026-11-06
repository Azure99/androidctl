package com.rainng.androidctl

import org.json.JSONArray
import org.json.JSONObject
import java.util.Locale

internal object UiRefRepair {
    private data class Match(
        val node: JSONObject,
        val score: Int,
    )

    // At most one same-request repair, only after a definitely unsent first attempt rejects a changed target.
    fun perform(
        snapshots: UiSnapshot,
        run: UiRun,
        capture: () -> JSONObject,
        action: (UiTarget, String) -> JSONObject,
    ): JSONObject {
        val target =
            run.measure("preWaitMs") {
                snapshots.target(run.args.getString("ref"))
            }
        val origin = target.facts
        val first =
            try {
                action(target, "original")
            } catch (error: Rejected) {
                if (run.dispatchStarted) throw error
                run.failure(error)
            }
        if (first.getString("result") != Outcome.NOT_SENT || !repairable(run, first.optJSONObject("error")?.text("code"))) {
            return first
        }
        run.remember(first)
        val recovered =
            run.measure("preWaitMs") {
                snapshots.target(select(origin, capture()).getString("ref"))
            }
        run.clearUnsentAttempt()
        return action(recovered, "signature")
    }

    private val repairableCodes = setOf("staleRef", "selectionChanged", "identityUnknown")

    private fun repairable(
        run: UiRun,
        code: String?,
    ): Boolean = !run.dispatchStarted && code in repairableCodes && run.budget.remaining() != 0L

    fun select(
        source: JSONObject,
        tree: JSONObject,
    ): JSONObject {
        if (tree.getBoolean("partial")) throw Rejected("identityUnknown", "Ref repair observation is incomplete")
        val matches: List<Match> =
            tree
                .getJSONArray("nodes")
                .objects()
                .filter { it.optBoolean("refAvailable") }
                .mapNotNull { match(source, it) }
                .sortedByDescending { it.score }
        val best =
            matches.firstOrNull()
                ?: throw Rejected("staleRef", "No current target matches the original ref signature")
        val second = matches.getOrNull(1)
        if (second != null && best.score - second.score < 2) {
            throw Rejected("ambiguous", "Original ref signature matches multiple current targets without a clear best match")
        }
        return best.node
    }

    @Suppress("CyclomaticComplexMethod", "MagicNumber") // Keep the reviewed eligibility gates and fixed evidence weights together.
    private fun match(
        source: JSONObject,
        node: JSONObject,
    ): Match? {
        val oldView = source.getJSONObject("view")
        val newView = node.getJSONObject("view")
        if (source.text("packageName") != node.text("packageName") ||
            oldView.text("role") != newView.text("role")
        ) {
            return null
        }
        if (!oldView.getBoolean("nameComplete") || !newView.getBoolean("nameComplete")) return null
        val sameName = same(oldView.text("name"), newView.text("name"))
        if (oldView.text("name").isNotBlank() && newView.text("name").isNotBlank() && !sameName) return null
        val sameId =
            !source.unavailable("resourceId") &&
                !node.unavailable("resourceId") &&
                same(source.text("resourceId"), node.text("resourceId"))
        val anchors = (if (sameName) 1 else 0) + (if (sameId) 1 else 0)
        if (anchors == 0) return null
        val sameClass =
            !source.unavailable("className") &&
                !node.unavailable("className") &&
                same(source.text("className"), node.text("className"))
        val sameRow = same(source.getJSONObject("row").text("label"), node.getJSONObject("row").text("label"))
        if (source.getJSONObject("row").text("label").isNotBlank() &&
            node.getJSONObject("row").text("label").isNotBlank() &&
            !sameRow
        ) {
            return null
        }
        val sharedActions = strings(source.optJSONArray("actions")).intersect(strings(node.optJSONArray("actions"))).size
        val nearBounds = closeBounds(source.optJSONArray("bounds"), node.optJSONArray("bounds"))
        val classAndRowMatch = sameClass && sameRow
        val supported = sharedActions > 0 || nearBounds || classAndRowMatch
        if (anchors == 1 && !supported) return null
        val score =
            anchors * 8 + (if (sameClass) 3 else 0) + (if (sameRow) 3 else 0) +
                sharedActions.coerceAtMost(2) * 2 + (if (nearBounds) 2 else 0)
        return Match(node, score)
    }

    private fun same(
        left: String,
        right: String,
    ): Boolean = left.isNotBlank() && key(left) == key(right)

    private fun key(value: String): String = value.trim().replace(Regex("\\s+"), " ").lowercase(Locale.ROOT)

    private fun strings(values: JSONArray?): Set<String> =
        if (values == null) emptySet() else (0 until values.length()).map { values.getString(it) }.toSet()

    @Suppress("MagicNumber") // Compare all four coordinates with the reviewed total pixel-distance tolerance.
    private fun closeBounds(
        left: JSONArray?,
        right: JSONArray?,
    ): Boolean {
        if (left == null || right == null) return false
        if (left.length() != 4 || right.length() != 4) return false
        return (0 until 4).sumOf { kotlin.math.abs(left.getInt(it) - right.getInt(it)) } <= 8
    }
}
