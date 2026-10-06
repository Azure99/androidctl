package com.rainng.androidctl

import android.os.Bundle
import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONArray
import org.json.JSONObject

internal data class ActionAdmission(
    val fresh: JSONObject,
    val rowSelection: RowSelection? = null,
)

// UI actions share original-target guards and one request budget.
@Suppress("DEPRECATION", "LargeClass") // Keep input admission, value verification and compound steps together.
internal class UiActions(
    private val ui: UiBackend,
) {
    fun act(run: UiRun): JSONObject =
        try {
            actOnTarget(run)
        } catch (error: Exception) {
            if (run.command != "fill" || run.dispatchStarted) throw error
            val progress = FillProgress(null, run.args.optBoolean("submit"))
            progress.fail(FillStep.FOCUS, error)
            progress.result(run.dispatchStarted)
        }

    private fun actOnTarget(run: UiRun): JSONObject {
        val action = action(run)
        val source =
            run.measure("preWaitMs") {
                if (run.args.has("ref")) ui.snapshots.target(run.args.getString("ref")) else selected(run, action)
            }
        val method = resolutionMethod(run)
        val report =
            { target: JSONObject ->
                run.resolve(method, target)
                if (action != UiAction.FILL) run.select(action.wire, target)
            }
        return withOriginalTarget(run, source, report) { original, node ->
            val admitted = run.measure("preWaitMs") { awaitAction(run, node, original, action) }
            val current = admitted.fresh
            val reported = copy(current).put("ref", original.getString("ref"))
            report(reported)
            val focused = action == UiAction.FOCUS && UiSelector.focused(current)
            when {
                action == UiAction.UNCHECK && UiSemantics.role(current) == Role.RADIO ->
                    radioUncheck(run, current)
                action == UiAction.FILL ->
                    FillSequence(run, node, original, current).perform()
                action.alreadySatisfied(run.args, current) ->
                    unchanged(run, current, action, if (focused) "Target is already focused" else "Value already matches")
                action.changesChecked && !action.ready(current) ->
                    rowCheck(run, node, original, action, requireNotNull(admitted.rowSelection))
                else -> sendAction(run, node, original, current, action)
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

    private fun radioUncheck(
        run: UiRun,
        current: JSONObject,
    ): JSONObject {
        UiAdvice.requireUncheckedRadio(current)
        return unchanged(run, current, UiAction.UNCHECK, "Radio is already unchecked")
    }

    private fun unchanged(
        run: UiRun,
        current: JSONObject,
        action: UiAction,
        reason: String,
    ): JSONObject =
        outcome(
            run.command,
            reason = reason,
            data =
                (if (action.verifiesValue) UiVerification.evaluate(action, run.args, current).json() else JSONObject())
                    .put("settle", "unchanged"),
        )

    private fun sendAction(
        run: UiRun,
        node: AccessibilityNodeInfo,
        original: JSONObject,
        current: JSONObject,
        action: UiAction,
    ): JSONObject {
        val verification =
            if (action.verifiesValue) {
                { verifyCheckedValue(run, original, action) }
            } else {
                null
            }
        val before = ui.snapshots.cached()
        val route = if (action.isScroll) requireNotNull(UiScroll.route(current, action)) else null
        if (route != null) {
            run.select(UiSemantics.actionName(route), copy(current).put("ref", original.getString("ref")))
        }
        val pending =
            outcome(
                run.command,
                Outcome.UNKNOWN,
                "Platform action result pending",
            )
        val result = invoke(run, node, action, pending, platformId = route ?: action.platformId).put("command", run.command)
        val state = result.getString("result")
        run.remember(
            if (state == Outcome.DONE && verification != null) {
                copy(result).put("result", Outcome.UNKNOWN).put("reason", "Value verification pending")
            } else {
                result
            },
        )
        val observed = ui.postAction.settle(run, before, if (state == Outcome.DONE) verification else null)
        val answer = observed.appliedTo(result)
        run.remember(answer)
        return answer
    }

    private fun rowCheck(
        run: UiRun,
        originalNode: AccessibilityNodeInfo,
        original: JSONObject,
        action: UiAction,
        selection: RowSelection,
    ): JSONObject {
        val row = selection.row
        val control = selection.verificationControl
        val data = json("verificationTarget" to json("ref" to control.getString("ref")))
        run.select("click", row)
        run.remember(outcome(run.command, Outcome.NOT_SENT, "Suggested row has not been dispatched", data))
        var controlNode: AccessibilityNodeInfo? = null
        var rowNode: AccessibilityNodeInfo? = null
        try {
            val checkNode = AccessibilityNodeInfo.obtain(ui.snapshots.target(control.getString("ref")).handle)
            controlNode = checkNode
            checkedNoop(run, readOriginal(run, checkNode, control), action, data)?.let { return it }
            val clickNode = AccessibilityNodeInfo.obtain(ui.snapshots.target(row.getString("ref")).handle)
            rowNode = clickNode
            val currentRow = awaitAction(run, clickNode, row, UiAction.CLICK).fresh
            run.select("click", copy(currentRow).put("ref", row.getString("ref")))
            val sameControl = original.getString("ref") == control.getString("ref")
            val freshTarget =
                if (original.getString("ref") == row.getString("ref")) currentRow else readOriginal(run, originalNode, original)
            val freshControl = if (sameControl) freshTarget else readOriginal(run, checkNode, control)
            run.resolve(resolutionMethod(run), copy(freshTarget).put("ref", original.getString("ref")))
            checkedNoop(run, freshControl, action, data)?.let { return it }
            val before = ui.snapshots.cached()
            val pending = outcome(run.command, Outcome.UNKNOWN, "Suggested row click pending", copy(data))
            val result = invoke(run, clickNode, UiAction.CLICK, pending, data).put("command", run.command)
            val accepted = result.getString("result") == Outcome.DONE
            run.remember(
                if (accepted) copy(result).put("result", Outcome.UNKNOWN).put("reason", "Checked-state verification pending") else result,
            )
            val observed = ui.postAction.settle(run, before, if (accepted) ({ verifyCheckedValue(run, control, action) }) else null)
            val answer = observed.appliedTo(result)
            run.remember(answer)
            return answer
        } finally {
            rowNode?.recycle()
            controlNode?.recycle()
        }
    }

    private fun checkedNoop(
        run: UiRun,
        current: JSONObject,
        action: UiAction,
        data: JSONObject,
    ): JSONObject? {
        val result =
            when {
                action == UiAction.UNCHECK && UiSemantics.role(current) == Role.RADIO -> radioUncheck(run, current)
                action.alreadySatisfied(run.args, current) -> unchanged(run, current, action, "Row control already matches")
                else -> null
            }
        if (result == null) {
            UiAdvice.rowControlNotReadyReason(current)?.let { condition ->
                throw Rejected("notReady", "The suggested row's control is no longer ready for checked-state input: $condition")
            }
        }
        return result?.apply { for (field in data.keys()) getJSONObject("data").put(field, data.get(field)) }
    }

    private inner class FillSequence(
        private val run: UiRun,
        private val node: AccessibilityNodeInfo,
        private val original: JSONObject,
        private val initial: JSONObject,
    ) {
        private val before = ui.snapshots.cached()
        private val progress = FillProgress(original, run.args.optBoolean("submit"))
        private var fillObservation: PostActionObservation? = null

        fun perform(): JSONObject {
            performSteps()
            return finish()
        }

        private fun performSteps() {
            var step = FillStep.FOCUS
            try {
                if (skipMatchingValue()) return
                focus()
                if (progress.step(FillStep.FOCUS).getString("result") != Outcome.DONE) return
                step = FillStep.FILL
                val filledInput = fill()
                if (progress.step(FillStep.FILL).getString("result") != Outcome.DONE || !progress.hasSubmit) return
                step = FillStep.SUBMIT
                submit(filledInput)
            } catch (error: Exception) {
                progress.fail(step, error)
            }
        }

        private fun skipMatchingValue(): Boolean {
            if (progress.hasSubmit) return false
            val verified = UiVerification.evaluate(UiAction.FILL, run.args, initial)
            if (verified.state != VerificationState.MATCHES) return false
            val target = copy(initial).put("ref", original.getString("ref"))
            progress.record(
                FillStep.FOCUS,
                outcome(
                    "focus",
                    reason = "Focus is unnecessary because the value already matches",
                    data = stepData(UiAction.FOCUS, target),
                ),
            )
            progress.record(
                FillStep.FILL,
                UiVerification.verifiedResult(
                    outcome("fill", reason = "Value already matches", data = stepData(UiAction.FILL, target)),
                    verified,
                ),
            )
            return true
        }

        private fun result(): JSONObject = progress.result(run.dispatchStarted)

        private fun finish(): JSONObject {
            var answer = result()
            run.remember(answer)
            val submitSent =
                progress.hasSubmit && progress.step(FillStep.SUBMIT).getJSONObject("data").getBoolean("sent")
            val observed =
                when {
                    !run.dispatchStarted -> null
                    fillObservation != null && !submitSent -> fillObservation
                    else -> ui.postAction.settle(run, before)
                }
            if (observed != null) {
                answer = observed.appliedTo(answer)
                run.remember(answer)
            } else {
                answer.getJSONObject("data").put("settle", "unchanged")
            }
            return answer
        }

        private fun focus() {
            run.remember(result())
            val ready = if (UiSelector.focused(initial)) initial else awaitAction(run, node, original, UiAction.FOCUS).fresh
            val data = stepData(UiAction.FOCUS, copy(ready).put("ref", original.getString("ref")))
            if (UiSelector.focused(ready)) {
                progress.record(FillStep.FOCUS, outcome("focus", reason = "Target is already focused", data = data))
                return
            }
            progress.record(FillStep.FOCUS, outcome("focus", Outcome.NOT_SENT, "Focus has not been dispatched", data))
            val pending = progress.pending(FillStep.FOCUS, "Focus result pending")
            val focused = invoke(run, node, UiAction.FOCUS, pending, data, started = { progress.started(FillStep.FOCUS) })
            progress.record(FillStep.FOCUS, focused)
            if (focused.getString("result") != Outcome.DONE) return
            focused.put("result", Outcome.UNKNOWN).put("reason", "Input focus verification pending")
            progress.record(FillStep.FOCUS, focused)
            run.remember(result())
            awaitInputFocus(run, node, original)
            progress.record(
                FillStep.FOCUS,
                focused.put("result", Outcome.DONE).put("reason", "Input focus confirmed for the original target"),
            )
        }

        private fun fill(): JSONObject {
            progress.record(
                FillStep.FILL,
                outcome("fill", Outcome.NOT_SENT, "Fill has not been dispatched", stepData(UiAction.FILL, original)),
            )
            run.remember(result())
            val fresh = awaitFocusedFill(run, node, original)
            val verified = UiVerification.evaluate(UiAction.FILL, run.args, fresh)
            val password = UiVerification.knownPassword(fresh)
            val data = stepData(UiAction.FILL, copy(fresh).put("ref", original.getString("ref")))
            if (verified.state == VerificationState.MATCHES) {
                progress.record(
                    FillStep.FILL,
                    UiVerification.verifiedResult(outcome("fill", reason = "Value already matches", data = data), verified),
                )
                return fresh
            }
            progress.record(FillStep.FILL, outcome("fill", Outcome.NOT_SENT, "Fill has not been dispatched", data))
            val pending = progress.pending(FillStep.FILL, "Fill result pending")
            val filled = invoke(run, node, UiAction.FILL, pending, data, started = { progress.started(FillStep.FILL) })
            progress.record(FillStep.FILL, filled)
            if (filled.getString("result") != Outcome.DONE) return fresh
            if (password) {
                filled.getJSONObject("data").put("verification", "unobservable")
                progress.record(FillStep.FILL, filled)
                return fresh
            }
            filled.put("result", Outcome.UNKNOWN).put("reason", "Value verification pending")
            progress.record(FillStep.FILL, filled)
            run.remember(result())
            fillObservation =
                ui.postAction.settle(
                    run,
                    before,
                    requirePostSample = true,
                    keepObserving = { observedFillValue().state == VerificationState.DIFFERS },
                )
            val observed = observedFillValue()
            val answer = UiVerification.verifiedResult(filled, observed)
            when (observed.state) {
                VerificationState.MATCHES -> answer.put("reason", "Requested value matches the observed original field")
                VerificationState.DIFFERS -> answer.put("reason", "Observed value differs from the requested value")
                VerificationState.UNOBSERVABLE -> Unit
            }
            progress.record(FillStep.FILL, answer)
            run.remember(result())
            return fresh
        }

        private fun observedFillValue(): Verification {
            val tree = ui.snapshots.tree
            if (run.absentWindow != null || tree == null || tree.getLong("sequence") <= before.getLong("sequence")) {
                return Verification(facts = json("verificationError" to "No post-write observation is available for the original field"))
            }
            val retained =
                filledTarget()?.facts
                    ?: return Verification(
                        facts = json("verificationError" to "Filled target identity is not retained in the current observation"),
                    )
            if (!retained.getBoolean("visible")) {
                return Verification(
                    facts = json("verificationError" to "The original filled field is no longer visible in the current observation"),
                )
            }
            return UiVerification.evaluate(UiAction.FILL, run.args, retained)
        }

        private fun submit(filledInput: JSONObject) {
            run.remember(result())
            val filled = progress.step(FillStep.FILL)
            val submitOriginal =
                if (filled.getJSONObject("data").getBoolean("sent") && !UiVerification.knownPassword(filledInput)) {
                    filledTarget()?.facts
                        ?: throw Rejected("selectionChanged", "Filled target is no longer identifiable; submit was not sent")
                } else {
                    original
                }
            val focused = awaitInputFocus(run, node, submitOriginal)
            if (!UiVerification.knownPassword(filledInput)) {
                val verified = UiVerification.evaluate(UiAction.FILL, run.args, focused)
                if (verified.state != VerificationState.MATCHES) {
                    val data = verified.json().put("verificationTarget", json("ref" to submitOriginal.getString("ref")))
                    progress.record(
                        FillStep.SUBMIT,
                        outcome(
                            "submit",
                            Outcome.NOT_SENT,
                            "Current original field could not be confirmed as an exact match; submit was not sent",
                            data,
                        ),
                    )
                    run.remember(result())
                    return
                }
            }
            val selection = submitSelection(copy(focused).put("ref", submitOriginal.getString("ref")))
            val data = json("selected" to selection)
            progress.record(FillStep.SUBMIT, outcome("submit", Outcome.NOT_SENT, "Submit has not been dispatched", data))
            run.remember(result())
            val pending = progress.pending(FillStep.SUBMIT, "Submit result pending")
            val submitted =
                if (selection.getString("action") == "ime") {
                    invoke(run, node, UiAction.IME, pending, data, started = { progress.started(FillStep.SUBMIT) }).put("command", "submit")
                } else {
                    ui.submitEnter(run, data, pending) { progress.started(FillStep.SUBMIT) }
                }
            progress.record(FillStep.SUBMIT, submitted)
        }

        private fun filledTarget(): UiTarget? {
            val expected = UiSemantics.identity(original) ?: return null
            val retained = ui.snapshots.atSource(original) ?: return null
            val current = UiSemantics.identity(retained.facts) ?: return null
            return retained.takeIf { expected.copy(name = current.name) == current }
        }
    }

    private fun verifyCheckedValue(
        run: UiRun,
        original: JSONObject,
        action: UiAction,
    ): Verification {
        val retained =
            ui.snapshots.targetOrNull(original.getString("ref"))
                ?: return Verification(
                    facts = json("verificationError" to "Original ref is not retained in the current observation"),
                    terminal = true,
                )
        return UiVerification.evaluate(action, run.args, retained.facts)
    }

    @Suppress("LongParameterList") // The requested action builds arguments; scroll may select a different advertised platform ID.
    internal fun invoke(
        run: UiRun,
        node: AccessibilityNodeInfo,
        action: UiAction,
        pending: JSONObject,
        data: JSONObject = JSONObject(),
        platformId: Int = action.platformId,
        started: (() -> Unit)? = null,
    ): JSONObject {
        val args =
            if (action == UiAction.FILL) {
                Bundle().apply {
                    putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, run.args.getString("value"))
                }
            } else {
                null
            }
        val callback =
            ui.platform(run).action(node, platformId, args, run.budget) {
                ui.dispatch(run, pending)
                started?.invoke()
            }
        return outcome(action.wire, callback.classify(), data = copy(data).put("platform", callback.json()))
    }

    private fun selected(
        run: UiRun,
        action: UiAction,
    ): UiTarget {
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
                reason = "No target matches the selector in the complete observation" + UiAdvice.directionAdvice(action, null)
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
            if (action.alreadySatisfied(run.args, fresh)) return ActionAdmission(fresh)
            val condition = action.notReadyReason(fresh) ?: return ActionAdmission(fresh)
            if (action.changesChecked) {
                captureSuggestedRow(run, original, action)?.let { selection ->
                    return ActionAdmission(readOriginal(run, node, original), selection)
                }
            }
            reason = "Original target is not ready for ${action.wire}: $condition" + UiAdvice.directionAdvice(action, fresh)
            if (action.isScroll) {
                if (fresh.getBoolean("visible") &&
                    fresh.getBoolean("enabled") &&
                    UiScroll.route(fresh, action) == null
                ) {
                    throw Rejected("notReady", reason)
                }
            }
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

    internal fun awaitFocusedFill(
        run: UiRun,
        node: AccessibilityNodeInfo,
        original: JSONObject,
    ): JSONObject {
        var reason = "Original target is not ready within the request budget"
        while (run.budget.remaining() > 0) {
            val fresh = readOriginal(run, node, original, requireInputFocus = true)
            val focusCondition = UiAction.inputFocusNotReadyReason(fresh)
            if (focusCondition == null && UiAction.FILL.alreadySatisfied(run.args, fresh)) return fresh
            val condition = focusCondition ?: UiAction.FILL.notReadyReason(fresh) ?: return fresh
            reason = "Original target is not ready for fill: $condition"
            run.budget.pause()
        }
        throw Rejected("notReady", reason)
    }

    private fun captureSuggestedRow(
        run: UiRun,
        original: JSONObject,
        action: UiAction,
    ): RowSelection? {
        val tree = ui.capture(run)
        val retained = ui.snapshots.target(original.getString("ref"))
        return UiAdvice.suggestedRow(tree, retained.facts, action)
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

    private fun action(run: UiRun): UiAction =
        UiAction.parse(if (run.command == "scroll") "scroll-${run.args.getString("direction")}" else run.command)

    companion object {
        fun submitSelection(target: JSONObject): JSONObject {
            if (!target.getBoolean("editable") || !UiSelector.focused(target)) {
                throw Rejected("submitUnsupported", "Submit requires the same editable, focused field")
            }
            val actions = target.getJSONArray("actions")
            if ((0 until actions.length()).any { actions.getString(it) == UiAction.IME.wire }) {
                return UiRun.selection("ime", target)
            }
            if (target.opt("isMultiLine") != false) {
                throw Rejected("submitUnsupported", "Enter requires a known single-line original field; submit was not sent")
            }
            return UiRun.selection("key", target, "enter")
        }

        fun stepData(
            action: UiAction,
            target: JSONObject,
        ): JSONObject =
            json("selected" to UiRun.selection(action.wire, target)).apply {
                if (action.verifiesValue) put("verificationTarget", json("ref" to target.getString("ref")))
            }
    }
}
