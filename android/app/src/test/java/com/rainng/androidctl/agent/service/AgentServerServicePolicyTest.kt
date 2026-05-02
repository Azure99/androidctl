package com.rainng.androidctl.agent.service

import android.app.Service
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AgentServerServicePolicyTest {
    @Test
    fun restartPolicyTreatsNullAndStartAsStickyForegroundOnly() {
        assertTrue(AgentServerService.shouldPromoteToForeground(null))
        assertEquals(Service.START_STICKY, AgentServerService.startMode(null))

        assertTrue(AgentServerService.shouldPromoteToForeground(AgentServerService.ACTION_START))
        assertEquals(Service.START_STICKY, AgentServerService.startMode(AgentServerService.ACTION_START))

        assertFalse(AgentServerService.shouldPromoteToForeground(AgentServerService.ACTION_STOP))
        assertEquals(Service.START_NOT_STICKY, AgentServerService.startMode(AgentServerService.ACTION_STOP))

        assertFalse(AgentServerService.shouldPromoteToForeground("noop"))
        assertEquals(Service.START_NOT_STICKY, AgentServerService.startMode("noop"))
    }
}
