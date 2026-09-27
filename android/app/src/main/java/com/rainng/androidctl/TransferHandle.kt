package com.rainng.androidctl

import android.os.ParcelFileDescriptor
import android.system.Os
import android.system.OsConstants
import android.system.StructStat
import org.json.JSONObject
import java.io.File
import java.io.InputStream
import java.io.OutputStream
import java.security.MessageDigest

internal class TransferHandle(
    val push: Boolean,
    path: String,
    private val replace: Boolean,
) {
    private val target = File(path)
    private val temporary = ".androidctl-${token()}.tmp"
    private var directory = -1
    private var parcel: ParcelFileDescriptor? = null
    private var input: InputStream? = null
    private var output: OutputStream? = null
    private var original: StructStat? = null
    private val digest = MessageDigest.getInstance("SHA-256")

    @Volatile var offset = 0L
        private set

    @Volatile var size = 0L
        private set

    @Volatile var opened = false
        private set

    var publicationAttempted = false
        private set
    private var published = false

    init {
        if (!target.isAbsolute || target.name in setOf("", ".", "..") || '\u0000' in path) {
            throw Rejected("path", "An absolute path to one ordinary file is required")
        }
    }

    fun open(expectedSize: Long) {
        directory = NativeFiles.openDirectory(requireNotNull(target.parent).toByteArray())
        if (push) {
            openPushTarget(expectedSize)
        } else {
            openPullSource()
        }
    }

    private fun openPushTarget(expectedSize: Long) {
        if (NativeFiles.targetExists(directory, target.name.toByteArray()) && !replace) {
            throw Rejected("fileExists", "Destination exists; choose --replace explicitly")
        }
        val fd = NativeFiles.createTemporary(directory, temporary.toByteArray())
        opened = true
        parcel = ParcelFileDescriptor.adoptFd(fd)
        original = Os.fstat(requireNotNull(parcel).fileDescriptor)
        size = expectedSize
        output = ParcelFileDescriptor.AutoCloseOutputStream(parcel)
    }

    private fun openPullSource() {
        val fd = NativeFiles.openRegular(directory, target.name.toByteArray())
        opened = true
        parcel = ParcelFileDescriptor.adoptFd(fd)
        original = Os.fstat(requireNotNull(parcel).fileDescriptor)
        size = requireNotNull(original).st_size
        input = ParcelFileDescriptor.AutoCloseInputStream(parcel)
    }

    fun write(bytes: ByteArray) {
        if (offset + bytes.size > size) throw Rejected("size", "Chunk exceeds the declared size")
        requireNotNull(output).write(bytes)
        digest.update(bytes)
        offset += bytes.size
    }

    fun read(): ByteArray {
        val bytes = ByteArray(minOf(Limits.CHUNK_BYTES.toLong(), size - offset).toInt())
        var received = 0
        while (received < bytes.size) {
            val count = requireNotNull(input).read(bytes, received, bytes.size - received)
            if (count <= 0) throw Rejected("sourceChanged", "Source ended before its declared size")
            received += count
        }
        digest.update(bytes)
        offset += bytes.size
        return bytes
    }

    fun finish(
        expectedHash: String,
        beforePublish: () -> Unit,
    ): JSONObject {
        if (offset != size) throw Rejected("size", "Transfer size does not match")
        val hash = digest.digest().joinToString("") { "%02x".format(it) }
        if (hash != expectedHash) throw Rejected("hash", "Transfer hash does not match")
        if (push) {
            publish(beforePublish)
        } else {
            verifySource()
        }
        return json("size" to size, "sha256" to hash)
    }

    @Suppress("ComplexCondition") // Source identity includes size and both second/nanosecond timestamps.
    private fun verifySource() {
        if (requireNotNull(input).read() != -1) {
            throw Rejected("sourceChanged", "Source contains bytes beyond its declared size")
        }
        val current = Os.fstat(requireNotNull(parcel).fileDescriptor)
        val initial = requireNotNull(original)
        if (current.st_dev != initial.st_dev ||
            current.st_ino != initial.st_ino ||
            current.st_size != initial.st_size ||
            current.st_mtim != initial.st_mtim ||
            current.st_ctim != initial.st_ctim
        ) {
            throw Rejected("sourceChanged", "Source metadata changed during transfer")
        }
    }

    private fun publish(beforePublish: () -> Unit) {
        requireNotNull(output).flush()
        requireNotNull(parcel).fileDescriptor.sync()
        ParcelFileDescriptor.adoptFd(NativeFiles.openRegular(directory, temporary.toByteArray())).use { check ->
            val current = Os.fstat(check.fileDescriptor)
            val initial = requireNotNull(original)
            if (current.st_dev != initial.st_dev || current.st_ino != initial.st_ino) {
                throw Rejected("temporaryChanged", "Temporary file identity changed")
            }
        }
        beforePublish()
        val publication =
            NativeFiles.publishFile(
                directory = directory,
                from = temporary.toByteArray(),
                to = target.name.toByteArray(),
                replace = replace,
            )
        publicationAttempted = publication.publicationAttempted
        val error = publication.errorNumber
        if (error != 0) {
            val unsupported = error == OsConstants.ENOSYS || error == OsConstants.EOPNOTSUPP
            throw Rejected(
                if (unsupported) "publishUnsupported" else "publishFailed",
                if (unsupported) "Atomic no-replace is unsupported here; choose --replace or another path" else Os.strerror(error),
                uncertain = error == OsConstants.EIO,
            )
        }
        published = true
    }

    fun close(): String? {
        val failures = mutableListOf<String>()
        if (push && parcel != null && original == null) failures.add("Temporary identity unavailable; left untouched")

        fun attempt(action: () -> Unit) {
            runCatching(action).onFailure { failures.add(message(it)) }
        }
        // AutoClose streams own the parcel; a failed close must not close the same fd again.
        val stream = output ?: input
        val descriptor = parcel
        output = null
        input = null
        parcel = null
        attempt { if (stream != null) stream.close() else descriptor?.close() }
        if (push && !published && original != null) {
            attempt {
                val identity = requireNotNull(original)
                if (!NativeFiles.removeOwned(directory, temporary.toByteArray(), identity.st_dev, identity.st_ino)) {
                    failures.add("Temporary identity changed; left untouched")
                }
            }
        }
        if (directory >= 0) attempt { NativeFiles.close(directory) }
        directory = -1
        return failures.takeIf { it.isNotEmpty() }?.joinToString("; ")
    }
}
