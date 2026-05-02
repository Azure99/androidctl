package com.rainng.androidctl.agent.auth

import java.lang.reflect.Field

internal object DeviceTokenStoreTestSupport {
    fun resetSingleton() {
        setRepository(null)
    }

    fun installRepository(repository: TokenRepository) {
        setRepository(repository)
    }

    fun currentRepository(): TokenRepository? =
        synchronized(DeviceTokenStore) {
            repositoryField.get(DeviceTokenStore) as? TokenRepository
        }

    private fun setRepository(repository: TokenRepository?) {
        synchronized(DeviceTokenStore) {
            repositoryField.set(DeviceTokenStore, repository)
        }
    }

    private val repositoryField: Field =
        DeviceTokenStore::class
            .java
            .getDeclaredField("repository")
            .apply { isAccessible = true }
}
