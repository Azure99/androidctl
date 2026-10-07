package com.rainng.androidctl

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertThrows
import org.junit.Test
import java.util.Base64
import java.util.zip.InflaterInputStream

class UiDeliveryTest {
    @Test fun preparedTreeDeliversRawOrCompressedBytesAndCanBeRepublishedAfterClear() {
        val sessions = Sessions({ 0L }, {})
        val delivery = UiDelivery(sessions, "worker")
        val owner = SessionOwner("pair", "task", "host")
        val lease = sessions.acquire(owner)
        val samples =
            listOf(
                json("sequence" to 1) to "raw",
                json("sequence" to 2, "padding" to "Repeated content ".repeat(100)) to "zlib",
            )
        for ((sample, encoding) in samples) {
            val prepared = UiDelivery.prepareTree(sample)
            val capture = delivery.cached(lease, prepared)
            val data = delivery.read(owner, json("capture" to capture, "kind" to "tree", "offset" to 0)).getJSONObject("data")
            val bytes = Base64.getDecoder().decode(data.getString("bytes"))
            val decoded = if (encoding == "zlib") InflaterInputStream(bytes.inputStream()).use { it.readBytes() } else bytes
            assertEquals(encoding, data.getString("encoding"))
            assertEquals(prepared.size, data.getInt("size"))
            assertArrayEquals(sample.toString().toByteArray(), decoded)
            assertEquals(sha256(bytes), data.getString("sha256"))
            assertEquals(sha256(decoded), data.getString("decodedSha256"))
            assertEquals(capture, delivery.cached(lease, prepared))
            delivery.clear()
            assertEquals("captureUnavailable", readFailure(delivery, owner, capture))
            assertNotEquals(capture, delivery.cached(lease, prepared))
        }
    }

    @Test fun publishedBaselineUsesOnlyTheCurrentCompleteCaptureWithoutRequiringRead() {
        val sessions = Sessions({ 0L }, {})
        val delivery = UiDelivery(sessions, "worker")
        val owner = SessionOwner("pair", "task", "host")
        val lease = sessions.acquire(owner)
        val sample = publishedTree()
        val prepared = UiDelivery.prepareTree(sample)
        assertEquals("noPublishedSnapshot", assertThrows(Rejected::class.java) { delivery.publishedBaseline(lease, sample) }.code)
        val capture = delivery.createCapture(lease, prepared, false)
        val baseline = delivery.publishedBaseline(lease, sample)
        assertEquals(capture, baseline.getString("capture"))
        assertEquals(1L, baseline.getLong("sequence"))
        assertEquals(0, baseline.getJSONObject("selectionScope").length())
        assertEquals(UiSnapshot.signature(sample), baseline.getString("signature"))
        sample.getJSONObject("scope").put("window", 2)
        assertEquals(1, baseline.getJSONObject("scope").getInt("window"))
        assertEquals("noPublishedSnapshot", assertThrows(Rejected::class.java) { delivery.publishedBaseline(lease, publishedTree(2)) }.code)
        sessions.close(owner)
        assertEquals("sessionChanged", assertThrows(Rejected::class.java) { delivery.publishedBaseline(lease, sample) }.code)
    }

    @Test fun partialAndInvalidatedPublicationCannotReuseAnOlderCompleteBaseline() {
        val sessions = Sessions({ 0L }, {})
        val delivery = UiDelivery(sessions, "worker")
        val lease = sessions.acquire(SessionOwner("pair", "task", "host"))
        delivery.createCapture(lease, UiDelivery.prepareTree(publishedTree()), false)
        val partial = publishedTree(2).put("partial", true)
        delivery.createCapture(lease, UiDelivery.prepareTree(partial), false)
        assertEquals("partialBaseline", assertThrows(Rejected::class.java) { delivery.publishedBaseline(lease, partial) }.code)
        delivery.clear()
        assertEquals("noPublishedSnapshot", assertThrows(Rejected::class.java) { delivery.publishedBaseline(lease, partial) }.code)
        val oversized = publishedTree(3).put("padding", "x".repeat(Limits.TREE_BYTES + 1))
        val prepared = UiDelivery.prepareTree(oversized)
        assertEquals(0, prepared.size)
        assertEquals("treeByteLimit", assertThrows(Rejected::class.java) { delivery.createCapture(lease, prepared, false) }.code)
        assertEquals("noPublishedSnapshot", assertThrows(Rejected::class.java) { delivery.publishedBaseline(lease, oversized) }.code)
    }

    @Test fun baselineRejectsLeasePublicationAndPartialBeforeInspectingNodes() {
        val sessions = Sessions({ 0L }, {})
        val delivery = UiDelivery(sessions, "worker")
        val owner = SessionOwner("pair", "task", "host")
        val lease = sessions.acquire(owner)
        val partial = publishedTree().put("partial", true).apply { remove("nodes") }

        assertEquals("noPublishedSnapshot", assertThrows(Rejected::class.java) { delivery.publishedBaseline(lease, partial) }.code)
        delivery.createCapture(lease, UiDelivery.prepareTree(partial), false)
        partial.put("sequence", 2)
        assertEquals("noPublishedSnapshot", assertThrows(Rejected::class.java) { delivery.publishedBaseline(lease, partial) }.code)
        partial.put("sequence", 1)
        assertEquals("partialBaseline", assertThrows(Rejected::class.java) { delivery.publishedBaseline(lease, partial) }.code)
        sessions.close(owner)
        assertEquals("sessionChanged", assertThrows(Rejected::class.java) { delivery.publishedBaseline(lease, partial) }.code)
    }

    @Test fun expiredOrReplacedCaptureCannotBeReadAsCurrent() {
        var now = 0L
        val sessions = Sessions({ now }, {})
        val delivery = UiDelivery(sessions, "worker")
        val owner = SessionOwner("pair", "task", "host")
        val lease = sessions.acquire(owner)
        val first = delivery.createCapture(lease, UiDelivery.prepareTree(json("sequence" to 1)), false)
        val prepared = UiDelivery.prepareTree(json("sequence" to 2))
        val second = delivery.createCapture(lease, prepared, false)
        delivery.image(lease, first, byteArrayOf(1), json("id" to "old"))
        delivery.imageFailed(lease, first, "Late failure")
        assertEquals("captureUnavailable", readFailure(delivery, owner, first))
        assertEquals(second, delivery.cached(lease, prepared))
        assertEquals(
            "not requested",
            delivery.read(owner, json("capture" to second, "kind" to "image", "offset" to 0)).getJSONObject("data").getString("state"),
        )
        now += Sessions.DEFAULT_IDLE_MS
        assertEquals("captureUnavailable", readFailure(delivery, owner, second))
        assertEquals("sessionChanged", assertThrows(Rejected::class.java) { delivery.imageFailed(lease, second, "Old lease") }.code)
    }

    @Test fun imageDeliveryBindsBytesAndMetadataAndKeepsFailureReason() {
        val sessions = Sessions({ 0L }, {})
        val delivery = UiDelivery(sessions, "worker")
        val owner = SessionOwner("pair", "task", "host")
        val lease = sessions.acquire(owner)
        var capture = delivery.createCapture(lease, UiDelivery.prepareTree(json("sequence" to 1)), true)

        fun image() = delivery.read(owner, json("capture" to capture, "kind" to "image", "offset" to 0)).getJSONObject("data")
        assertEquals("pending", image().getString("state"))
        val meta = json("id" to "i1", "mappingKnown" to false)
        delivery.image(lease, capture, byteArrayOf(1), meta)
        val available = image()
        assertEquals("available", available.getString("state"))
        assertEquals("AQ==", available.getString("bytes"))
        assertEquals(structure(meta), structure(available.getJSONObject("image")))
        capture = delivery.createCapture(lease, UiDelivery.prepareTree(json("sequence" to 2)), true)
        delivery.imageFailed(lease, capture, "PNG encoding failed")
        val failed = image()
        assertEquals("PNG encoding failed", failed.getString("state"))
        assertFalse(failed.has("bytes"))
        assertFalse(failed.has("image"))
        delivery.clear()
        assertEquals("captureUnavailable", readFailure(delivery, owner, capture))
    }

    private fun publishedTree(sequence: Int = 1): JSONObject =
        json(
            "sequence" to sequence,
            "selectionScope" to JSONObject(),
            "scope" to json("window" to 1, "package" to "fixture"),
            "partial" to false,
            "nodes" to JSONArray(),
            "windows" to JSONArray(),
        )

    private fun readFailure(
        delivery: UiDelivery,
        owner: SessionOwner,
        capture: String,
    ): String =
        try {
            delivery.read(owner, json("capture" to capture, "kind" to "tree", "offset" to 0))
            throw AssertionError("An unavailable capture must be rejected")
        } catch (error: Rejected) {
            error.code
        }
}
