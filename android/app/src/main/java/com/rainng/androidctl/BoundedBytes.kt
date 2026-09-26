package com.rainng.androidctl

import java.io.ByteArrayOutputStream
import java.io.IOException

private const val INITIAL_BUFFER_BYTES = 8192

internal class BoundedBytes(
    private val limit: Int,
) : ByteArrayOutputStream(minOf(limit, INITIAL_BUFFER_BYTES)) {
    @Synchronized override fun write(value: Int) {
        ensureRoom(1)
        super.write(value)
    }

    @Synchronized override fun write(
        bytes: ByteArray,
        offset: Int,
        length: Int,
    ) {
        ensureRoom(length)
        super.write(bytes, offset, length)
    }

    private fun ensureRoom(length: Int) {
        if (length > limit - count) throw IOException("Output exceeds $limit bytes")
    }
}
