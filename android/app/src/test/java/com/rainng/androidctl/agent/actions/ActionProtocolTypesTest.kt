package com.rainng.androidctl.agent.actions

import com.rainng.androidctl.agent.errors.RequestValidationException
import org.junit.Assert.assertEquals
import org.junit.Assert.fail
import org.junit.Test

class ActionProtocolTypesTest {
    @Test
    fun actionResultStatusesExposeOnlyNormalWireNamesInStableOrder() {
        assertEquals(
            listOf("done", "partial"),
            enumValues<ActionResultStatus>().map(ActionResultStatus::wireName),
        )
    }

    @Test
    fun actionKindsExportCanonicalWireNamesInStableOrder() {
        assertEquals(
            listOf("tap", "longTap", "type", "node", "scroll", "global", "gesture", "launchApp", "openUrl"),
            ActionKind.capabilityWireNames(),
        )
    }

    @Test
    fun scrollDirectionsExposeTheFrozenAcceptedWireSet() {
        assertEquals(
            listOf("backward", "down", "up", "left", "right"),
            enumValues<ScrollDirection>().map(ScrollDirection::wireName),
        )
    }

    @Test
    fun scrollDirectionDecodesEveryAllowedToken() {
        listOf(
            "backward" to ScrollDirection.Backward,
            "down" to ScrollDirection.Down,
            "up" to ScrollDirection.Up,
            "left" to ScrollDirection.Left,
            "right" to ScrollDirection.Right,
        ).forEach { (token, expectedDirection) ->
            assertEquals(expectedDirection, ScrollDirection.decode(token))
            assertEquals(token, ScrollDirection.decode(token).wireName)
        }
    }

    @Test
    fun scrollDirectionRejectsLegacyForwardToken() {
        assertValidationError("unsupported scroll direction 'forward'") {
            ScrollDirection.decode("forward")
        }
    }

    @Test
    fun scrollDirectionRejectsOtherNonListedTokens() {
        assertValidationError("unsupported scroll direction 'backwards'") {
            ScrollDirection.decode("backwards")
        }
        assertValidationError("unsupported scroll direction 'center'") {
            ScrollDirection.decode("center")
        }
    }

    private fun assertValidationError(
        expectedMessage: String,
        block: () -> Unit,
    ) {
        try {
            block()
            fail("expected RequestValidationException")
        } catch (error: RequestValidationException) {
            assertEquals(expectedMessage, error.message)
        }
    }
}
