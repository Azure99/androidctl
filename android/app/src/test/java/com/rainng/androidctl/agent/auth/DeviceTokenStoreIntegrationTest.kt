package com.rainng.androidctl.agent.auth

import android.content.Context
import android.content.SharedPreferences
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import org.mockito.Mockito.anyInt
import org.mockito.Mockito.anyString
import org.mockito.Mockito.mock
import org.mockito.Mockito.never
import org.mockito.Mockito.verify
import org.mockito.Mockito.`when`
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey

class DeviceTokenStoreIntegrationTest {
    @Before
    fun setUp() {
        DeviceTokenStoreTestSupport.resetSingleton()
    }

    @After
    fun tearDown() {
        DeviceTokenStoreTestSupport.resetSingleton()
    }

    @Test
    fun initializeBuildsRepositoryFromApplicationContext() {
        val context = mock(Context::class.java)
        val applicationContext = mock(Context::class.java)
        val prefs = mock(SharedPreferences::class.java)
        `when`(context.applicationContext).thenReturn(applicationContext)
        `when`(
            applicationContext.getSharedPreferences(
                SharedPreferencesTokenPersistence.PREFS_NAME,
                Context.MODE_PRIVATE,
            ),
        ).thenReturn(prefs)

        DeviceTokenStore.initialize(context)

        verify(applicationContext)
            .getSharedPreferences(SharedPreferencesTokenPersistence.PREFS_NAME, Context.MODE_PRIVATE)
        verify(context, never()).getSharedPreferences(anyString(), anyInt())
        assertTrue(DeviceTokenStoreTestSupport.currentRepository() is DeviceTokenRepository)
    }

    @Test
    fun loadCurrentTokenDelegatesToRepositoryAfterInitialization() {
        val repository = FakeTokenRepository(initialToken = "stable-token")
        DeviceTokenStoreTestSupport.installRepository(repository)

        assertEquals(DeviceTokenLoadResult.Available("stable-token"), DeviceTokenStore.loadCurrentToken())
        assertEquals(DeviceTokenLoadResult.Available("stable-token"), DeviceTokenStore.loadCurrentToken())
        assertEquals(2, repository.loadCurrentTokenCalls)
    }

    @Test
    fun regenerateTokenReplacesStoredToken() {
        val repository = FakeTokenRepository(initialToken = "token-1", regeneratedToken = "token-2")
        DeviceTokenStoreTestSupport.installRepository(repository)

        assertEquals("token-2", DeviceTokenStore.regenerateToken())
        assertEquals(DeviceTokenLoadResult.Available("token-2"), DeviceTokenStore.loadCurrentToken())
        assertEquals(1, repository.regenerateTokenCalls)
    }

    @Test
    fun replaceTokenDelegatesHostTokenToRepositoryAfterInitialization() {
        val repository = FakeTokenRepository(initialToken = "token-1")
        DeviceTokenStoreTestSupport.installRepository(repository)

        assertEquals("host-token", DeviceTokenStore.replaceToken("host-token"))
        assertEquals(DeviceTokenLoadResult.Available("host-token"), DeviceTokenStore.loadCurrentToken())
        assertEquals(1, repository.replaceTokenCalls)
    }

    @Test
    fun sharedPreferencesPersistenceReadsAndWritesEncryptedTokensOnly() {
        val prefs = mock(SharedPreferences::class.java)
        val editor = mock(SharedPreferences.Editor::class.java)
        `when`(prefs.edit()).thenReturn(editor)
        `when`(editor.putString(org.mockito.Mockito.anyString(), org.mockito.Mockito.anyString())).thenReturn(editor)
        `when`(prefs.getString("device_token_encrypted", null)).thenReturn("enc-token")

        val persistence = SharedPreferencesTokenPersistence(prefs)

        assertEquals("enc-token", persistence.readEncryptedToken())

        persistence.writeEncryptedToken("new-encrypted-token")

        org.mockito.Mockito
            .verify(prefs)
            .getString("device_token_encrypted", null)
        org.mockito.Mockito
            .verify(prefs)
            .edit()
        org.mockito.Mockito
            .verify(editor)
            .putString("device_token_encrypted", "new-encrypted-token")
        org.mockito.Mockito
            .verify(editor, never())
            .remove(org.mockito.Mockito.anyString())
        org.mockito.Mockito
            .verify(editor)
            .apply()
    }

    @Test
    fun androidKeyStoreCipherRoundsTripPayloadWithInjectedJvmCrypto() {
        val secretKey = jvmSecretKey()
        val cipher =
            AndroidKeyStoreTokenCipher(
                cipherFactory = { Cipher.getInstance("AES/GCM/NoPadding") },
                secretKeyProvider = { secretKey },
            )

        val encrypted = cipher.encrypt("device-token")
        val decrypted = cipher.decrypt(encrypted)

        assertTrue(encrypted.startsWith("v1:"))
        assertEquals("device-token", decrypted)
    }

    @Test(expected = IllegalArgumentException::class)
    fun androidKeyStoreCipherRejectsInvalidPayload() {
        val cipher =
            AndroidKeyStoreTokenCipher(
                cipherFactory = { Cipher.getInstance("AES/GCM/NoPadding") },
                secretKeyProvider = { jvmSecretKey() },
            )

        cipher.decrypt("invalid-payload")
    }

    @Test
    fun loadCurrentTokenReturnsBlockedResultWithoutRegenerationWhenRepositoryBlocks() {
        val repository =
            FakeTokenRepository(
                initialLoadResult =
                    DeviceTokenLoadResult.Blocked(DEVICE_TOKEN_DECRYPT_FAILED_MESSAGE),
            )
        DeviceTokenStoreTestSupport.installRepository(repository)

        assertEquals(
            DeviceTokenLoadResult.Blocked(DEVICE_TOKEN_DECRYPT_FAILED_MESSAGE),
            DeviceTokenStore.loadCurrentToken(),
        )
        assertEquals(1, repository.loadCurrentTokenCalls)
        assertEquals(0, repository.regenerateTokenCalls)
    }

    @Test
    fun loadAndRegenerateFailFastWhenStoreIsNotInitialized() {
        assertDeviceTokenStoreNotInitialized {
            DeviceTokenStore.loadCurrentToken()
        }
        assertDeviceTokenStoreNotInitialized {
            DeviceTokenStore.regenerateToken()
        }
        assertDeviceTokenStoreNotInitialized {
            DeviceTokenStore.replaceToken("host-token")
        }
    }

    private fun jvmSecretKey(): SecretKey {
        val keyGenerator = KeyGenerator.getInstance("AES")
        keyGenerator.init(128)
        return keyGenerator.generateKey()
    }

    private fun assertDeviceTokenStoreNotInitialized(block: () -> Unit) {
        try {
            block()
            fail("Expected DeviceTokenStore to fail fast before initialization")
        } catch (error: IllegalStateException) {
            assertEquals("DeviceTokenStore is not initialized", error.message)
        }
    }

    private class FakeTokenRepository(
        initialToken: String = "",
        initialLoadResult: DeviceTokenLoadResult = DeviceTokenLoadResult.Available(initialToken),
        private val regeneratedToken: String = initialToken,
    ) : TokenRepository {
        private var currentTokenValue = initialToken
        private var loadCurrentTokenResult: DeviceTokenLoadResult = initialLoadResult
        var loadCurrentTokenCalls = 0
        var regenerateTokenCalls = 0
        var replaceTokenCalls = 0

        override fun loadCurrentToken(): DeviceTokenLoadResult {
            loadCurrentTokenCalls += 1
            return loadCurrentTokenResult
        }

        override fun regenerateToken(): String {
            regenerateTokenCalls += 1
            currentTokenValue = regeneratedToken
            loadCurrentTokenResult = DeviceTokenLoadResult.Available(currentTokenValue)
            return currentTokenValue
        }

        override fun replaceToken(token: String): String {
            replaceTokenCalls += 1
            currentTokenValue = token
            loadCurrentTokenResult = DeviceTokenLoadResult.Available(currentTokenValue)
            return currentTokenValue
        }
    }
}
