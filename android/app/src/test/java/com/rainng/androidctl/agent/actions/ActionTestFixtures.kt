package com.rainng.androidctl.agent.actions

import com.rainng.androidctl.agent.errors.RequestValidationException
import com.rainng.androidctl.agent.rpc.codec.JsonReader
import com.rainng.androidctl.agent.runtime.ForegroundObservation
import com.rainng.androidctl.agent.runtime.ForegroundObservationProvider
import com.rainng.androidctl.agent.runtime.ObservedWindowState
import org.json.JSONObject
import java.util.ArrayDeque

internal fun actionRequest(
    kind: String,
    target: ActionTarget,
    timeoutMs: Long = 5000L,
    input: JSONObject? = null,
    node: JSONObject? = null,
    scroll: JSONObject? = null,
    global: JSONObject? = null,
    gesture: JSONObject? = null,
    intent: JSONObject? = null,
): ActionRequest =
    actionRequestJson(
        kind = kind,
        target = target,
        timeoutMs = timeoutMs,
        input = input,
        node = node,
        scroll = scroll,
        global = global,
        gesture = gesture,
        intent = intent,
    ).let(::decodeActionRequest)

internal fun actionRequestJson(
    kind: String,
    target: ActionTarget,
    timeoutMs: Long = 5000L,
    input: JSONObject? = null,
    node: JSONObject? = null,
    scroll: JSONObject? = null,
    global: JSONObject? = null,
    gesture: JSONObject? = null,
    intent: JSONObject? = null,
): JSONObject =
    JSONObject()
        .put("kind", kind)
        .put("target", target.toJson())
        .put("options", JSONObject().put("timeoutMs", timeoutMs))
        .apply {
            input?.let { put("input", it) }
            node?.let { put("node", it) }
            scroll?.let { put("scroll", it) }
            global?.let { put("global", it) }
            gesture?.let { put("gesture", it) }
            intent?.let { put("intent", it) }
        }

internal fun decodeActionRequest(params: JSONObject): ActionRequest =
    try {
        ActionRequestCodec.read(JsonReader.fromObject(params))
    } catch (error: RequestValidationException) {
        throw invalidRequest(error.message).apply { initCause(error) }
    }

internal fun observedWindowState(
    packageName: String? = "com.android.settings",
    activityName: String? = "SettingsActivity",
): ObservedWindowState =
    ObservedWindowState(
        packageName = packageName,
        activityName = activityName,
    )

internal fun foregroundObservation(
    packageName: String? = "com.android.settings",
    activityName: String? = "SettingsActivity",
    generation: Long = 0L,
    interactive: Boolean = true,
): ForegroundObservation =
    ForegroundObservation(
        state = observedWindowState(packageName = packageName, activityName = activityName),
        generation = generation,
        interactive = interactive,
    )

internal fun queuedForegroundObservationProvider(vararg observations: ForegroundObservation): ForegroundObservationProvider =
    QueueForegroundObservationProvider(observations.toList())

internal fun newActionPerformer(
    backend: RecordingActionBackend,
    clock: TestClock? = null,
    observationProvider: ForegroundObservationProvider? = null,
): ActionPerformer =
    ActionPerformer(
        backend = backend,
        observationProvider = observationProvider ?: AutoAdvanceForegroundObservationProvider(backend),
        nanoTimeProvider = clock?.let { it::nanoTime } ?: System::nanoTime,
        sleepProvider = clock?.let { it::sleep } ?: { Thread.sleep(it) },
    )

internal data class LaunchAppCall(
    val packageName: String,
)

internal data class OpenUrlCall(
    val url: String,
)

internal data class RecordedActionInvocation(
    val kind: String,
    val detail: String,
)

internal class RecordingActionBackend : ActionBackend {
    val operations = mutableListOf<String>()
    val invocations = mutableListOf<RecordedActionInvocation>()
    val launchAppCalls = mutableListOf<LaunchAppCall>()
    val openUrlCalls = mutableListOf<OpenUrlCall>()
    val queuedObservedStates = ArrayDeque<ObservedWindowState>()
    var nextError: ActionException? = null
    var typeStatus: ActionResultStatus = ActionResultStatus.Done
    var observedState: ObservedWindowState = observedWindowState()

    fun recordedExecutedKinds(): Set<String> = invocations.map(RecordedActionInvocation::kind).toSet()

    override fun tapHandle(
        snapshotId: Long,
        rid: String,
        longPress: Boolean,
    ): ActionResultStatus {
        maybeThrow()
        invocations +=
            RecordedActionInvocation(
                kind = if (longPress) "longTap" else "tap",
                detail = "handle:$snapshotId:$rid",
            )
        operations += "tap:$snapshotId:$rid:$longPress"
        return ActionResultStatus.Done
    }

    override fun tapCoordinates(
        x: Float,
        y: Float,
        longPress: Boolean,
        timeoutMs: Long,
    ): ActionResultStatus {
        maybeThrow()
        invocations +=
            RecordedActionInvocation(
                kind = if (longPress) "longTap" else "tap",
                detail = "coordinates:$x:$y:$timeoutMs",
            )
        operations += "coordinates:$x:$y:$longPress:$timeoutMs"
        return ActionResultStatus.Done
    }

    override fun type(
        snapshotId: Long,
        rid: String,
        input: ActionTextInput,
        timeoutMs: Long,
    ): ActionResultStatus {
        maybeThrow()
        invocations +=
            RecordedActionInvocation(
                kind = "type",
                detail = "handle:$snapshotId:$rid:${input.text}:${input.replace}:${input.submit}:${input.ensureFocused}:$timeoutMs",
            )
        operations +=
            "type:$snapshotId:$rid:${input.text}:${input.replace}:${input.submit}:${input.ensureFocused}:$timeoutMs"
        return typeStatus
    }

    override fun global(action: GlobalAction): ActionResultStatus {
        maybeThrow()
        invocations += RecordedActionInvocation(kind = "global", detail = action.wireName)
        operations += "global:${action.wireName}"
        return ActionResultStatus.Done
    }

    override fun launchApp(packageName: String): ActionResultStatus {
        maybeThrow()
        invocations +=
            RecordedActionInvocation(
                kind = "launchApp",
                detail = packageName,
            )
        launchAppCalls += LaunchAppCall(packageName = packageName)
        operations += "launch"
        return ActionResultStatus.Done
    }

    override fun openUrl(url: String): ActionResultStatus {
        maybeThrow()
        invocations +=
            RecordedActionInvocation(
                kind = "openUrl",
                detail = url,
            )
        openUrlCalls += OpenUrlCall(url = url)
        operations += "url"
        return ActionResultStatus.Done
    }

    override fun nodeAction(
        snapshotId: Long,
        rid: String,
        action: NodeAction,
    ): ActionResultStatus {
        maybeThrow()
        invocations +=
            RecordedActionInvocation(
                kind = "node",
                detail = "handle:$snapshotId:$rid:${action.wireName}",
            )
        operations += "node:$snapshotId:$rid:${action.wireName}"
        return ActionResultStatus.Done
    }

    override fun scroll(
        snapshotId: Long,
        rid: String,
        direction: ScrollDirection,
    ): ActionResultStatus {
        maybeThrow()
        invocations +=
            RecordedActionInvocation(
                kind = "scroll",
                detail = "handle:$snapshotId:$rid:${direction.wireName}",
            )
        operations += "scroll:$snapshotId:$rid:${direction.wireName}"
        return ActionResultStatus.Done
    }

    override fun gesture(
        direction: GestureDirection,
        timeoutMs: Long,
    ): ActionResultStatus {
        maybeThrow()
        invocations +=
            RecordedActionInvocation(
                kind = "gesture",
                detail = "${direction.wireName}:$timeoutMs",
            )
        operations += "gesture:${direction.wireName}:$timeoutMs"
        return ActionResultStatus.Done
    }

    fun nextObservedState(): ObservedWindowState {
        val nextState =
            if (queuedObservedStates.isEmpty()) {
                observedState
            } else {
                queuedObservedStates.removeFirst()
            }
        observedState = nextState
        return nextState
    }

    private fun maybeThrow() {
        nextError?.let { error ->
            nextError = null
            throw error
        }
    }
}

internal class TestClock {
    val sleeps = mutableListOf<Long>()
    private var nowMs: Long = 0L

    fun nanoTime(): Long = nowMs * 1_000_000L

    fun sleep(durationMs: Long) {
        sleeps += durationMs
        nowMs += durationMs
    }
}

private fun ActionTarget.toJson(): JSONObject =
    when (this) {
        is ActionTarget.Handle ->
            JSONObject()
                .put("kind", "handle")
                .put(
                    "handle",
                    JSONObject()
                        .put("snapshotId", snapshotId)
                        .put("rid", rid),
                )

        is ActionTarget.Coordinates ->
            JSONObject()
                .put("kind", "coordinates")
                .put("x", x)
                .put("y", y)

        ActionTarget.None -> JSONObject().put("kind", "none")
    }

private class AutoAdvanceForegroundObservationProvider(
    private val backend: RecordingActionBackend,
) : ForegroundObservationProvider {
    private var generation: Long = 0L

    override fun observe(): ForegroundObservation =
        ForegroundObservation(
            state = backend.nextObservedState(),
            generation = generation++,
        )
}

internal class QueueForegroundObservationProvider(
    observations: List<ForegroundObservation>,
) : ForegroundObservationProvider {
    private val queue = ArrayDeque(observations)
    private var latestObservation: ForegroundObservation = observations.lastOrNull() ?: ForegroundObservation()

    override fun observe(): ForegroundObservation {
        latestObservation =
            if (queue.isEmpty()) {
                latestObservation
            } else {
                queue.removeFirst()
            }
        return latestObservation
    }
}
