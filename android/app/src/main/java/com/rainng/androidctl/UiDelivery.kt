package com.rainng.androidctl

import org.json.JSONObject
import java.util.zip.Deflater

internal class UiDelivery(
    private val sessions: Sessions,
    private val workerId: String,
) {
    internal class Blob(
        raw: ByteArray,
        val bytes: ByteArray = raw,
    ) {
        val hash = sha256(bytes)
        val encoding = if (bytes === raw) "raw" else "zlib"
        val decodedSize = raw.size
        val decodedHash = if (bytes === raw) hash else sha256(raw)
    }

    internal data class PreparedTree(
        val sequence: Long,
        val blob: Blob?,
    ) {
        val size: Int get() = blob?.bytes?.size ?: 0
    }

    private sealed interface ImageState {
        data object NotRequested : ImageState

        data object Pending : ImageState

        data class Unavailable(
            val reason: String,
        ) : ImageState

        data class Available(
            val blob: Blob,
            val meta: JSONObject,
        ) : ImageState
    }

    private data class Capture(
        val id: String,
        val lease: Lease,
        val sequence: Long,
        val tree: Blob,
        var image: ImageState,
    )

    private var next = 0L
    private var current: Capture? = null

    @Synchronized fun clear() {
        current = null
    }

    fun publishedBaseline(
        lease: Lease,
        tree: JSONObject?,
    ): JSONObject {
        val baseline =
            sessions.withCurrentLease(lease) {
                synchronized(this) {
                    val capture =
                        current?.takeIf { it.lease === lease && tree != null && it.sequence == tree.getLong("sequence") }
                            ?: throw Rejected("noPublishedSnapshot", "No current published observation; run snapshot")
                    val observed = requireNotNull(tree)
                    if (observed.getBoolean("partial")) {
                        throw Rejected("partialBaseline", "Published screen-change baseline must be complete")
                    }
                    json(
                        "capture" to capture.id,
                        "sequence" to capture.sequence,
                        "scope" to copy(observed.getJSONObject("scope")),
                        "selectionScope" to copy(observed.getJSONObject("selectionScope")),
                    )
                }
            }
        return baseline.put("signature", UiSnapshot.signature(requireNotNull(tree)))
    }

    fun cached(
        lease: Lease,
        prepared: PreparedTree,
    ): String =
        sessions.withCurrentLease(lease) {
            synchronized(this) {
                current?.takeIf { it.lease === lease && it.sequence == prepared.sequence }?.id
                    ?: createCapture(lease, prepared, false)
            }
        }

    fun createCapture(
        lease: Lease,
        prepared: PreparedTree,
        screenshot: Boolean,
    ): String =
        sessions.withCurrentLease(lease) {
            synchronized(this) {
                val capture =
                    Capture(
                        "$workerId:${++next}",
                        lease,
                        prepared.sequence,
                        prepared.blob ?: throw Rejected("treeByteLimit", "Snapshot exceeds its transfer budget"),
                        image = if (screenshot) ImageState.Pending else ImageState.NotRequested,
                    )
                current = capture
                capture.id
            }
        }

    fun image(
        lease: Lease,
        id: String,
        bytes: ByteArray,
        meta: JSONObject,
    ) = updateImage(lease, id, ImageState.Available(Blob(bytes), meta))

    fun imageFailed(
        lease: Lease,
        id: String,
        reason: String,
    ) = updateImage(lease, id, ImageState.Unavailable(reason))

    private fun updateImage(
        lease: Lease,
        id: String,
        state: ImageState,
    ) = sessions.withCurrentLease(lease) {
        synchronized(this) {
            current?.takeIf { it.id == id && it.lease === lease }?.let {
                it.image = state
            }
        }
    }

    fun read(
        owner: SessionOwner,
        request: JSONObject,
    ): JSONObject {
        val lease = sessions.acquire(owner)
        return sessions.withCurrentLease(lease) {
            synchronized(this) {
                val capture =
                    current?.takeIf { it.lease === lease && it.id == request.getString("capture") }
                        ?: throw Rejected("captureUnavailable", "Capture is no longer current")
                val kind = request.getString("kind")
                val image = capture.image
                val blob: Blob? =
                    when (kind) {
                        "tree" -> capture.tree
                        "image" -> (image as? ImageState.Available)?.blob
                        else -> throw Rejected("artifact", "Expected tree or image")
                    }
                if (blob == null) {
                    val state =
                        when (image) {
                            ImageState.NotRequested -> "not requested"
                            ImageState.Pending -> "pending"
                            is ImageState.Unavailable -> image.reason
                            is ImageState.Available -> "available"
                        }
                    outcome("ui.read", data = json("capture" to capture.id, "state" to state))
                } else {
                    val bytes = blob.bytes
                    val offset = request.getInt("offset")
                    outcome(
                        "ui.read",
                        data =
                            json(
                                "capture" to capture.id,
                                "state" to "available",
                                "offset" to offset,
                                "size" to bytes.size,
                                "sha256" to blob.hash,
                                "encoding" to blob.encoding,
                                "decodedSize" to blob.decodedSize,
                                "decodedSha256" to blob.decodedHash,
                                "image" to if (kind == "image") (image as ImageState.Available).meta else null,
                                "bytes" to encodeChunk(bytes, offset),
                            ),
                    )
                }
            }
        }
    }

    companion object {
        fun prepareTree(tree: JSONObject): PreparedTree {
            val sequence = tree.getLong("sequence")
            val raw = tree.toString().toByteArray()
            if (raw.size > Limits.TREE_BYTES) return PreparedTree(sequence, null)
            val compressor = Deflater(1)
            val compressed = ByteArray(raw.size)
            val bytes =
                try {
                    compressor.setInput(raw)
                    compressor.finish()
                    var count = 0
                    while (!compressor.finished() && count < compressed.size) {
                        count += compressor.deflate(compressed, count, compressed.size - count)
                    }
                    if (compressor.finished() && count < raw.size) compressed.copyOf(count) else raw
                } finally {
                    compressor.end()
                }
            return PreparedTree(sequence, Blob(raw, bytes))
        }
    }
}
