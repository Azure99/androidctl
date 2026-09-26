package com.rainng.androidctl

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import java.security.MessageDigest
import java.util.Base64

class OutputStoreTest {
    @Test fun readsBothStreamsInBoundedChunksWithExactSizeAndDigest() {
        val store = OutputStore { 0L }
        val stdout = ByteArray(32775) { (it % 251).toByte() }
        val stderr = "diagnostic\n".toByteArray()
        store.add("job", CapturedStream(stdout, stdout.size.toLong(), false), CapturedStream(stderr, stderr.size.toLong(), false))
        val details = requireNotNull(store.describe("job")).getJSONArray("streams")
        for ((name, expected) in listOf("stdout" to stdout, "stderr" to stderr)) {
            val metadata = (0 until details.length()).map(details::getJSONObject).single { it.getString("stream") == name }
            val digest = MessageDigest.getInstance("SHA-256").digest(expected).joinToString("") { "%02x".format(it) }
            assertEquals(expected.size, metadata.getInt("size"))
            assertEquals(digest, metadata.getString("sha256"))
            assertFalse(metadata.getBoolean("truncated"))
            val bytes =
                (0..expected.size step 32768)
                    .flatMap { offset ->
                        val chunk = store.read("job", json("stream" to name, "offset" to offset))
                        assertEquals(expected.size, chunk.getInt("size"))
                        assertEquals(digest, chunk.getString("sha256"))
                        Base64.getDecoder().decode(chunk.getString("bytes")).toList()
                    }.toByteArray()
            assertArrayEquals(expected, bytes)
            assertEquals("", store.read("job", json("stream" to name, "offset" to expected.size)).getString("bytes"))
        }
        assertEquals("stream", assertThrows(Rejected::class.java) { store.read("job", json("stream" to "other", "offset" to 0)) }.code)
        for (offset in listOf(-1, stderr.size + 1)) {
            assertEquals(
                "offset",
                assertThrows(Rejected::class.java) { store.read("job", json("stream" to "stderr", "offset" to offset)) }.code,
            )
        }
    }

    @Test fun truncationPreservesEitherMoreReadBytesOrExplicitCut() {
        val store = OutputStore { 0L }
        store.add(
            "job",
            CapturedStream(byteArrayOf(1), 2, false),
            CapturedStream(byteArrayOf(2), 1, true),
        )
        val streams = requireNotNull(store.describe("job")).getJSONArray("streams")
        for (name in listOf("stdout", "stderr")) {
            val metadata = (0 until streams.length()).map(streams::getJSONObject).single { it.getString("stream") == name }
            assertTrue(metadata.getBoolean("truncated"))
        }
    }

    @Test fun readAtExpiryBoundaryDoesNotExtendRetention() {
        var now = 0L
        val store = OutputStore { now }
        store.add(
            "job",
            CapturedStream(byteArrayOf(1), 1, false),
            CapturedStream(byteArrayOf(), 0, false),
        )
        now = 120000
        assertNotNull(store.describe("job"))
        assertEquals("AQ==", store.read("job", json("stream" to "stdout", "offset" to 0)).getString("bytes"))
        now++
        assertNull(store.describe("job"))
        assertEquals(
            "outputExpired",
            assertThrows(Rejected::class.java) { store.read("job", json("stream" to "stdout", "offset" to 0)) }.code,
        )
    }

    @Test fun capacityEvictsOldestInsertionEvenAfterReadingIt() {
        val store = OutputStore { 0L }
        val empty = CapturedStream(byteArrayOf(), 0, false)
        repeat(8) { store.add("$it", empty, empty) }
        store.read("0", json("stream" to "stdout", "offset" to 0))
        store.add("8", empty, empty)
        assertNull(store.describe("0"))
        (1..8).forEach { assertNotNull(store.describe("$it")) }
    }
}
