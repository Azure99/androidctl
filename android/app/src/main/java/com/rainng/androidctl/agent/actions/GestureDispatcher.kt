package com.rainng.androidctl.agent.actions

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.graphics.Path
import android.os.Handler
import android.os.Looper
import com.rainng.androidctl.agent.errors.RpcErrorCode
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

internal interface GestureDispatcher {
    fun tapCoordinates(
        x: Float,
        y: Float,
        longPress: Boolean,
        timeoutMs: Long,
    ): ActionResultStatus

    fun gesture(
        direction: GestureDirection,
        timeoutMs: Long,
    ): ActionResultStatus
}

internal class AccessibilityGestureDispatcher(
    private val service: AccessibilityService,
    private val gestureCallbackHandlerProvider: () -> Handler? = { Handler(Looper.getMainLooper()) },
    private val tapGestureFactory: (Float, Float, Long) -> GestureDescription = ::defaultBuildTapGesture,
    private val swipeGestureFactory: (GestureDirection) -> GestureDescription = { direction ->
        defaultBuildSwipeGesture(service, direction)
    },
) : GestureDispatcher {
    override fun tapCoordinates(
        x: Float,
        y: Float,
        longPress: Boolean,
        timeoutMs: Long,
    ): ActionResultStatus =
        dispatchGesture(
            service = service,
            gestureCallbackHandlerProvider = gestureCallbackHandlerProvider,
            gesture = tapGestureFactory(x, y, if (longPress) LONG_PRESS_TAP_DURATION_MS else TAP_DURATION_MS),
            timeoutMs = timeoutMs,
        )

    override fun gesture(
        direction: GestureDirection,
        timeoutMs: Long,
    ): ActionResultStatus =
        dispatchGesture(
            service = service,
            gestureCallbackHandlerProvider = gestureCallbackHandlerProvider,
            gesture = swipeGestureFactory(direction),
            timeoutMs = timeoutMs,
        )
}

private fun defaultBuildTapGesture(
    x: Float,
    y: Float,
    durationMs: Long,
): GestureDescription {
    val path = Path().apply { moveTo(x, y) }
    return GestureDescription
        .Builder()
        .addStroke(GestureDescription.StrokeDescription(path, 0, durationMs))
        .build()
}

internal fun defaultBuildSwipeGesture(
    service: AccessibilityService,
    direction: GestureDirection,
): GestureDescription {
    val width =
        service.resources.displayMetrics.widthPixels
            .toFloat()
    val height =
        service.resources.displayMetrics.heightPixels
            .toFloat()
    val path = Path()
    when (direction) {
        GestureDirection.Down -> {
            path.moveTo(width * SWIPE_MIDPOINT_FRACTION, height * SWIPE_START_FRACTION)
            path.lineTo(width * SWIPE_MIDPOINT_FRACTION, height * SWIPE_END_FRACTION)
        }
        GestureDirection.Up -> {
            path.moveTo(width * SWIPE_MIDPOINT_FRACTION, height * SWIPE_END_FRACTION)
            path.lineTo(width * SWIPE_MIDPOINT_FRACTION, height * SWIPE_START_FRACTION)
        }
        GestureDirection.Left -> {
            path.moveTo(width * SWIPE_END_FRACTION, height * SWIPE_MIDPOINT_FRACTION)
            path.lineTo(width * SWIPE_START_FRACTION, height * SWIPE_MIDPOINT_FRACTION)
        }
        GestureDirection.Right -> {
            path.moveTo(width * SWIPE_START_FRACTION, height * SWIPE_MIDPOINT_FRACTION)
            path.lineTo(width * SWIPE_END_FRACTION, height * SWIPE_MIDPOINT_FRACTION)
        }
    }
    return GestureDescription
        .Builder()
        .addStroke(GestureDescription.StrokeDescription(path, 0, SWIPE_DURATION_MS))
        .build()
}

internal fun dispatchGesture(
    service: AccessibilityService,
    gestureCallbackHandlerProvider: () -> Handler?,
    gesture: GestureDescription,
    timeoutMs: Long,
): ActionResultStatus {
    val latch = CountDownLatch(1)
    var completed = false
    val accepted =
        service.dispatchGesture(
            gesture,
            object : AccessibilityService.GestureResultCallback() {
                override fun onCompleted(gestureDescription: GestureDescription?) {
                    completed = true
                    latch.countDown()
                }

                override fun onCancelled(gestureDescription: GestureDescription?) {
                    latch.countDown()
                }
            },
            gestureCallbackHandlerProvider(),
        )
    if (!accepted) {
        throw ActionException(
            code = RpcErrorCode.ACTION_FAILED,
            message = "gesture dispatch was rejected",
            retryable = true,
        )
    }
    if (!latch.await(timeoutMs, TimeUnit.MILLISECONDS) || !completed) {
        throw ActionException(
            code = RpcErrorCode.ACTION_TIMEOUT,
            message = "gesture did not complete before timeout",
            retryable = true,
        )
    }
    return ActionResultStatus.Done
}

private const val TAP_DURATION_MS = 80L
private const val LONG_PRESS_TAP_DURATION_MS = 700L
private const val SWIPE_DURATION_MS = 300L
private const val SWIPE_START_FRACTION = 0.25f
private const val SWIPE_MIDPOINT_FRACTION = 0.5f
private const val SWIPE_END_FRACTION = 0.75f
