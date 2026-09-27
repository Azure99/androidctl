package com.rainng.androidctl

import androidx.annotation.Keep

@Keep
internal object NativeFiles {
    init {
        System.loadLibrary("androidctl_files")
    }

    external fun openDirectory(path: ByteArray): Int

    external fun openLockFile(path: ByteArray): Int

    external fun checkLockFile(
        fd: Int,
        path: ByteArray,
    ): LongArray

    external fun tryExclusiveLock(fd: Int): Boolean

    external fun slotOwners(fd: Int): IntArray

    fun createTemporary(
        directory: Int,
        name: ByteArray,
    ): Int = openFile(directory, name, create = true)

    fun openRegular(
        directory: Int,
        name: ByteArray,
    ): Int = openFile(directory, name, create = false)

    private external fun openFile(
        directory: Int,
        name: ByteArray,
        create: Boolean,
    ): Int

    external fun targetExists(
        directory: Int,
        name: ByteArray,
    ): Boolean

    data class PublicationResult(
        val errorNumber: Int,
        // The final publication path; an EINVAL fallback starts with a new preflight.
        val publicationAttempted: Boolean,
    )

    fun publishFile(
        directory: Int,
        from: ByteArray,
        to: ByteArray,
        replace: Boolean,
    ): PublicationResult {
        val status = publish(directory, from, to, replace)
        return PublicationResult(
            errorNumber = kotlin.math.abs(status),
            publicationAttempted = status >= 0,
        )
    }

    // Negative errno is a preflight rejection; positive errno follows a rename attempt.
    // Path preparation throws before publication; attempted publication reports its status normally.
    private external fun publish(
        directory: Int,
        from: ByteArray,
        to: ByteArray,
        replace: Boolean,
    ): Int

    external fun removeOwned(
        directory: Int,
        name: ByteArray,
        device: Long,
        inode: Long,
    ): Boolean

    external fun close(fd: Int)
}
