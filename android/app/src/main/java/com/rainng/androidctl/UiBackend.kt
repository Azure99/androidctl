package com.rainng.androidctl

import android.accessibilityservice.AccessibilityServiceInfo
import android.annotation.SuppressLint
import android.app.UiAutomation
import android.content.Context
import android.os.Build
import android.os.HandlerThread
import android.os.SystemClock
import android.view.accessibility.AccessibilityWindowInfo
import org.json.JSONObject
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

internal class ObservationDelivery(
    val capture: String?,
    val facts: JSONObject,
    val problem: JSONObject?,
)

@SuppressLint("PrivateApi", "SoonBlockedPrivateApi") // Shizuku worker accesses the platform automation backend.
internal class UiBackend(
    private val context: Context,
    private val sessions: Sessions,
    private val workerId: String,
) {
    private val executor = Executors.newSingleThreadExecutor { Thread(it, "androidctl-ui") }
    private var callbackThread: HandlerThread? = null
    private var automation: UiAutomation? = null
    private var platformConnection: Any? = null
    private var platform: AccessibilityConnection? = null
    private var generation = 0
    val snapshots = UiSnapshot()
    val delivery = UiDelivery(sessions, workerId)
    private val visuals = VisualInput(this)

    @Volatile private var closeError: String? = null
    val cleanupFailure: String? get() = closeError

    fun deviceFacts(): JSONObject = deviceFacts(context)

    fun capabilities(owner: SessionOwner?): JSONObject =
        synchronized(sessions) {
            json(
                "androidApi" to Build.VERSION.SDK_INT,
                "coordinates" to visuals.status(sessions.ownsIdleUi(owner)),
            )
        }

    fun execute(
        owner: SessionOwner,
        command: String,
        args: JSONObject,
        deadline: Long,
    ): JSONObject {
        if (command == "snapshot" && args.optBoolean("cached") && args.optBoolean("screenshot")) {
            throw Rejected("cachedScreenshot", "Cached observations cannot request a new screenshot")
        }
        val budget = UiBudget(deadline)
        val run = UiRun(command, sessions.acquire(owner), args, budget)
        closeError?.let { throw Rejected("uiCleanup", "UI cleanup failed: $it; restart the worker") }
        val resource = sessions.reserve(run.lease, ResourceKind.UI, changing = command !in RpcMethods.UI_READ_ONLY)
        executor.execute {
            try {
                current(run)
                run.budget.reserveDelivery(snapshots.deliveryTree()?.size)
                perform(run)
            } catch (error: Exception) {
                finish(run, run.failure(error))
            } finally {
                sessions.finished(resource)
                run.finished.countDown()
            }
        }
        val completed = run.finished.await(run.budget.responseRemaining(), TimeUnit.MILLISECONDS)
        return run.response(expired = !completed)
    }

    private fun perform(run: UiRun) {
        if (run.command == "snapshot") {
            val tree = if (run.args.optBoolean("cached")) snapshots.cached() else capture(run)
            if (run.args.has("renderRef") &&
                tree.getJSONArray("nodes").objects().none { it.getString("ref") == run.args.getString("renderRef") }
            ) {
                throw Rejected("staleRef", "Ref is not in the current snapshot; run snapshot")
            }
        } else {
            capture(run)
        }
        finish(run, outcome(run.command))
    }

    private fun finish(
        run: UiRun,
        result: JSONObject,
    ) {
        if (run.command != "screenshot") run.remember(result)
        val screenshot = run.command == "screenshot" || run.args.optBoolean("screenshot")
        if (screenshot) visuals.invalidate()
        val observed = observation(run, screenshot)
        val answer = observedResult(run, result, observed)
        val capture = observed.capture
        if (screenshot && run.budget.remaining() == 0L) answer.getJSONObject("data").put("imageError", "Request budget exhausted")
        if (run.command == "screenshot") {
            val imagePending = answer.getString("result") == Outcome.DONE
            if (imagePending) {
                run.remember(copy(answer).put("result", Outcome.FAILED).put("reason", "Screenshot did not complete within the budget"))
            }
            if (capture != null) {
                val failure = attachImage(run, capture)
                if (imagePending && failure != null) answer.put("result", Outcome.FAILED).put("reason", failure)
            } else if (imagePending) {
                answer.put("result", Outcome.FAILED).put("reason", "Screenshot unavailable within the budget")
            }
            run.remember(answer)
        } else {
            run.remember(answer)
            if (screenshot && capture != null) attachImage(run, capture)
        }
    }

    private fun observedResult(
        run: UiRun,
        result: JSONObject,
        observed: ObservationDelivery,
    ): JSONObject {
        val answer = copy(result)
        val data = answer.getJSONObject("data")
        val notice = listOf(data.text("notice"), observed.facts.text("notice")).filter(String::isNotEmpty).joinToString("; ")
        for (field in observed.facts.keys()) data.put(field, observed.facts.get(field))
        if (notice.isNotEmpty()) data.put("notice", notice)
        if (observed.capture == null && !data.has("snapshotError")) data.put("snapshotError", "No current snapshot")
        val missing = observed.capture == null || run.snapshotError != null
        if (run.command == "snapshot" && missing && answer.getString("result") == Outcome.DONE) {
            answer.put("result", Outcome.FAILED).put("reason", data.optString("snapshotError", "Snapshot unavailable"))
            observed.problem?.let { answer.put("error", it) }
        }
        return answer
    }

    private fun observation(
        run: UiRun,
        screenshot: Boolean,
    ): ObservationDelivery {
        val data = json("samples" to run.samples)
        run.snapshotError?.let { data.put("snapshotError", it) }
        snapshots.tree?.let { tree ->
            data
                .put("snapshotCached", run.samples == 0)
                .put("snapshotSequence", tree.getLong("sequence"))
                .put("snapshotSampleMs", tree.getLong("finishedMs"))
                .put("snapshotAgeMs", (SystemClock.elapsedRealtime() - tree.getLong("finishedMs")).coerceAtLeast(0))
                .put("snapshotScope", copy(tree.getJSONObject("scope")))
                .put(
                    "snapshotBeforeAction",
                    run.command !in RpcMethods.UI_READ_ONLY && (!run.dispatchStarted || tree.getLong("sequence") == run.beforeSequence),
                )
        }
        var captureProblem: JSONObject? = null
        val capture =
            try {
                prepareCapture(run, screenshot)
            } catch (error: Exception) {
                val issue = problem(error)
                captureProblem = issue
                data.put("snapshotError", issue.getString("message")).put("snapshotProblem", issue)
                null
            }
        if (capture != null) {
            data.put("capture", capture)
            snapshots.notice()?.let { data.put("notice", it) }
        }
        return ObservationDelivery(capture, data, captureProblem)
    }

    private fun prepareCapture(
        run: UiRun,
        screenshot: Boolean,
    ): String? {
        if (snapshots.tree == null) return null
        if (!sessions.isCurrent(run.lease)) return null
        val prepared = requireNotNull(snapshots.deliveryTree())
        return if (run.args.optBoolean("cached")) {
            delivery.cached(run.lease, prepared)
        } else {
            delivery.createCapture(run.lease, prepared, screenshot && run.budget.remaining() > 0)
        }
    }

    private fun attachImage(
        run: UiRun,
        capture: String,
    ): String? =
        try {
            val image = visuals.screenshot(run)
            delivery.image(run.lease, capture, image.first, image.second)
            null
        } catch (error: Exception) {
            val reason = message(error)
            if (sessions.isCurrent(run.lease)) delivery.imageFailed(run.lease, capture, reason)
            reason
        }

    fun current(run: UiRun) {
        run.budget.check()
        sessions.requireCurrent(run.lease)
    }

    fun capture(
        run: UiRun,
        until: Long = run.budget.deadline,
    ): JSONObject {
        while (true) {
            try {
                return collect(run, until)
            } catch (error: Rejected) {
                if (error.code != "scopeUnknown" || SystemClock.elapsedRealtime() >= until) throw error
                run.budget.pause(until)
                if (SystemClock.elapsedRealtime() >= until || run.budget.remaining() == 0L) throw error
            }
        }
    }

    @Suppress("DEPRECATION")
    private fun collect(
        run: UiRun,
        until: Long,
        scope: JSONObject = run.args.optJSONObject("scope") ?: JSONObject(),
        windows: List<AccessibilityWindowInfo>? = null,
    ): JSONObject {
        try {
            var sampled = windows
            val collected =
                try {
                    if (SystemClock.elapsedRealtime() >= minOf(until, run.budget.deadline)) {
                        throw Rejected("snapshotBudget", "Snapshot budget exhausted")
                    }
                    current(run)
                    connect(run)
                    if (SystemClock.elapsedRealtime() >= until) throw Rejected("snapshotBudget", "Snapshot budget exhausted")
                    sampled = sampled ?: requireNotNull(platform).windows()
                    TreeCollector(sampled, scope, run.budget, until)
                } catch (error: Exception) {
                    sampled?.forEach { it.recycle() }
                    throw error
                }
            var collector: TreeCollector? = collected
            val replacement =
                try {
                    collected
                        .collect()
                        .put("display", deviceFacts())
                        .put("connection", generation)
                    val assembled = snapshots.assemble(collected)
                    sessions.withCurrentLease(run.lease) {
                        snapshots.install(assembled)
                        collector = null
                        delivery.clear()
                    }
                    assembled
                } catch (error: Exception) {
                    collector?.close()
                    throw error
                }
            run.samples++
            run.snapshotError = null
            if (replacement.changed) visuals.invalidate()
            run.budget.reserveDelivery(snapshots.deliveryTree()?.size)
            return replacement.tree
        } catch (error: Exception) {
            run.snapshotError = message(error)
            visuals.invalidate()
            throw error
        }
    }

    fun automation(run: UiRun): UiAutomation {
        connect(run)
        return requireNotNull(automation)
    }

    fun platform(run: UiRun): AccessibilityConnection {
        connect(run)
        return requireNotNull(platform)
    }

    private fun connect(run: UiRun) {
        current(run)
        if (platform != null) return
        run.measure("uiConnectMs") { connectPlatform(run) }
    }

    private fun connectPlatform(run: UiRun) {
        try {
            val thread = HandlerThread("androidctl-ui-callbacks").apply { start() }
            callbackThread = thread
            val connection = HiddenApi.newAutomationConnection.newInstance()
            platformConnection = connection
            val ui = HiddenApi.newAutomation.newInstance(thread.looper, connection)
            automation = ui
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                HiddenApi.connectWithTimeout.invoke(ui, UiAutomation.FLAG_DONT_SUPPRESS_ACCESSIBILITY_SERVICES, run.budget.remaining())
            } else {
                HiddenApi.connect.invoke(ui, UiAutomation.FLAG_DONT_SUPPRESS_ACCESSIBILITY_SERVICES)
            }
            current(run)
            ui.serviceInfo =
                ui.serviceInfo.apply {
                    flags =
                        flags or AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS or
                        AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS or
                        AccessibilityServiceInfo.FLAG_INCLUDE_NOT_IMPORTANT_VIEWS
                }
            platform = AccessibilityConnection(ui, context)
            generation++
        } catch (error: Exception) {
            try {
                disconnect()
            } catch (cleanup: Exception) {
                closeError = message(cleanup)
                sessions.closing(run.lease)
            }
            throw error
        }
    }

    fun clear(lease: Lease) {
        delivery.clear()
        val closing = sessions.closing(lease)
        executor.execute {
            try {
                snapshots.close()
                visuals.invalidate()
                disconnect()
                sessions.finished(closing)
            } catch (error: Exception) {
                closeError = message(error)
            }
        }
    }

    private fun disconnect() {
        val ui = automation
        val internal =
            ui?.let {
                HiddenApi.remoteCallbackThread.get(it) as? HandlerThread
            }
        try {
            if (ui != null) unregister(ui)
        } finally {
            internal?.quitSafely()
            internal?.join()
            callbackThread?.quitSafely()
            callbackThread?.join()
        }
        automation = null
        platform = null
        platformConnection = null
        callbackThread = null
    }

    private fun unregister(ui: UiAutomation) {
        try {
            HiddenApi.disconnect.invoke(ui)
            return
        } catch (error: Exception) {
            if (platform != null || rootCause(error) !is IllegalStateException) throw error
        }
        // Failed connect may register a service before UiAutomation acquired its connection id.
        val connection = platformConnection ?: return
        try {
            HiddenApi.disconnectConnection.invoke(connection)
        } catch (cleanup: Exception) {
            val cause = rootCause(cleanup)
            if (cause !is IllegalStateException || cause.message != "Already disconnected.") throw cleanup
        }
    }
}
