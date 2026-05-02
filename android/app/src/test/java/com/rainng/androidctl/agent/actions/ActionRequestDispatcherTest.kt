package com.rainng.androidctl.agent.actions

import com.rainng.androidctl.agent.errors.RpcErrorCode
import org.junit.Assert.assertEquals
import org.junit.Assert.fail
import org.junit.Test

class ActionRequestDispatcherTest {
    @Test
    fun tapRejectsNoneTargetAtDispatcherSeam() {
        try {
            ActionRequestDispatcher(RecordingActionBackend()).dispatch(
                TapActionRequest(
                    target = ActionTarget.None,
                    timeoutMs = 5000L,
                ),
            )
            fail("expected ActionException")
        } catch (error: ActionException) {
            assertEquals(RpcErrorCode.INVALID_REQUEST, error.code)
            assertEquals("tap requires handle or coordinates target", error.message)
        }
    }
}
