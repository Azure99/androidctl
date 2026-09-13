package com.rainng.androidctl

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Test

class JsonComparisonTest {
    @Test fun comparisonsPreserveJsonTypesValuesAndArrayOrder() {
        for ((expected, actual) in listOf(
            """{"x":1}""" to """{"x":"1"}""",
            """{"x":1.2}""" to """{"x":1.9}""",
            """{"x":true}""" to """{"x":1}""",
            """{"x":null}""" to """{}""",
            """{"x":[1,2]}""" to """{"x":[2,1]}""",
            """{"x":[1]}""" to """{"x":["1"]}""",
            """{"x":9007199254740992}""" to """{"x":9007199254740993}""",
        )) {
            assertNotEquals("Distinct JSON facts must not compare equal", structure(JSONObject(expected)), structure(JSONObject(actual)))
        }
        assertEquals(
            structure(JSONObject("""{"x":[{"a":1,"b":null}]}""")),
            structure(JSONObject("""{"x":[{"b":null,"a":1.0}]}""")),
        )
    }
}
