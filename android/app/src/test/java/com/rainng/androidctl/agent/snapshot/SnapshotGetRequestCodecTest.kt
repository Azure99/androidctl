package com.rainng.androidctl.agent.snapshot

import com.rainng.androidctl.agent.errors.RequestValidationException
import com.rainng.androidctl.agent.rpc.codec.JsonReader
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.fail
import org.junit.Test

class SnapshotGetRequestCodecTest {
    @Test
    fun readRejectsMissingFlags() {
        assertValidationError("snapshot.get requires includeInvisible") {
            SnapshotGetRequestCodec.read(JsonReader.fromObject(JSONObject()))
        }
        assertValidationError("snapshot.get requires includeSystemWindows") {
            SnapshotGetRequestCodec.read(
                JsonReader.fromObject(
                    JSONObject("""{"includeInvisible":true}"""),
                ),
            )
        }
        assertValidationError("snapshot.get requires includeInvisible") {
            SnapshotGetRequestCodec.read(
                JsonReader.fromObject(
                    JSONObject("""{"includeSystemWindows":true}"""),
                ),
            )
        }
    }

    @Test
    fun readParsesExplicitFlags() {
        val request =
            SnapshotGetRequestCodec.read(
                JsonReader.fromObject(
                    JSONObject("""{"includeInvisible":false,"includeSystemWindows":false}"""),
                ),
            )

        assertEquals(SnapshotGetRequest(includeInvisible = false, includeSystemWindows = false), request)
    }

    @Test
    fun readIgnoresUnknownTopLevelParamsFields() {
        val request =
            SnapshotGetRequestCodec.read(
                JsonReader.fromObject(
                    JSONObject(
                        """
                        {
                          "includeInvisible": true,
                          "includeSystemWindows": false,
                          "clientTag": "ignored",
                          "debug": {"trace": true},
                          "unusedNull": null
                        }
                        """.trimIndent(),
                    ),
                ),
            )

        assertEquals(SnapshotGetRequest(includeInvisible = true, includeSystemWindows = false), request)
    }

    @Test
    fun readRejectsStringBoolean() {
        assertValidationError("snapshot.get includeInvisible must be a boolean") {
            SnapshotGetRequestCodec.read(
                JsonReader.fromObject(
                    JSONObject("""{"includeInvisible":"false","includeSystemWindows":true}"""),
                ),
            )
        }
    }

    @Test
    fun readRejectsInvalidIncludeSystemWindowsType() {
        assertValidationError("snapshot.get includeSystemWindows must be a boolean") {
            SnapshotGetRequestCodec.read(
                JsonReader.fromObject(
                    JSONObject("""{"includeInvisible":true,"includeSystemWindows":"false"}"""),
                ),
            )
        }
    }

    @Test
    fun readRejectsNullFlags() {
        assertValidationError("snapshot.get includeInvisible must be a boolean") {
            SnapshotGetRequestCodec.read(
                JsonReader.fromObject(
                    JSONObject("""{"includeInvisible":null,"includeSystemWindows":true}"""),
                ),
            )
        }
        assertValidationError("snapshot.get includeSystemWindows must be a boolean") {
            SnapshotGetRequestCodec.read(
                JsonReader.fromObject(
                    JSONObject("""{"includeInvisible":true,"includeSystemWindows":null}"""),
                ),
            )
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
