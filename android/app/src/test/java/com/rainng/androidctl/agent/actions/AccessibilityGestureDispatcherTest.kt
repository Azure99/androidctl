package com.rainng.androidctl.agent.actions

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import com.rainng.androidctl.agent.errors.RpcErrorCode
import com.rainng.androidctl.agent.testsupport.assertActionException
import com.rainng.androidctl.agent.testsupport.mockService
import org.junit.Assert.assertEquals
import org.junit.Test
import org.mockito.ArgumentMatchers.any
import org.mockito.ArgumentMatchers.isNull
import org.mockito.Mockito.doAnswer
import org.mockito.Mockito.mock

class AccessibilityGestureDispatcherTest {
    @Test
    fun tapCoordinatesUsesTapGestureAndReportsDone() {
        var capturedDuration = -1L
        val dispatcher =
            newDispatcher(
                tapGestureFactory = { _, _, durationMs ->
                    capturedDuration = durationMs
                    mock(GestureDescription::class.java)
                },
            )

        val status = dispatcher.tapCoordinates(10f, 20f, longPress = true, timeoutMs = 10L)

        assertEquals(ActionResultStatus.Done, status)
        assertEquals(700L, capturedDuration)
    }

    @Test
    fun gestureFailsWhenDispatchIsRejected() {
        val dispatcher =
            newDispatcher(
                dispatchGestureBehavior = { _, _ -> false },
            )

        assertActionException(
            expectedCode = RpcErrorCode.ACTION_FAILED,
            expectedMessage = "gesture dispatch was rejected",
            expectedRetryable = true,
        ) {
            dispatcher.gesture(GestureDirection.Down, timeoutMs = 10L)
        }
    }

    @Test
    fun gestureFailsWhenDispatchIsCancelled() {
        val dispatcher =
            newDispatcher(
                dispatchGestureBehavior = { _, callback ->
                    callback.onCancelled(null)
                    true
                },
            )

        assertActionException(
            expectedCode = RpcErrorCode.ACTION_TIMEOUT,
            expectedMessage = "gesture did not complete before timeout",
            expectedRetryable = true,
        ) {
            dispatcher.gesture(GestureDirection.Down, timeoutMs = 10L)
        }
    }

    @Test
    fun gestureFailsWhenCompletionCallbackNeverArrives() {
        val dispatcher =
            newDispatcher(
                dispatchGestureBehavior = { _, _ -> true },
            )

        assertActionException(
            expectedCode = RpcErrorCode.ACTION_TIMEOUT,
            expectedMessage = "gesture did not complete before timeout",
            expectedRetryable = true,
        ) {
            dispatcher.gesture(GestureDirection.Down, timeoutMs = 1L)
        }
    }

    private fun newDispatcher(
        tapGestureFactory: (Float, Float, Long) -> GestureDescription = { _, _, _ ->
            mock(GestureDescription::class.java)
        },
        swipeGestureFactory: (GestureDirection) -> GestureDescription = {
            mock(GestureDescription::class.java)
        },
        dispatchGestureBehavior: (GestureDescription, AccessibilityService.GestureResultCallback) -> Boolean = { _, callback ->
            callback.onCompleted(null)
            true
        },
    ): AccessibilityGestureDispatcher {
        val service = mockService()
        doAnswer { invocation ->
            val gesture = invocation.getArgument<GestureDescription>(0)
            val callback = invocation.getArgument<AccessibilityService.GestureResultCallback>(1)
            dispatchGestureBehavior(gesture, callback)
        }.`when`(service).dispatchGesture(any(), any(), isNull())
        return AccessibilityGestureDispatcher(
            service = service,
            gestureCallbackHandlerProvider = { null },
            tapGestureFactory = tapGestureFactory,
            swipeGestureFactory = swipeGestureFactory,
        )
    }
}
