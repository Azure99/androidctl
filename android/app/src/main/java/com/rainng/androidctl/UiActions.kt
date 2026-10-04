package com.rainng.androidctl

import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONArray
import org.json.JSONObject

internal data class ActionAdmission(
    val fresh: JSONObject,
)

// UI actions share original-target guards and one request budget.
@Suppress("DEPRECATION")
internal class UiActions(
    private val ui: UiBackend,
) {
    fun act(run: UiRun): JSONObject = actOnTarget(run)

    private fun actOnTarget(run: UiRun): JSONObject {
        val action = action(run)
        val source =
            run.measure("preWaitMs") {
                if (run.args.has("ref")) ui.snapshots.target(run.args.getString("ref")) else selected(run)
            }
        val method = resolutionMethod(run)
        val report =
            { target: JSONObject ->
                run.resolve(method, target)
                run.select(action.wire, target)
            }
        return withOriginalTarget(run, source, report) { original, node ->
            val admitted = run.measure("preWaitMs") { awaitAction(run, node, original, action) }
            val current = admitted.fresh
            val reported = copy(current).put("ref", original.getString("ref"))
            report(reported)
            when {
                action.alreadySatisfied(current) -> unchanged(run, "Target is already focused")
                else -> sendAction(run, node, action)
            }
        }
    }

    fun key(
        run: UiRun,
        send: () -> JSONObject,
    ): JSONObject {
        val source = ui.snapshots.target(run.args.getString("ref"))
        val method = resolutionMethod(run)
        val report =
            { target: JSONObject ->
                run.resolve(method, target)
                run.select("key", target, run.args.getString("key"))
            }
        return withOriginalTarget(run, source, report) { original, node ->
            val current = run.measure("preWaitMs") { awaitInputFocus(run, node, original) }
            val reported = copy(current).put("ref", original.getString("ref"))
            report(reported)
            val before = ui.snapshots.cached()
            val result = send()
            run.remember(result)
            val observed = ui.postAction.settle(run, before)
            val answer = observed.appliedTo(result)
            run.remember(answer)
            answer
        }
    }

    private fun withOriginalTarget(
        run: UiRun,
        source: UiTarget,
        report: (JSONObject) -> Unit,
        perform: (JSONObject, AccessibilityNodeInfo) -> JSONObject,
    ): JSONObject {
        val original = copy(source.facts)
        report(original)
        requireScope(run, original)
        val node = AccessibilityNodeInfo.obtain(source.handle)
        try {
            return perform(original, node)
        } finally {
            node.recycle()
        }
    }

    private fun resolutionMethod(run: UiRun): String =
        when {
            !run.args.has("ref") -> "selector"
            else -> "original"
        }

    private fun requireScope(
        run: UiRun,
        original: JSONObject,
    ) {
        requireWindow(run, original)
        val requestedPackage =
            run.args
                .optJSONObject("scope")
                ?.text("package")
                .orEmpty()
        if (requestedPackage.isNotEmpty() && requestedPackage != original.text("packageName")) {
            throw Rejected("scopeMismatch", "Ref belongs to a different package; select its original scope")
        }
    }

    private fun requireWindow(
        run: UiRun,
        target: JSONObject,
    ) {
        val explicit = run.requestedWindow
        if (explicit != null && explicit != target.getInt("windowId")) {
            throw Rejected(
                "scopeMismatch",
                "Ref belongs to window ${target.getInt("windowId")}; repeat --window ${target.getInt("windowId")}",
            )
        }
    }

    private fun unchanged(
        run: UiRun,
        reason: String,
    ): JSONObject =
        outcome(
            run.command,
            reason = reason,
            data =
                json("settle" to "unchanged"),
        )

    private fun sendAction(
        run: UiRun,
        node: AccessibilityNodeInfo,
        action: UiAction,
    ): JSONObject {
        val before = ui.snapshots.cached()
        val pending =
            outcome(
                run.command,
                Outcome.UNKNOWN,
                "Platform action result pending",
            )
        val result = invoke(run, node, action, pending).put("command", run.command)
        run.remember(result)
        val observed = ui.postAction.settle(run, before)
        val answer = observed.appliedTo(result)
        run.remember(answer)
        return answer
    }

    internal fun invoke(
        run: UiRun,
        node: AccessibilityNodeInfo,
        action: UiAction,
        pending: JSONObject,
    ): JSONObject {
        val callback =
            ui.platform(run).action(node, action.platformId, null, run.budget) {
                ui.dispatch(run, pending)
            }
        return outcome(action.wire, callback.classify(), data = json("platform" to callback.json()))
    }

    private fun selected(run: UiRun): UiTarget {
        val fields = run.args.optJSONObject("selector") ?: throw Rejected("selector", "Provide a ref or at least one selector flag")
        if (fields.length() == 0) throw Rejected("selector", "At least one selector flag is required")
        val selector = UiSelector(fields)
        var reason = "No matching ready target"
        while (run.budget.remaining() > 0) {
            val tree = ui.capture(run)
            val nodes = tree.getJSONArray("nodes").objects()
            val byRef = nodes.associateBy { it.getString("ref") }
            val answers = nodes.map { it to selector.matches(it) }
            val matches = answers.filter { it.second == true }.map { it.first }
            val candidates = matches.groupBy { it.getJSONObject("view").text("selectionTarget").ifEmpty { it.getString("ref") } }
            val complete = !tree.getBoolean("partial") && answers.none { it.second == null }
            if (candidates.size > 1) {
                throw Rejected(
                    "ambiguous",
                    UiSelector.ambiguity(
                        candidates.keys.map(byRef::getValue),
                        incomplete = !complete,
                    ),
                )
            }
            val target = candidates.keys.singleOrNull()?.let(byRef::get)
            if (!complete) {
                reason = "Task observation is incomplete; uniqueness is unknown"
            } else if (target == null) {
                reason = "No target matches the selector in the complete observation"
            } else {
                inputReady(tree.getJSONObject("display"))
                activeWindow(run, tree.getJSONArray("windows"), target)
                return ui.snapshots.target(target.getString("ref"))
            }
            run.budget.pause()
        }
        throw Rejected("notReady", reason)
    }

    private fun readOriginal(
        run: UiRun,
        node: AccessibilityNodeInfo,
        original: JSONObject,
        requireInputFocus: Boolean = false,
    ): JSONObject {
        ui.current(run)
        inputReady(ui.deviceFacts())
        val sampled = ui.platform(run).windows()
        try {
            val windows = NodeFacts.defaultWindows(sampled)
            activeWindow(run, JSONArray(windows.map(NodeFacts::window)), original)
            if (requireInputFocus && windows.singleOrNull { it.isFocused }?.id != original.getInt("windowId")) {
                throw Rejected("inputFocus", "Original target is outside the current input-focus window; run snapshot")
            }
            val window = windows.single { it.id == original.getInt("windowId") }
            val fresh = FreshNodes(run.budget).use { it.read(node, window) }
            val identity =
                UiSemantics.identity(fresh)
                    ?: throw Rejected("identityUnknown", "Complete current selection identity is unavailable")
            if (identity != UiSemantics.identity(original)) {
                throw Rejected("selectionChanged", "Original identity, name or row changed; run snapshot")
            }
            return fresh
        } finally {
            sampled.forEach { it.recycle() }
        }
    }

    internal fun awaitAction(
        run: UiRun,
        node: AccessibilityNodeInfo,
        original: JSONObject,
        action: UiAction,
    ): ActionAdmission {
        var reason = "Original target is not ready within the request budget"
        while (run.budget.remaining() > 0) {
            val fresh = readOriginal(run, node, original)
            if (action.alreadySatisfied(fresh)) return ActionAdmission(fresh)
            val condition = action.notReadyReason(fresh) ?: return ActionAdmission(fresh)
            reason = "Original target is not ready for ${action.wire}: $condition"
            run.budget.pause()
        }
        throw Rejected("notReady", reason)
    }

    internal fun awaitInputFocus(
        run: UiRun,
        node: AccessibilityNodeInfo,
        original: JSONObject,
    ): JSONObject {
        var reason = "Original target is not ready within the request budget"
        while (run.budget.remaining() > 0) {
            val fresh = readOriginal(run, node, original, requireInputFocus = true)
            val condition = UiAction.inputFocusNotReadyReason(fresh) ?: return fresh
            reason = "Original target is not ready for input focus: $condition"
            run.budget.pause()
        }
        throw Rejected("notReady", reason)
    }

    private fun activeWindow(
        run: UiRun,
        windows: JSONArray,
        target: JSONObject,
    ) {
        if (windows.objects().none { it.getInt("id") == target.getInt("windowId") }) {
            throw Rejected("selectionChanged", "Original target window is no longer available; run snapshot")
        }
        requireWindow(run, target)
        if (run.requestedWindow != null) return
        val active = windows.objects().filter { it.optBoolean("active") && it.text("type") != "inputMethod" }
        if (active.singleOrNull()?.getInt("id") != target.getInt("windowId")) {
            throw Rejected(
                "covered",
                "Target is outside the active task window; use snapshot --window ${target.getInt(
                    "windowId",
                )} and repeat that scope on the action",
            )
        }
    }

    private fun action(run: UiRun): UiAction = UiAction.parse(run.command)
}
