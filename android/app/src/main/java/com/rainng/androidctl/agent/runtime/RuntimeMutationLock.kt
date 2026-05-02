package com.rainng.androidctl.agent.runtime

internal class RuntimeMutationLock {
    private val lock = Any()

    fun <T> synchronize(action: () -> T): T = synchronized(lock, action)
}
