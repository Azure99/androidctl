package com.rainng.androidctl.agent.events

import android.view.accessibility.AccessibilityEvent

private const val NANOS_PER_MILLISECOND = 1_000_000L

internal class DeviceEventAggregator(
    private val buffer: DeviceEventBuffer,
    cooldownClockMsProvider: () -> Long = { System.nanoTime() / NANOS_PER_MILLISECOND },
    cooldownScheduler: CooldownScheduler = NoOpCooldownScheduler,
) {
    private var lastPackageName: String? = null
    private var lastImeState = ImeState(visible = false, windowId = null)
    private var lastRuntimeStatus: RuntimeStatusPayload? = null
    private var lastWindowKey: ForegroundContextKey? = null
    private var lastFocusKey: ForegroundContextKey? = null
    private val invalidationCooldown =
        InvalidationCooldown(
            sharedLock = this,
            buffer = buffer,
            cooldownClockMsProvider = cooldownClockMsProvider,
            cooldownScheduler = cooldownScheduler,
        )

    @Synchronized
    fun recordRuntimeStatus(payload: RuntimeStatusPayload) {
        if (payload == lastRuntimeStatus) {
            return
        }
        lastRuntimeStatus = payload
        buffer.publish(payload)
    }

    @Synchronized
    fun recordObservation(observation: AccessibilityObservation) {
        val packageName = observation.packageName?.takeIf(String::isNotBlank)
        val activityName = observation.activityName?.takeIf(String::isNotBlank)

        if (packageName != null && packageName != lastPackageName) {
            lastPackageName = packageName
            buffer.publish(
                PackageChangedPayload(
                    packageName = packageName,
                    activityName = activityName,
                ),
            )
        }

        if (observation.eventType in WINDOW_EVENT_TYPES) {
            publishWindowChangedIfNeeded(packageName, activityName, eventTypeName(observation.eventType))
        }

        if (observation.eventType in FOCUS_EVENT_TYPES) {
            publishFocusChangedIfNeeded(packageName, activityName, eventTypeName(observation.eventType))
        }

        if (observation.eventType in INVALIDATION_EVENT_TYPES) {
            invalidationCooldown.recordInvalidation(
                generation = observation.generation,
                packageName = packageName,
                reason = eventTypeName(observation.eventType),
            )
        }

        val imeState =
            ImeState(
                visible = observation.imeVisible,
                windowId = observation.imeWindowId,
            )
        if (imeState != lastImeState) {
            lastImeState = imeState
            buffer.publish(
                ImeChangedPayload(
                    visible = imeState.visible,
                    windowId = imeState.windowId,
                ),
            )
        }
    }

    @Synchronized
    fun cancelPendingWork() {
        invalidationCooldown.cancelPendingWork()
    }

    @Synchronized
    fun resetForAttachmentChange() {
        cancelPendingWork()
        lastPackageName = null
        lastImeState = ImeState(visible = false, windowId = null)
        lastRuntimeStatus = null
        lastWindowKey = null
        lastFocusKey = null
    }

    @Synchronized
    fun close() {
        invalidationCooldown.close()
    }

    private fun publishWindowChangedIfNeeded(
        packageName: String?,
        activityName: String?,
        reason: String,
    ) {
        val nextWindowKey = ForegroundContextKey(packageName = packageName, activityName = activityName)
        if (nextWindowKey == lastWindowKey) {
            return
        }

        lastWindowKey = nextWindowKey
        buffer.publish(
            WindowChangedPayload(
                packageName = packageName,
                activityName = activityName,
                reason = reason,
            ),
        )
    }

    private fun publishFocusChangedIfNeeded(
        packageName: String?,
        activityName: String?,
        reason: String,
    ) {
        val nextFocusKey = ForegroundContextKey(packageName = packageName, activityName = activityName)
        if (nextFocusKey == lastFocusKey) {
            return
        }

        lastFocusKey = nextFocusKey
        buffer.publish(
            FocusChangedPayload(
                packageName = packageName,
                activityName = activityName,
                reason = reason,
            ),
        )
    }

    companion object {
        private val WINDOW_EVENT_TYPES =
            setOf(
                AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
                AccessibilityEvent.TYPE_WINDOWS_CHANGED,
            )

        private val FOCUS_EVENT_TYPES =
            setOf(
                AccessibilityEvent.TYPE_VIEW_FOCUSED,
                AccessibilityEvent.TYPE_VIEW_ACCESSIBILITY_FOCUSED,
                AccessibilityEvent.TYPE_VIEW_ACCESSIBILITY_FOCUS_CLEARED,
                AccessibilityEvent.TYPE_VIEW_TEXT_SELECTION_CHANGED,
            )

        private val INVALIDATION_EVENT_TYPES =
            setOf(
                AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED,
                AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
                AccessibilityEvent.TYPE_WINDOWS_CHANGED,
                AccessibilityEvent.TYPE_VIEW_CLICKED,
                AccessibilityEvent.TYPE_VIEW_LONG_CLICKED,
                AccessibilityEvent.TYPE_VIEW_SELECTED,
                AccessibilityEvent.TYPE_VIEW_TEXT_CHANGED,
                AccessibilityEvent.TYPE_VIEW_SCROLLED,
            )

        internal fun eventTypeName(eventType: Int): String =
            when (eventType) {
                AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED -> "windowContentChanged"
                AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED -> "windowStateChanged"
                AccessibilityEvent.TYPE_WINDOWS_CHANGED -> "windowsChanged"
                AccessibilityEvent.TYPE_VIEW_CLICKED -> "viewClicked"
                AccessibilityEvent.TYPE_VIEW_LONG_CLICKED -> "viewLongClicked"
                AccessibilityEvent.TYPE_VIEW_SELECTED -> "viewSelected"
                AccessibilityEvent.TYPE_VIEW_TEXT_CHANGED -> "viewTextChanged"
                AccessibilityEvent.TYPE_VIEW_TEXT_SELECTION_CHANGED -> "textSelectionChanged"
                AccessibilityEvent.TYPE_VIEW_SCROLLED -> "viewScrolled"
                AccessibilityEvent.TYPE_VIEW_FOCUSED -> "viewFocused"
                AccessibilityEvent.TYPE_VIEW_ACCESSIBILITY_FOCUSED -> "accessibilityFocused"
                AccessibilityEvent.TYPE_VIEW_ACCESSIBILITY_FOCUS_CLEARED -> "accessibilityFocusCleared"
                else -> "event_$eventType"
            }
    }
}

private data class ForegroundContextKey(
    val packageName: String?,
    val activityName: String?,
)

private data class PendingInvalidation(
    val generation: Long,
    val packageName: String?,
    val reason: String,
)

private class InvalidationCooldown(
    private val sharedLock: Any,
    private val buffer: DeviceEventBuffer,
    private val cooldownClockMsProvider: () -> Long,
    private val cooldownScheduler: CooldownScheduler,
) {
    private var pendingInvalidation: PendingInvalidation? = null
    private var invalidationGeneration: Long? = null
    private var invalidationCooldownDeadlineMs = 0L
    private var pendingInvalidationTask: ScheduledTask? = null
    private var activeInvalidationTaskId: Long? = null
    private var nextInvalidationTaskId = 0L

    fun recordInvalidation(
        generation: Long,
        packageName: String?,
        reason: String,
    ) = synchronized(sharedLock) {
        val nowMs = cooldownClockMsProvider()
        if (invalidationGeneration != null && invalidationGeneration != generation) {
            flushPendingInvalidationLocked(
                nowMs = nowMs,
                resetCooldownState = true,
            )
        }

        val payload =
            PendingInvalidation(
                generation = generation,
                packageName = packageName,
                reason = reason,
            )

        if (invalidationGeneration == null) {
            publishInvalidation(payload, nowMs)
            return@synchronized
        }

        if (nowMs < invalidationCooldownDeadlineMs) {
            pendingInvalidation = payload
            schedulePendingInvalidationIfNeededLocked(nowMs)
            return@synchronized
        }

        pendingInvalidation?.let { stalePending ->
            pendingInvalidation = null
            cancelPendingInvalidationTaskLocked()
            publishInvalidation(stalePending, nowMs)
        }

        publishInvalidation(payload, nowMs)
    }

    fun cancelPendingWork() =
        synchronized(sharedLock) {
            cancelPendingInvalidationTaskLocked()
            pendingInvalidation = null
            invalidationGeneration = null
            invalidationCooldownDeadlineMs = 0L
        }

    fun close() =
        synchronized(sharedLock) {
            cancelPendingWork()
            cooldownScheduler.shutdown()
        }

    private fun withSharedLock(task: () -> Unit) {
        synchronized(sharedLock) {
            task()
        }
    }

    private fun flushPendingInvalidationLocked(
        nowMs: Long,
        resetCooldownState: Boolean,
    ) {
        cancelPendingInvalidationTaskLocked()
        pendingInvalidation?.let { payload ->
            publishInvalidation(payload, nowMs)
            pendingInvalidation = null
        }
        if (resetCooldownState) {
            invalidationGeneration = null
            invalidationCooldownDeadlineMs = 0L
        }
    }

    private fun publishInvalidation(
        payload: PendingInvalidation,
        nowMs: Long,
    ) {
        buffer.publish(
            SnapshotInvalidatedPayload(
                packageName = payload.packageName,
                reason = payload.reason,
            ),
        )
        invalidationGeneration = payload.generation
        invalidationCooldownDeadlineMs = nowMs + INVALIDATION_COOLDOWN_MS
    }

    private fun schedulePendingInvalidationIfNeededLocked(nowMs: Long) {
        if (pendingInvalidationTask != null) {
            return
        }

        val taskId = ++nextInvalidationTaskId
        activeInvalidationTaskId = taskId
        val delayMs = (invalidationCooldownDeadlineMs - nowMs).coerceAtLeast(0L)
        pendingInvalidationTask =
            cooldownScheduler.schedule(delayMs) {
                publishPendingInvalidationIfDue(taskId)
            }
    }

    private fun publishPendingInvalidationIfDue(taskId: Long) {
        withSharedLock {
            if (activeInvalidationTaskId != taskId) {
                return@withSharedLock
            }

            pendingInvalidationTask = null
            activeInvalidationTaskId = null

            val payload = pendingInvalidation ?: return@withSharedLock
            val nowMs = cooldownClockMsProvider()
            if (nowMs < invalidationCooldownDeadlineMs) {
                schedulePendingInvalidationIfNeededLocked(nowMs)
                return@withSharedLock
            }

            pendingInvalidation = null
            publishInvalidation(payload, nowMs)
        }
    }

    private fun cancelPendingInvalidationTaskLocked() {
        pendingInvalidationTask?.cancel()
        pendingInvalidationTask = null
        activeInvalidationTaskId = null
    }

    private companion object {
        private const val INVALIDATION_COOLDOWN_MS = 150L
    }
}
