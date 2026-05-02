package com.rainng.androidctl.agent.auth

import android.content.Context
import android.content.SharedPreferences
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import androidx.core.content.edit
import com.rainng.androidctl.agent.logging.AgentLog
import java.security.GeneralSecurityException
import java.security.KeyStore
import java.util.Base64
import java.util.UUID
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

object DeviceTokenStore {
    @Volatile
    private var repository: TokenRepository? = null

    @Synchronized
    fun initialize(context: Context) {
        repository = createDefaultRepository(context.applicationContext)
    }

    internal fun loadCurrentToken(): DeviceTokenLoadResult {
        val currentRepository = checkNotNull(repository) { "DeviceTokenStore is not initialized" }
        return currentRepository.loadCurrentToken()
    }

    fun regenerateToken(): String {
        val currentRepository = checkNotNull(repository) { "DeviceTokenStore is not initialized" }
        return currentRepository.regenerateToken()
    }

    fun replaceToken(token: String): String {
        val currentRepository = checkNotNull(repository) { "DeviceTokenStore is not initialized" }
        return currentRepository.replaceToken(token)
    }

    private fun createDefaultRepository(applicationContext: Context): TokenRepository =
        DeviceTokenRepository(
            persistence = SharedPreferencesTokenPersistence(applicationContext),
            cipher = AndroidKeyStoreTokenCipher(),
            onDecryptFailure = { error ->
                AgentLog.w("stored device token could not be decrypted", error)
            },
        )
}

internal const val DEVICE_TOKEN_DECRYPT_FAILED_MESSAGE = "stored device token could not be decrypted"

internal sealed interface DeviceTokenLoadResult {
    data class Available(
        val token: String,
    ) : DeviceTokenLoadResult

    data class Blocked(
        val message: String,
    ) : DeviceTokenLoadResult
}

internal interface TokenRepository {
    fun loadCurrentToken(): DeviceTokenLoadResult

    fun regenerateToken(): String

    fun replaceToken(token: String): String
}

internal class DeviceTokenRepository(
    private val persistence: TokenPersistence,
    private val cipher: TokenCipher,
    private val tokenFactory: () -> String = { UUID.randomUUID().toString() },
    private val onDecryptFailure: (Exception) -> Unit = {},
) : TokenRepository {
    @Synchronized
    override fun loadCurrentToken(): DeviceTokenLoadResult {
        val encryptedToken = persistence.readEncryptedToken()?.takeIf(String::isNotBlank)
        if (encryptedToken != null) {
            return loadEncryptedToken(encryptedToken)
        }

        return DeviceTokenLoadResult.Available(persistGeneratedToken(tokenFactory()))
    }

    @Synchronized
    override fun regenerateToken(): String = persistGeneratedToken(tokenFactory())

    @Synchronized
    override fun replaceToken(token: String): String = persistRequiredToken(token)

    private fun loadEncryptedToken(encryptedToken: String): DeviceTokenLoadResult =
        try {
            DeviceTokenLoadResult.Available(cipher.decrypt(encryptedToken))
        } catch (error: GeneralSecurityException) {
            handleDecryptFailure(error)
        } catch (error: IllegalArgumentException) {
            handleDecryptFailure(error)
        } catch (error: IllegalStateException) {
            handleDecryptFailure(error)
        }

    private fun persistGeneratedToken(token: String): String {
        val normalizedToken = token.takeIf(String::isNotBlank) ?: tokenFactory()
        return writeToken(normalizedToken)
    }

    private fun persistRequiredToken(token: String): String {
        require(token.isNotBlank()) { "device token cannot be blank" }
        return writeToken(token)
    }

    private fun writeToken(normalizedToken: String): String {
        val encryptedToken = cipher.encrypt(normalizedToken)
        persistence.writeEncryptedToken(encryptedToken)
        return normalizedToken
    }

    private fun handleDecryptFailure(error: Exception): DeviceTokenLoadResult.Blocked {
        onDecryptFailure(error)
        return DeviceTokenLoadResult.Blocked(DEVICE_TOKEN_DECRYPT_FAILED_MESSAGE)
    }
}

internal interface TokenPersistence {
    fun readEncryptedToken(): String?

    fun writeEncryptedToken(token: String)
}

internal class SharedPreferencesTokenPersistence(
    private val prefs: SharedPreferences,
) : TokenPersistence {
    constructor(context: Context) : this(context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE))

    override fun readEncryptedToken(): String? = prefs.getString(KEY_ENCRYPTED_DEVICE_TOKEN, null)

    override fun writeEncryptedToken(token: String) {
        prefs.edit {
            putString(KEY_ENCRYPTED_DEVICE_TOKEN, token)
        }
    }

    companion object {
        const val PREFS_NAME = "device_agent_auth"
        private const val KEY_ENCRYPTED_DEVICE_TOKEN = "device_token_encrypted"
    }
}

internal interface TokenCipher {
    fun encrypt(plaintext: String): String

    fun decrypt(ciphertext: String): String
}

internal class AndroidKeyStoreTokenCipher(
    private val cipherFactory: () -> Cipher = { Cipher.getInstance(TRANSFORMATION) },
    private val secretKeyProvider: () -> SecretKey = ::getOrCreateAndroidKeyStoreSecretKey,
    private val base64Encoder: Base64.Encoder = Base64.getEncoder(),
    private val base64Decoder: Base64.Decoder = Base64.getDecoder(),
) : TokenCipher {
    override fun encrypt(plaintext: String): String {
        val cipher = cipherFactory()
        cipher.init(Cipher.ENCRYPT_MODE, secretKeyProvider())
        val encryptedBytes = cipher.doFinal(plaintext.toByteArray(Charsets.UTF_8))
        val iv = base64Encoder.encodeToString(cipher.iv)
        val payload = base64Encoder.encodeToString(encryptedBytes)
        return "$VERSION_PREFIX:$iv:$payload"
    }

    override fun decrypt(ciphertext: String): String {
        val parts = ciphertext.split(':', limit = 3)
        require(parts.size == ENCRYPTED_TOKEN_PART_COUNT && parts[0] == VERSION_PREFIX) { "invalid encrypted token payload" }

        val iv = base64Decoder.decode(parts[1])
        val payload = base64Decoder.decode(parts[2])
        val cipher = cipherFactory()
        cipher.init(Cipher.DECRYPT_MODE, secretKeyProvider(), GCMParameterSpec(GCM_TAG_LENGTH_BITS, iv))
        return cipher.doFinal(payload).toString(Charsets.UTF_8)
    }

    companion object {
        private const val ANDROID_KEYSTORE = "AndroidKeyStore"
        private const val TRANSFORMATION = "AES/GCM/NoPadding"
        private const val KEY_ALIAS = "com.rainng.androidctl.device_token"
        private const val KEY_SIZE_BITS = 256
        private const val GCM_TAG_LENGTH_BITS = 128
        private const val ENCRYPTED_TOKEN_PART_COUNT = 3
        private const val VERSION_PREFIX = "v1"

        @Synchronized
        private fun getOrCreateAndroidKeyStoreSecretKey(): SecretKey {
            val keyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
            val existingKey = (keyStore.getEntry(KEY_ALIAS, null) as? KeyStore.SecretKeyEntry)?.secretKey
            if (existingKey != null) {
                return existingKey
            }

            val keyGenerator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE)
            keyGenerator.init(
                KeyGenParameterSpec
                    .Builder(
                        KEY_ALIAS,
                        KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
                    ).setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .setKeySize(KEY_SIZE_BITS)
                    .setRandomizedEncryptionRequired(true)
                    .build(),
            )
            return keyGenerator.generateKey()
        }
    }
}
