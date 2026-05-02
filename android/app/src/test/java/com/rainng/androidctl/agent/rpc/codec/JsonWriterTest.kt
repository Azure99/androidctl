package com.rainng.androidctl.agent.rpc.codec

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class JsonWriterTest {
    @Test
    fun nullableStringPreservesKeyAndWritesJsonNull() {
        val writer = JsonWriter.objectWriter()

        writer.nullableString("subtitle", null)

        val json = writer.toJsonObject()
        assertTrue(json.has("subtitle"))
        assertTrue(json.isNull("subtitle"))
    }

    @Test
    fun writesArrayEncoding() {
        val writer = JsonWriter.objectWriter()

        writer.array("items") { items ->
            items.objectElement { item ->
                item.requiredString("kind", "node")
                item.requiredInt("id", 7)
            }
            items.requiredStringValue("tail")
            items.requiredIntValue(9)
        }

        val json = writer.toJsonObject()
        val items = json.getJSONArray("items")
        assertEquals(3, items.length())
        assertEquals("node", items.getJSONObject(0).getString("kind"))
        assertEquals(7, items.getJSONObject(0).getInt("id"))
        assertEquals("tail", items.getString(1))
        assertEquals(9, items.getInt(2))
    }
}
