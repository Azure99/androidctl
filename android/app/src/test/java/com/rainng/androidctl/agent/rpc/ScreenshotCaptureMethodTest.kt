package com.rainng.androidctl.agent.rpc

import com.rainng.androidctl.agent.RequestBudgets
import com.rainng.androidctl.agent.errors.RequestValidationException
import com.rainng.androidctl.agent.screenshot.ScreenshotRequest
import com.rainng.androidctl.agent.screenshot.ScreenshotResponse
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.fail
import org.junit.Test

class ScreenshotCaptureMethodTest {
    @Test
    fun policyUsesUnifiedMethodTimeout() {
        val method =
            ScreenshotCaptureMethod(
                screenshotExecutionFactory =
                    providerFactory {
                        ScreenshotResponse(
                            contentType = "image/png",
                            widthPx = 1,
                            heightPx = 1,
                            bodyBase64 = "AA==",
                        )
                    },
            )

        assertEquals(true, method.policy.requiresReadyRuntime)
        assertEquals(true, method.policy.requiresAccessibilityHandle)
        assertEquals("SCREENSHOT_UNAVAILABLE", method.policy.timeoutError.name)
        assertEquals("screenshot.capture timed out", method.policy.timeoutMessage)
    }

    @Test
    fun prepareParsesScreenshotRequestAndDefersProviderUntilExecute() {
        var capturedRequest: ScreenshotRequest? = null
        var providerCalls = 0
        val payload =
            ScreenshotResponse(
                contentType = "image/png",
                widthPx = 100,
                heightPx = 50,
                bodyBase64 = "ZmFrZQ==",
            )
        val method =
            ScreenshotCaptureMethod(
                screenshotExecutionFactory =
                    providerFactory { request ->
                        providerCalls += 1
                        capturedRequest = request
                        payload
                    },
            )
        val prepared = method.prepare(request("""{"format":"jpeg","scale":0.5}"""))

        assertEquals(0, providerCalls)
        val encoded = prepared.executeEncoded()

        assertEquals(1, providerCalls)
        assertEquals(RequestBudgets.SCREENSHOT_METHOD_TIMEOUT_MS, prepared.timeoutMs)
        assertEquals(ScreenshotRequest(format = "jpeg", scale = 0.5), capturedRequest)
        assertEquals("image/png", encoded.getString("contentType"))
        assertEquals(100, encoded.getInt("widthPx"))
        assertEquals(50, encoded.getInt("heightPx"))
        assertEquals("ZmFrZQ==", encoded.getString("bodyBase64"))
    }

    @Test
    fun prepareUsesScreenshotMethodBudget() {
        val method =
            ScreenshotCaptureMethod(
                screenshotExecutionFactory =
                    providerFactory {
                        ScreenshotResponse(
                            contentType = "image/png",
                            widthPx = 1,
                            heightPx = 1,
                            bodyBase64 = "AA==",
                        )
                    },
            )

        val prepared = method.prepare(request("""{"format":"png","scale":1.0}"""))

        assertEquals(RequestBudgets.SCREENSHOT_METHOD_TIMEOUT_MS, prepared.timeoutMs)
    }

    @Test
    fun prepareRejectsInvalidParams() {
        val method =
            ScreenshotCaptureMethod(
                screenshotExecutionFactory =
                    providerFactory {
                        ScreenshotResponse(
                            contentType = "image/png",
                            widthPx = 1,
                            heightPx = 1,
                            bodyBase64 = "AA==",
                        )
                    },
            )

        try {
            method.prepare(request("""{"format":"gif","scale":1.0}"""))
            fail("expected RequestValidationException")
        } catch (error: RequestValidationException) {
            assertEquals("screenshot.capture requires format png or jpeg", error.message)
        }
    }

    @Test
    fun prepareBindsScreenshotExecutionFactoryBeforeExecute() {
        var boundBody = "prepare=="
        val method =
            ScreenshotCaptureMethod {
                val preparedBody = boundBody
                {
                    ScreenshotResponse(
                        contentType = "image/png",
                        widthPx = 1,
                        heightPx = 1,
                        bodyBase64 = preparedBody,
                    )
                }
            }

        val prepared = method.prepare(request("""{"format":"png","scale":1.0}"""))
        boundBody = "execute=="
        val encoded = prepared.executeEncoded()

        assertEquals("prepare==", encoded.getString("bodyBase64"))
    }

    private fun request(params: String): RpcRequestEnvelope =
        RpcRequestEnvelope(
            id = "req-screenshot",
            method = "screenshot.capture",
            params = JSONObject(params),
        )

    private fun providerFactory(provider: (ScreenshotRequest) -> ScreenshotResponse): (ScreenshotRequest) -> () -> ScreenshotResponse =
        { request -> { provider(request) } }
}
