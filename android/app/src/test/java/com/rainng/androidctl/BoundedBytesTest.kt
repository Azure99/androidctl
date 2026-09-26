package com.rainng.androidctl

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test
import java.io.IOException

class BoundedBytesTest {
    @Test fun outputStopsBeforeKeepingBytesBeyondTheBudget() {
        val output = BoundedBytes(10)
        output.write(ByteArray(9))
        assertThrows(IOException::class.java) { output.write(ByteArray(2)) }
        assertEquals(9, output.size())
        output.write(1)
        assertEquals(10, output.size())
        assertThrows(IOException::class.java) { output.write(2) }
        assertEquals(10, output.toByteArray().size)
    }
}
