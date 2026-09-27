package com.rainng.androidctl

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test
import java.io.IOException

class FileResultTest {
    @Test fun publicationUncertaintyTakesPrecedenceOverOpenedFact() {
        val failure = IOException("publication interrupted")
        assertEquals(
            "not sent",
            fileFailure(
                command = "push",
                id = "f1",
                begun = true,
                opened = false,
                publicationAttempted = false,
                error = failure,
            ).getString("result"),
        )
        for (command in listOf("push")) {
            val result =
                fileFailure(
                    command = command,
                    id = "f1",
                    begun = true,
                    opened = true,
                    publicationAttempted = false,
                    error = failure,
                )
            assertEquals("failed", result.getString("result"))
            assertEquals("publication interrupted", result.getString("reason"))
            assertEquals("workerFailure", result.getJSONObject("error").getString("code"))
            assertEquals("publication interrupted", result.getJSONObject("error").getString("message"))
        }
        for (opened in listOf(false, true)) {
            val result =
                fileFailure(
                    command = "push",
                    id = "f1",
                    begun = true,
                    opened = opened,
                    publicationAttempted = true,
                    error = failure,
                )
            assertEquals("UNKNOWN", result.getString("result"))
            assertEquals("f1", result.getJSONObject("data").getString("job"))
            assertEquals("workerFailure", result.getJSONObject("error").getString("code"))
        }
        assertEquals(
            "failed",
            fileFailure(
                command = "push",
                id = "f1",
                begun = true,
                opened = true,
                publicationAttempted = true,
                error = Rejected("cancelled", "cancelled"),
            ).getString("result"),
        )
        assertEquals(
            "UNKNOWN",
            fileFailure(
                command = "push",
                id = "f1",
                begun = true,
                opened = true,
                publicationAttempted = true,
                error = Rejected("publishFailed", "I/O", uncertain = true),
            ).getString("result"),
        )
    }

    @Test fun base64UsesLibraryPaddingAndRejectsBadCharacters() {
        for (encoded in listOf("YQ==", "YQ")) {
            assertArrayEquals(byteArrayOf(97), decodeFileChunk(encoded))
        }
        assertArrayEquals(byteArrayOf(), decodeFileChunk(""))
        for (encoded in listOf("Y Q==", "Y!Q==", "Y\nQ==", "Y", "YQ=", "YQ===", "YQ===" + "A", "====")) {
            assertEquals("chunk", assertThrows(Rejected::class.java) { decodeFileChunk(encoded) }.code)
        }
    }
}
