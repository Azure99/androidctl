package com.rainng.androidctl.agent.rpc.codec

import com.rainng.androidctl.agent.actions.ActionRequestCodec
import com.rainng.androidctl.agent.actions.ActionResultCodec
import com.rainng.androidctl.agent.actions.ActionTargetCodec
import com.rainng.androidctl.agent.device.AppsListResponseCodec
import com.rainng.androidctl.agent.events.DeviceEventCodec
import com.rainng.androidctl.agent.events.EventPollRequestCodec
import com.rainng.androidctl.agent.events.EventPollResultCodec
import com.rainng.androidctl.agent.rpc.MetaResponseCodec
import com.rainng.androidctl.agent.screenshot.ScreenshotRequestCodec
import com.rainng.androidctl.agent.screenshot.ScreenshotResponseCodec
import com.rainng.androidctl.agent.snapshot.SnapshotGetRequestCodec
import com.rainng.androidctl.agent.snapshot.SnapshotResponseCodec
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class JsonCodecRoleConformanceTest {
    @Test
    fun oneWayCodecsExposeOnlyTheirDirection() {
        assertDecoderOnly(ActionRequestCodec)
        assertDecoderOnly(SnapshotGetRequestCodec)
        assertDecoderOnly(EventPollRequestCodec)
        assertDecoderOnly(ScreenshotRequestCodec)

        assertEncoderOnly(ActionResultCodec)
        assertEncoderOnly(DeviceEventCodec)
        assertEncoderOnly(EventPollResultCodec)
        assertEncoderOnly(AppsListResponseCodec)
        assertEncoderOnly(MetaResponseCodec)
        assertEncoderOnly(ScreenshotResponseCodec)
        assertEncoderOnly(SnapshotResponseCodec)
        assertEncoderOnly(DisplayFragmentCodec)
        assertEncoderOnly(ImeFragmentCodec)
        assertEncoderOnly(ForegroundContextFragmentCodec)
    }

    @Test
    fun actionTargetCodecRemainsJsonCodec() {
        assertTwoWayCodec(ActionTargetCodec)
    }

    private fun assertDecoderOnly(codec: Any) {
        assertTrue(codec is JsonDecoder<*>)
        assertFalse(codec is JsonEncoder<*>)
    }

    private fun assertEncoderOnly(codec: Any) {
        assertFalse(codec is JsonDecoder<*>)
        assertTrue(codec is JsonEncoder<*>)
    }

    private fun assertTwoWayCodec(codec: Any) {
        assertTrue(codec is JsonCodec<*>)
    }
}
