package com.rainng.androidctl.agent.actions

import com.rainng.androidctl.agent.errors.RpcErrorCode
import com.rainng.androidctl.agent.runtime.ForegroundObservation
import com.rainng.androidctl.agent.runtime.ObservedWindowState
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ActionPerformerTest {
    @Test
    fun tapHandleUsesBackend() {
        val backend = RecordingActionBackend()
        val payload =
            newActionPerformer(backend).perform(
                actionRequest(
                    kind = "tap",
                    target = ActionTarget.Handle(snapshotId = 42L, rid = "w1:0.1"),
                    timeoutMs = 5000L,
                    input = null,
                    node = null,
                    scroll = null,
                    global = null,
                    gesture = null,
                    intent = null,
                ),
            )

        assertEquals("tap:42:w1:0.1:false", backend.operations.single())
        assertEquals(ActionResultStatus.Done, payload.status)
    }

    @Test
    fun typeUsesBackendWithInputFlags() {
        val backend = RecordingActionBackend()
        val payload =
            newActionPerformer(backend).perform(
                actionRequest(
                    kind = "type",
                    target = ActionTarget.Handle(snapshotId = 7L, rid = "w1:0.5"),
                    timeoutMs = 8000L,
                    input =
                        JSONObject()
                            .put("text", "wifi")
                            .put("replace", false)
                            .put("submit", true)
                            .put("ensureFocused", false),
                    node = null,
                    scroll = null,
                    global = null,
                    gesture = null,
                    intent = null,
                ),
            )

        assertEquals("type:7:w1:0.5:wifi:false:true:false:8000", backend.operations.single())
        assertEquals(ActionResultStatus.Done, payload.status)
    }

    @Test
    fun typeSubmitFlagRemainsCompatibleForDeviceContract() {
        val backend = RecordingActionBackend()

        newActionPerformer(backend).perform(
            actionRequest(
                kind = "type",
                target = ActionTarget.Handle(snapshotId = 8L, rid = "w1:0.7"),
                timeoutMs = 8000L,
                input =
                    JSONObject()
                        .put("text", "wifi")
                        .put("replace", true)
                        .put("submit", true)
                        .put("ensureFocused", true),
                node = null,
                scroll = null,
                global = null,
                gesture = null,
                intent = null,
            ),
        )

        assertEquals("type:8:w1:0.7:wifi:true:true:true:8000", backend.operations.single())
    }

    @Test
    fun globalSupportsAllDocumentedActions() {
        val backend = RecordingActionBackend()
        val performer = newActionPerformer(backend)

        listOf("back", "home", "recents", "notifications").forEach { action ->
            performer.perform(
                actionRequest(
                    kind = "global",
                    target = ActionTarget.None,
                    timeoutMs = 5000L,
                    input = null,
                    node = null,
                    scroll = null,
                    global = JSONObject().put("action", action),
                    gesture = null,
                    intent = null,
                ),
            )
        }

        assertEquals(
            listOf("global:back", "global:home", "global:recents", "global:notifications"),
            backend.operations,
        )
    }

    @Test
    fun coordinatesTapUsesBackendCoordinatesPath() {
        val backend = RecordingActionBackend()
        newActionPerformer(backend).perform(
            actionRequest(
                kind = "tap",
                target = ActionTarget.Coordinates(x = 540f, y = 1200f),
                timeoutMs = 3000L,
                input = null,
                node = null,
                scroll = null,
                global = null,
                gesture = null,
                intent = null,
            ),
        )

        assertEquals("coordinates:540.0:1200.0:false:3000", backend.operations.single())
    }

    @Test
    fun nodeActionsUseBackend() {
        val backend = RecordingActionBackend()
        val performer = newActionPerformer(backend)

        listOf("focus", "submit", "dismiss", "showOnScreen").forEach { action ->
            performer.perform(
                actionRequest(
                    kind = "node",
                    target = ActionTarget.Handle(snapshotId = 11L, rid = "w1:0.9"),
                    timeoutMs = 5000L,
                    input = null,
                    node = JSONObject().put("action", action),
                    scroll = null,
                    global = null,
                    gesture = null,
                    intent = null,
                ),
            )
        }

        assertEquals(
            listOf(
                "node:11:w1:0.9:focus",
                "node:11:w1:0.9:submit",
                "node:11:w1:0.9:dismiss",
                "node:11:w1:0.9:showOnScreen",
            ),
            backend.operations,
        )
    }

    @Test
    fun launchAppUsesBackend() {
        val backend = RecordingActionBackend()
        newActionPerformer(backend).perform(
            actionRequest(
                kind = "launchApp",
                target = ActionTarget.None,
                timeoutMs = 5000L,
                input = null,
                node = null,
                scroll = null,
                global = null,
                gesture = null,
                intent = JSONObject().put("packageName", "com.android.settings"),
            ),
        )

        assertEquals(LaunchAppCall("com.android.settings"), backend.launchAppCalls.single())
    }

    @Test
    fun openUrlUsesBackend() {
        val backend = RecordingActionBackend()
        newActionPerformer(backend).perform(
            actionRequest(
                kind = "openUrl",
                target = ActionTarget.None,
                timeoutMs = 5000L,
                input = null,
                node = null,
                scroll = null,
                global = null,
                gesture = null,
                intent = JSONObject().put("url", "https://example.com"),
            ),
        )

        assertEquals(OpenUrlCall("https://example.com"), backend.openUrlCalls.single())
    }

    @Test
    fun staleHandleIsSurfaced() {
        val backend = RecordingActionBackend()
        backend.nextError =
            ActionException(
                code = RpcErrorCode.STALE_TARGET,
                message = "snapshot handle is stale",
                retryable = true,
            )

        try {
            newActionPerformer(backend).perform(
                actionRequest(
                    kind = "tap",
                    target = ActionTarget.Handle(snapshotId = 1L, rid = "w1:0.1"),
                    timeoutMs = 5000L,
                    input = null,
                    node = null,
                    scroll = null,
                    global = null,
                    gesture = null,
                    intent = null,
                ),
            )
        } catch (error: ActionException) {
            assertEquals(RpcErrorCode.STALE_TARGET, error.code)
            assertTrue(error.retryable)
            return
        }

        throw AssertionError("expected ActionException")
    }

    @Test
    fun targetNotActionableIsSurfaced() {
        val backend = RecordingActionBackend()
        backend.nextError =
            ActionException(
                code = RpcErrorCode.TARGET_NOT_ACTIONABLE,
                message = "target is not editable",
                retryable = false,
            )

        try {
            newActionPerformer(backend).perform(
                actionRequest(
                    kind = "type",
                    target = ActionTarget.Handle(snapshotId = 1L, rid = "w1:0.5"),
                    timeoutMs = 5000L,
                    input = typeInput("wifi"),
                    node = null,
                    scroll = null,
                    global = null,
                    gesture = null,
                    intent = null,
                ),
            )
        } catch (error: ActionException) {
            assertEquals(RpcErrorCode.TARGET_NOT_ACTIONABLE, error.code)
            assertEquals(false, error.retryable)
            return
        }

        throw AssertionError("expected ActionException")
    }

    @Test
    fun timeoutIsSurfaced() {
        val backend = RecordingActionBackend()
        backend.nextError =
            ActionException(
                code = RpcErrorCode.ACTION_TIMEOUT,
                message = "gesture timed out",
                retryable = true,
            )

        try {
            newActionPerformer(backend).perform(
                actionRequest(
                    kind = "gesture",
                    target = ActionTarget.None,
                    timeoutMs = 100L,
                    input = null,
                    node = null,
                    scroll = null,
                    global = null,
                    gesture = JSONObject().put("direction", "down"),
                    intent = null,
                ),
            )
        } catch (error: ActionException) {
            assertEquals(RpcErrorCode.ACTION_TIMEOUT, error.code)
            assertTrue(error.retryable)
            return
        }

        throw AssertionError("expected ActionException")
    }

    @Test
    fun typeAllowsWhitespaceOnlyText() {
        val backend = RecordingActionBackend()

        newActionPerformer(backend).perform(
            actionRequest(
                kind = "type",
                target = ActionTarget.Handle(snapshotId = 1L, rid = "w1:0.5"),
                input = typeInput("   "),
            ),
        )

        assertEquals("type:1:w1:0.5:   :true:false:true:5000", backend.operations.single())
    }

    @Test
    fun typeAllowsEmptyTextWhenReplaceIsTrue() {
        val backend = RecordingActionBackend()

        newActionPerformer(backend).perform(
            actionRequest(
                kind = "type",
                target = ActionTarget.Handle(snapshotId = 1L, rid = "w1:0.5"),
                input =
                    typeInput("", replace = true),
            ),
        )

        assertEquals("type:1:w1:0.5::true:false:true:5000", backend.operations.single())
    }

    @Test
    fun longTapSuccessPopulatesStatusResolvedTargetAndObservedFields() {
        val backend = RecordingActionBackend()

        val payload =
            newActionPerformer(backend).perform(
                actionRequest(
                    kind = "longTap",
                    target = ActionTarget.Coordinates(x = 12f, y = 34f),
                    timeoutMs = 2500L,
                ),
            )

        assertEquals(ActionResultStatus.Done, payload.status)
        assertEquals(ActionTarget.Coordinates(x = 12f, y = 34f), payload.resolvedTarget)
        assertEquals("com.android.settings", payload.observed.packageName)
        assertEquals("SettingsActivity", payload.observed.activityName)
        assertEquals("coordinates:12.0:34.0:true:2500", backend.operations.single())
    }

    @Test
    fun scrollSuccessPopulatesStatusResolvedTargetAndObservedFields() {
        val backend = RecordingActionBackend()

        val payload =
            newActionPerformer(backend).perform(
                actionRequest(
                    kind = "scroll",
                    target = ActionTarget.Handle(snapshotId = 3L, rid = "w1:0.4"),
                    scroll = JSONObject().put("direction", "down"),
                ),
            )

        assertEquals(ActionResultStatus.Done, payload.status)
        assertEquals(ActionTarget.Handle(snapshotId = 3L, rid = "w1:0.4"), payload.resolvedTarget)
        assertEquals("com.android.settings", payload.observed.packageName)
        assertEquals("SettingsActivity", payload.observed.activityName)
        assertEquals("scroll:3:w1:0.4:down", backend.operations.single())
    }

    @Test
    fun gestureSuccessPopulatesStatusResolvedTargetAndObservedFields() {
        val backend = RecordingActionBackend()

        val payload =
            newActionPerformer(backend).perform(
                actionRequest(
                    kind = "gesture",
                    target = ActionTarget.None,
                    timeoutMs = 1500L,
                    gesture = JSONObject().put("direction", "left"),
                ),
            )

        assertEquals(ActionResultStatus.Done, payload.status)
        assertEquals(ActionTarget.None, payload.resolvedTarget)
        assertEquals("com.android.settings", payload.observed.packageName)
        assertEquals("SettingsActivity", payload.observed.activityName)
        assertEquals("gesture:left:1500", backend.operations.single())
    }

    @Test
    fun tapWaitsForGenerationAdvanceBeforeSamplingObservedState() {
        val backend = RecordingActionBackend()
        val clock = TestClock()
        val performer =
            newActionPerformer(
                backend = backend,
                clock = clock,
                observationProvider =
                    QueueForegroundObservationProvider(
                        listOf(
                            ForegroundObservation(
                                state =
                                    ObservedWindowState(
                                        packageName = "com.android.settings",
                                        activityName = "SettingsActivity",
                                    ),
                                generation = 4L,
                            ),
                            ForegroundObservation(
                                state =
                                    ObservedWindowState(
                                        packageName = "com.android.settings",
                                        activityName = "SettingsActivity",
                                    ),
                                generation = 4L,
                            ),
                            ForegroundObservation(
                                state =
                                    ObservedWindowState(
                                        packageName = "com.android.settings",
                                        activityName = null,
                                    ),
                                generation = 5L,
                            ),
                        ),
                    ),
            )

        val payload =
            performer.perform(
                actionRequest(
                    kind = "tap",
                    target = ActionTarget.Handle(snapshotId = 2L, rid = "w1:0.2"),
                ),
            )

        val observed = payload.observed
        assertEquals("com.android.settings", observed.packageName)
        assertEquals(null, observed.activityName)
        assertEquals(listOf(50L), clock.sleeps)
    }

    @Test
    fun nodeSubmitWaitsForGenerationAdvanceBeforeReturningObservedState() {
        val backend = RecordingActionBackend()
        val clock = TestClock()
        val performer =
            newActionPerformer(
                backend = backend,
                clock = clock,
                observationProvider =
                    QueueForegroundObservationProvider(
                        listOf(
                            ForegroundObservation(
                                state = ObservedWindowState(packageName = "com.android.settings", activityName = "Editor"),
                                generation = 1L,
                            ),
                            ForegroundObservation(
                                state = ObservedWindowState(packageName = "com.android.settings", activityName = "Editor"),
                                generation = 1L,
                            ),
                            ForegroundObservation(
                                state = ObservedWindowState(packageName = "com.android.settings", activityName = null),
                                generation = 2L,
                            ),
                        ),
                    ),
            )

        val payload =
            performer.perform(
                actionRequest(
                    kind = "node",
                    target = ActionTarget.Handle(snapshotId = 11L, rid = "w1:0.9"),
                    node = JSONObject().put("action", "submit"),
                ),
            )

        val observed = payload.observed
        assertEquals(null, observed.activityName)
        assertEquals(listOf(50L), clock.sleeps)
    }

    @Test
    fun typeWithoutSubmitDoesNotWaitForGenerationAdvanceWhenSamplingObservedState() {
        val backend = RecordingActionBackend()
        val clock = TestClock()
        val performer =
            newActionPerformer(
                backend = backend,
                clock = clock,
                observationProvider =
                    QueueForegroundObservationProvider(
                        listOf(
                            ForegroundObservation(
                                state = ObservedWindowState(packageName = "com.android.settings", activityName = "Editor"),
                                generation = 10L,
                            ),
                            ForegroundObservation(
                                state = ObservedWindowState(packageName = "com.android.settings", activityName = "Editor"),
                                generation = 10L,
                            ),
                        ),
                    ),
            )

        val payload =
            performer.perform(
                actionRequest(
                    kind = "type",
                    target = ActionTarget.Handle(snapshotId = 1L, rid = "w1:0.5"),
                    input = typeInput("wifi"),
                ),
            )

        assertEquals("Editor", payload.observed.activityName)
        assertTrue(clock.sleeps.isEmpty())
    }

    @Test
    fun typeSubmitWaitsForGenerationAdvanceBeforeReturningObservedState() {
        val backend = RecordingActionBackend()
        val clock = TestClock()
        val performer =
            newActionPerformer(
                backend = backend,
                clock = clock,
                observationProvider =
                    QueueForegroundObservationProvider(
                        listOf(
                            ForegroundObservation(
                                state = ObservedWindowState(packageName = "com.android.settings", activityName = "Editor"),
                                generation = 20L,
                            ),
                            ForegroundObservation(
                                state = ObservedWindowState(packageName = "com.android.settings", activityName = "Editor"),
                                generation = 20L,
                            ),
                            ForegroundObservation(
                                state = ObservedWindowState(packageName = "com.android.settings", activityName = null),
                                generation = 21L,
                            ),
                        ),
                    ),
            )

        val payload =
            performer.perform(
                actionRequest(
                    kind = "type",
                    target = ActionTarget.Handle(snapshotId = 1L, rid = "w1:0.5"),
                    input =
                        typeInput("wifi", submit = true),
                ),
            )

        val observed = payload.observed
        assertEquals(null, observed.activityName)
        assertEquals(listOf(50L), clock.sleeps)
    }

    @Test
    fun typePropagatesPartialStatusFromBackend() {
        val backend =
            RecordingActionBackend().apply {
                typeStatus = ActionResultStatus.Partial
            }

        val payload =
            newActionPerformer(backend).perform(
                actionRequest(
                    kind = "type",
                    target = ActionTarget.Handle(snapshotId = 1L, rid = "w1:0.5"),
                    input = typeInput("wifi"),
                ),
            )

        assertEquals(ActionResultStatus.Partial, payload.status)
    }

    @Test
    fun observationTimeoutReturnsLatestBestEffortState() {
        val backend = RecordingActionBackend()
        val clock = TestClock()
        val performer =
            newActionPerformer(
                backend = backend,
                clock = clock,
                observationProvider =
                    QueueForegroundObservationProvider(
                        listOf(
                            ForegroundObservation(
                                state = ObservedWindowState(packageName = "com.android.settings", activityName = "SettingsActivity"),
                                generation = 7L,
                            ),
                            ForegroundObservation(
                                state = ObservedWindowState(packageName = "com.android.settings", activityName = "SettingsActivity"),
                                generation = 7L,
                            ),
                            ForegroundObservation(
                                state = ObservedWindowState(packageName = "com.android.settings", activityName = null),
                                generation = 7L,
                            ),
                        ),
                    ),
            )

        val payload =
            performer.perform(
                actionRequest(
                    kind = "tap",
                    target = ActionTarget.Handle(snapshotId = 1L, rid = "w1:0.1"),
                ),
            )

        val observed = payload.observed
        assertEquals("com.android.settings", observed.packageName)
        assertEquals(null, observed.activityName)
        assertEquals(listOf(50L, 50L, 50L, 50L, 50L), clock.sleeps)
    }

    @Test
    fun observedStatePreservesNullPackageNameAsExplicitJsonNull() {
        val payload =
            newActionPerformer(
                backend = RecordingActionBackend(),
                observationProvider =
                    QueueForegroundObservationProvider(
                        listOf(
                            ForegroundObservation(
                                state = ObservedWindowState(packageName = null, activityName = null),
                                generation = 1L,
                            ),
                            ForegroundObservation(
                                state = ObservedWindowState(packageName = null, activityName = null),
                                generation = 1L,
                            ),
                        ),
                    ),
            ).perform(
                actionRequest(
                    kind = "type",
                    target = ActionTarget.Handle(snapshotId = 1L, rid = "w1:0.5"),
                    input = typeInput("wifi"),
                ),
            )

        val observed = payload.observed
        assertEquals(null, observed.packageName)
        assertEquals(null, observed.activityName)
    }

    private fun typeInput(
        text: String,
        replace: Boolean = true,
        submit: Boolean = false,
        ensureFocused: Boolean = true,
    ): JSONObject =
        JSONObject()
            .put("text", text)
            .put("replace", replace)
            .put("submit", submit)
            .put("ensureFocused", ensureFocused)

    @Test
    fun launchAppWaitsForTargetPackageObservation() {
        val backend =
            RecordingActionBackend().apply {
                observedState =
                    ObservedWindowState(
                        packageName = "com.android.launcher3",
                        activityName = "Launcher",
                    )
                queuedObservedStates +=
                    listOf(
                        ObservedWindowState(
                            packageName = "com.android.launcher3",
                            activityName = "Launcher",
                        ),
                        ObservedWindowState(
                            packageName = "com.android.launcher3",
                            activityName = "Launcher",
                        ),
                        ObservedWindowState(
                            packageName = "com.android.settings",
                            activityName = "SettingsActivity",
                        ),
                    )
            }
        val clock = TestClock()
        val performer =
            newActionPerformer(
                backend = backend,
                clock = clock,
            )

        val payload =
            performer.perform(
                actionRequest(
                    kind = "launchApp",
                    target = ActionTarget.None,
                    timeoutMs = 5000L,
                    intent = JSONObject().put("packageName", "com.android.settings"),
                ),
            )

        assertEquals("com.android.settings", payload.observed.packageName)
        assertEquals("SettingsActivity", payload.observed.activityName)
        assertEquals(listOf(50L), clock.sleeps)
    }

    @Test
    fun launchAppUsesSharedObservationMinimumBudget() {
        val backend =
            RecordingActionBackend().apply {
                observedState =
                    ObservedWindowState(
                        packageName = "com.android.launcher3",
                        activityName = "Launcher",
                    )
            }
        val clock = TestClock()

        newActionPerformer(
            backend = backend,
            clock = clock,
        ).perform(
            actionRequest(
                kind = "launchApp",
                target = ActionTarget.None,
                timeoutMs = 100L,
                intent = JSONObject().put("packageName", "com.android.settings"),
            ),
        )

        assertEquals(listOf(50L, 50L, 50L, 50L), clock.sleeps)
    }

    @Test
    fun launchAppUsesSharedObservationDivisorForMidrangeTimeout() {
        val backend =
            RecordingActionBackend().apply {
                observedState =
                    ObservedWindowState(
                        packageName = "com.android.launcher3",
                        activityName = "Launcher",
                    )
            }
        val clock = TestClock()

        newActionPerformer(
            backend = backend,
            clock = clock,
        ).perform(
            actionRequest(
                kind = "launchApp",
                target = ActionTarget.None,
                timeoutMs = 1000L,
                intent = JSONObject().put("packageName", "com.android.settings"),
            ),
        )

        assertEquals(listOf(50L, 50L, 50L, 50L, 50L), clock.sleeps)
    }

    @Test
    fun launchAppUsesSharedObservationMaximumBudget() {
        val backend =
            RecordingActionBackend().apply {
                observedState =
                    ObservedWindowState(
                        packageName = "com.android.launcher3",
                        activityName = "Launcher",
                    )
            }
        val clock = TestClock()

        newActionPerformer(
            backend = backend,
            clock = clock,
        ).perform(
            actionRequest(
                kind = "launchApp",
                target = ActionTarget.None,
                timeoutMs = 10_000L,
                intent = JSONObject().put("packageName", "com.android.settings"),
            ),
        )

        assertEquals(List(24) { 50L }, clock.sleeps)
    }

    @Test
    fun openUrlWithoutExplicitPackageWaitsForPackageChange() {
        val backend =
            RecordingActionBackend().apply {
                observedState =
                    ObservedWindowState(
                        packageName = "com.android.launcher3",
                        activityName = "Launcher",
                    )
                queuedObservedStates +=
                    listOf(
                        ObservedWindowState(
                            packageName = "com.android.launcher3",
                            activityName = "Launcher",
                        ),
                        ObservedWindowState(
                            packageName = "com.android.launcher3",
                            activityName = "Launcher",
                        ),
                        ObservedWindowState(
                            packageName = "com.android.chrome",
                            activityName = "Main",
                        ),
                    )
            }
        val clock = TestClock()
        val performer =
            newActionPerformer(
                backend = backend,
                clock = clock,
            )

        val payload =
            performer.perform(
                actionRequest(
                    kind = "openUrl",
                    target = ActionTarget.None,
                    timeoutMs = 5000L,
                    intent = JSONObject().put("url", "https://example.com"),
                ),
            )

        assertEquals("com.android.chrome", payload.observed.packageName)
        assertEquals("Main", payload.observed.activityName)
        assertEquals(listOf(50L), clock.sleeps)
    }

    @Test
    fun openUrlWithoutExplicitPackageUsesFastPathForWebTargetFromAppForeground() {
        val backend = RecordingActionBackend()
        val clock = TestClock()
        val observationProvider =
            QueueForegroundObservationProvider(
                listOf(
                    ForegroundObservation(
                        state =
                            ObservedWindowState(
                                packageName = "com.android.settings",
                                activityName = "SettingsActivity",
                            ),
                        generation = 0L,
                    ),
                    ForegroundObservation(
                        state =
                            ObservedWindowState(
                                packageName = "com.android.settings",
                                activityName = "SettingsActivity",
                            ),
                        generation = 1L,
                    ),
                    ForegroundObservation(
                        state =
                            ObservedWindowState(
                                packageName = "com.android.chrome",
                                activityName = "Main",
                            ),
                        generation = 2L,
                    ),
                ),
            )
        val performer =
            newActionPerformer(
                backend = backend,
                clock = clock,
                observationProvider = observationProvider,
            )

        val payload =
            performer.perform(
                actionRequest(
                    kind = "openUrl",
                    target = ActionTarget.None,
                    timeoutMs = 5000L,
                    intent = JSONObject().put("url", "https://example.com"),
                ),
            )

        assertEquals("com.android.settings", payload.observed.packageName)
        assertEquals("SettingsActivity", payload.observed.activityName)
        assertEquals(emptyList<Long>(), clock.sleeps)
    }

    @Test
    fun openUrlFastPathForWebTargetDoesNotRequireGenerationAdvance() {
        val backend = RecordingActionBackend()
        val clock = TestClock()
        val observationProvider =
            QueueForegroundObservationProvider(
                listOf(
                    ForegroundObservation(
                        state =
                            ObservedWindowState(
                                packageName = "com.android.settings",
                                activityName = "SettingsActivity",
                            ),
                        generation = 0L,
                    ),
                    ForegroundObservation(
                        state =
                            ObservedWindowState(
                                packageName = "com.android.settings",
                                activityName = "SettingsActivity",
                            ),
                        generation = 0L,
                    ),
                    ForegroundObservation(
                        state =
                            ObservedWindowState(
                                packageName = "com.android.chrome",
                                activityName = "Main",
                            ),
                        generation = 1L,
                    ),
                ),
            )
        val performer =
            newActionPerformer(
                backend = backend,
                clock = clock,
                observationProvider = observationProvider,
            )

        val payload =
            performer.perform(
                actionRequest(
                    kind = "openUrl",
                    target = ActionTarget.None,
                    timeoutMs = 5000L,
                    intent = JSONObject().put("url", "https://example.com"),
                ),
            )

        assertEquals("com.android.settings", payload.observed.packageName)
        assertEquals("SettingsActivity", payload.observed.activityName)
        assertEquals(emptyList<Long>(), clock.sleeps)
    }

    @Test
    fun openUrlWithoutExplicitPackageWaitsForNonWebTargetFromAppForeground() {
        val backend = RecordingActionBackend()
        val clock = TestClock()
        val observationProvider =
            QueueForegroundObservationProvider(
                listOf(
                    ForegroundObservation(
                        state =
                            ObservedWindowState(
                                packageName = "com.android.settings",
                                activityName = "SettingsActivity",
                            ),
                        generation = 0L,
                    ),
                    ForegroundObservation(
                        state =
                            ObservedWindowState(
                                packageName = "com.android.settings",
                                activityName = "SettingsActivity",
                            ),
                        generation = 1L,
                    ),
                    ForegroundObservation(
                        state =
                            ObservedWindowState(
                                packageName = "com.google.android.apps.messaging",
                                activityName = "ComposeActivity",
                            ),
                        generation = 2L,
                    ),
                ),
            )
        val performer =
            newActionPerformer(
                backend = backend,
                clock = clock,
                observationProvider = observationProvider,
            )

        val payload =
            performer.perform(
                actionRequest(
                    kind = "openUrl",
                    target = ActionTarget.None,
                    timeoutMs = 5000L,
                    intent = JSONObject().put("url", "smsto:10086?body=hi"),
                ),
            )

        assertEquals("com.google.android.apps.messaging", payload.observed.packageName)
        assertEquals("ComposeActivity", payload.observed.activityName)
        assertEquals(listOf(50L), clock.sleeps)
    }

    @Test
    fun openUrlWithoutPackageWaitsForFirstPackageChangeInsteadOfExpectedPackage() {
        val backend =
            RecordingActionBackend().apply {
                observedState =
                    ObservedWindowState(
                        packageName = "com.android.launcher3",
                        activityName = "Launcher",
                    )
                queuedObservedStates +=
                    listOf(
                        ObservedWindowState(
                            packageName = "com.android.launcher3",
                            activityName = "Launcher",
                        ),
                        ObservedWindowState(
                            packageName = "com.android.browser",
                            activityName = "BrowserActivity",
                        ),
                        ObservedWindowState(
                            packageName = "com.android.chrome",
                            activityName = "Main",
                        ),
                    )
            }
        val clock = TestClock()
        val performer =
            newActionPerformer(
                backend = backend,
                clock = clock,
            )

        val payload =
            performer.perform(
                actionRequest(
                    kind = "openUrl",
                    target = ActionTarget.None,
                    timeoutMs = 5000L,
                    intent = JSONObject().put("url", "smsto:10086?body=hi"),
                ),
            )

        assertEquals("com.android.browser", payload.observed.packageName)
        assertEquals("BrowserActivity", payload.observed.activityName)
        assertEquals(emptyList<Long>(), clock.sleeps)
    }
}
