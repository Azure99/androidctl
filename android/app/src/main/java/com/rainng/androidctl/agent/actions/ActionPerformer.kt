package com.rainng.androidctl.agent.actions

import com.rainng.androidctl.agent.runtime.ForegroundObservation
import com.rainng.androidctl.agent.runtime.ForegroundObservationProvider

internal class ActionPerformer(
    backend: ActionBackend,
    private val observationProvider: ForegroundObservationProvider,
    observationPolicyFactory: (ActionRequest, ForegroundObservation, String?) -> PostActionObservationPolicy =
        PostActionObservationPolicy::default,
    private val nanoTimeProvider: () -> Long = System::nanoTime,
    sleepProvider: (Long) -> Unit = { Thread.sleep(it) },
) {
    private val requestDispatcher = ActionRequestDispatcher(backend)
    private val postActionObserver =
        PostActionObserver(
            observationProvider = observationProvider,
            observationPolicyFactory = observationPolicyFactory,
            nanoTimeProvider = nanoTimeProvider,
            sleepProvider = sleepProvider,
        )

    fun perform(request: ActionRequest): ActionResult {
        val startedAt = nanoTimeProvider()
        val initialObservation = observationProvider.observe()
        val status = requestDispatcher.dispatch(request)
        val observedState = postActionObserver.observe(request = request, initialObservation = initialObservation)

        return ActionResult(
            actionId = ActionIds.nextActionId(),
            status = status,
            durationMs = (nanoTimeProvider() - startedAt) / NANOS_PER_MILLISECOND,
            resolvedTarget = request.target,
            observed = observedState.state,
        )
    }
}

private const val NANOS_PER_MILLISECOND = 1_000_000L
