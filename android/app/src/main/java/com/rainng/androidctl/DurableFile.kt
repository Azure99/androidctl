package com.rainng.androidctl

import android.system.Os
import android.system.OsConstants
import java.io.File
import java.io.FileOutputStream

internal fun persist(
    file: File,
    bytes: ByteArray,
) {
    val parent = requireNotNull(file.parentFile)
    val temporary = File(parent, ".${file.name}.next")
    FileOutputStream(temporary).use {
        it.write(bytes)
        it.fd.sync()
    }
    Os.rename(temporary.path, file.path)
    val directory = Os.open(parent.path, OsConstants.O_RDONLY, 0)
    try {
        Os.fsync(directory)
    } finally {
        Os.close(directory)
    }
}
