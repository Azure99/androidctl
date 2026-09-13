package com.rainng.androidctl

import java.io.File
import java.io.IOException

@Suppress("SwallowedException") // Close failures are attached to the original constructor failure.
internal class WorkerLock {
    val directory = File(WORKER_DIRECTORY)
    private val path = File(directory, "worker.lock")
    private val fd: Int

    init {
        if (!directory.exists() && !directory.mkdir()) throw Rejected("workerDirectory", "Cannot create worker directory")
        fd = NativeFiles.openLockFile(path.path.toByteArray())
        try {
            if (!NativeFiles.tryExclusiveLock(fd)) throw Rejected("workerBusy", "A worker still owns the device lock")
            check()
        } catch (error: Exception) {
            try {
                NativeFiles.close(fd)
            } catch (cleanup: IOException) {
                error.addSuppressed(cleanup)
            }
            throw error
        }
    }

    fun check() {
        try {
            NativeFiles.checkLockFile(fd, path.path.toByteArray())
        } catch (error: IOException) {
            throw Rejected("workerLockChanged", "Worker lock $path unavailable; new work is refused: ${message(error)}")
        }
    }
}

private const val WORKER_DIRECTORY = "/data/local/tmp/androidctl"
