package com.rainng.androidctl

import androidx.annotation.Keep

@Keep
internal object NativeFiles {
    init {
        System.loadLibrary("androidctl_files")
    }

    external fun openLockFile(path: ByteArray): Int

    external fun checkLockFile(
        fd: Int,
        path: ByteArray,
    ): LongArray

    external fun tryExclusiveLock(fd: Int): Boolean

    external fun slotOwners(fd: Int): IntArray

    external fun close(fd: Int)
}
