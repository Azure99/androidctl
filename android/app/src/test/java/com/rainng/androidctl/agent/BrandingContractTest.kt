package com.rainng.androidctl.agent

import com.rainng.androidctl.BuildConfig
import com.rainng.androidctl.SetupActivityContract
import com.rainng.androidctl.agent.service.AgentServerService
import org.junit.Assert.assertEquals
import org.junit.Test

class BrandingContractTest {
    @Test
    fun build_and_actions_match_androidctl_identity() {
        assertEquals("com.rainng.androidctl", BuildConfig.APPLICATION_ID)
        assertEquals("androidctl-device-agent", AgentConstants.SERVICE_NAME)
        assertEquals(
            "com.rainng.androidctl.action.START_SERVER",
            AgentServerService.ACTION_START,
        )
        assertEquals(
            "com.rainng.androidctl.action.STOP_SERVER",
            AgentServerService.ACTION_STOP,
        )
        assertEquals(
            "com.rainng.androidctl.action.SETUP",
            SetupActivityContract.ACTION_SETUP,
        )
        assertEquals(
            "com.rainng.androidctl.SetupActivity",
            SetupActivityContract.COMPONENT_CLASS_NAME,
        )
    }
}
