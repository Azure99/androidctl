package com.rainng.androidctl.agent.auth

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test
import org.w3c.dom.Element
import java.io.ByteArrayInputStream
import java.io.File
import javax.xml.parsers.DocumentBuilderFactory

class DeviceTokenRepositoryTest {
    @Test
    fun loadCurrentTokenReturnsDecryptedStoredToken() {
        val persistence = FakeTokenPersistence(encryptedToken = "enc:current-token")
        val repository =
            DeviceTokenRepository(
                persistence = persistence,
                cipher = FakeTokenCipher(),
                tokenFactory = { "generated-token" },
            )

        val token = repository.loadCurrentToken()

        assertEquals(DeviceTokenLoadResult.Available("current-token"), token)
        assertEquals("enc:current-token", persistence.encryptedToken)
    }

    @Test
    fun loadCurrentTokenGeneratesAndPersistsTokenWhenEncryptedTokenIsMissing() {
        val persistence = FakeTokenPersistence()
        val repository =
            DeviceTokenRepository(
                persistence = persistence,
                cipher = FakeTokenCipher(),
                tokenFactory = { "generated-token" },
            )

        val token = repository.loadCurrentToken()

        assertEquals(DeviceTokenLoadResult.Available("generated-token"), token)
        assertEquals("enc:generated-token", persistence.encryptedToken)
    }

    @Test
    fun loadCurrentTokenReturnsBlockedWhenEncryptedPayloadIsInvalid() {
        val persistence = FakeTokenPersistence(encryptedToken = "broken-payload")
        var generatedTokenCount = 0
        val repository =
            DeviceTokenRepository(
                persistence = persistence,
                cipher = FakeTokenCipher(failingPayloads = setOf("broken-payload")),
                tokenFactory = {
                    generatedTokenCount += 1
                    "generated-token"
                },
            )

        val token = repository.loadCurrentToken()

        assertEquals(
            DeviceTokenLoadResult.Blocked(DEVICE_TOKEN_DECRYPT_FAILED_MESSAGE),
            token,
        )
        assertEquals("broken-payload", persistence.encryptedToken)
        assertEquals(0, generatedTokenCount)
    }

    @Test
    fun regenerateTokenReplacesStoredValue() {
        val persistence = FakeTokenPersistence(encryptedToken = "enc:old-token")
        val repository =
            DeviceTokenRepository(
                persistence = persistence,
                cipher = FakeTokenCipher(),
                tokenFactory = { "new-token" },
            )

        val token = repository.regenerateToken()

        assertEquals("new-token", token)
        assertEquals("enc:new-token", persistence.encryptedToken)
    }

    @Test
    fun replaceTokenPersistsHostProvidedTokenWithoutGenerating() {
        val persistence = FakeTokenPersistence(encryptedToken = "enc:old-token")
        var generatedTokenCount = 0
        val repository =
            DeviceTokenRepository(
                persistence = persistence,
                cipher = FakeTokenCipher(),
                tokenFactory = {
                    generatedTokenCount += 1
                    "generated-token"
                },
            )

        val token = repository.replaceToken("host-token")

        assertEquals("host-token", token)
        assertEquals("enc:host-token", persistence.encryptedToken)
        assertEquals(0, generatedTokenCount)
    }

    @Test
    fun replaceTokenRejectsBlankTokenWithoutChangingStoredValue() {
        val persistence = FakeTokenPersistence(encryptedToken = "enc:old-token")
        val repository =
            DeviceTokenRepository(
                persistence = persistence,
                cipher = FakeTokenCipher(),
                tokenFactory = { "generated-token" },
            )

        assertThrows(IllegalArgumentException::class.java) {
            repository.replaceToken("")
        }

        assertEquals("enc:old-token", persistence.encryptedToken)
    }

    @Test
    fun backupAndTransferRulesExcludeTheDeviceTokenSharedPrefsFile() {
        val expectedExclusions = listOf(expectedDeviceTokenSharedPrefsFile)

        assertEquals(
            "backup_rules.xml full-backup-content sharedpref exclusions drifted",
            expectedExclusions,
            sharedPrefExclusionPaths(
                xmlFileName = "backup_rules.xml",
                sectionName = "full-backup-content",
            ),
        )
        assertEquals(
            "data_extraction_rules.xml cloud-backup sharedpref exclusions drifted",
            expectedExclusions,
            sharedPrefExclusionPaths(
                xmlFileName = "data_extraction_rules.xml",
                sectionName = "cloud-backup",
            ),
        )
        assertEquals(
            "data_extraction_rules.xml device-transfer sharedpref exclusions drifted",
            expectedExclusions,
            sharedPrefExclusionPaths(
                xmlFileName = "data_extraction_rules.xml",
                sectionName = "device-transfer",
            ),
        )
    }

    @Test
    fun sharedPrefExclusionPathsRequiresDirectChildExcludeElements() {
        val section =
            parseSection(
                xmlContent =
                    """
                    <data-extraction-rules>
                        <cloud-backup>
                            <nested>
                                <exclude
                                    domain="sharedpref"
                                    path="$expectedDeviceTokenSharedPrefsFile"
                                />
                            </nested>
                        </cloud-backup>
                    </data-extraction-rules>
                    """.trimIndent(),
                sectionName = "cloud-backup",
            )

        assertEquals(emptyList<String>(), sharedPrefExclusionPaths(section))
    }

    private class FakeTokenPersistence(
        var encryptedToken: String? = null,
    ) : TokenPersistence {
        override fun readEncryptedToken(): String? = encryptedToken

        override fun writeEncryptedToken(token: String) {
            encryptedToken = token
        }
    }

    private class FakeTokenCipher(
        private val failingPayloads: Set<String> = emptySet(),
    ) : TokenCipher {
        override fun encrypt(plaintext: String): String = "enc:$plaintext"

        override fun decrypt(ciphertext: String): String {
            require(ciphertext !in failingPayloads) { "unable to decrypt" }
            return ciphertext.removePrefix("enc:")
        }
    }

    private fun sharedPrefExclusionPaths(
        xmlFileName: String,
        sectionName: String,
    ): List<String> {
        val document =
            DocumentBuilderFactory
                .newInstance()
                .newDocumentBuilder()
                .parse(resolveXmlFile(xmlFileName))

        return document
            .getElementsByTagName(sectionName)
            .run {
                require(length == 1) {
                    "Expected exactly one <$sectionName> in $xmlFileName but found $length"
                }
                sharedPrefExclusionPaths(item(0) as Element)
            }
    }

    private fun sharedPrefExclusionPaths(section: Element): List<String> =
        buildList {
            val children = section.childNodes
            for (index in 0 until children.length) {
                val child = children.item(index) as? Element ?: continue
                if (child.tagName == "exclude" && child.getAttribute("domain") == "sharedpref") {
                    add(child.getAttribute("path"))
                }
            }
        }

    private fun parseSection(
        xmlContent: String,
        sectionName: String,
    ): Element =
        DocumentBuilderFactory
            .newInstance()
            .newDocumentBuilder()
            .parse(ByteArrayInputStream(xmlContent.toByteArray(Charsets.UTF_8)))
            .getElementsByTagName(sectionName)
            .run {
                require(length == 1) {
                    "Expected exactly one <$sectionName> in inline XML but found $length"
                }
                item(0) as Element
            }

    private fun resolveXmlFile(xmlFileName: String): File {
        val workingDirectory =
            File(checkNotNull(System.getProperty("user.dir")) { "user.dir is not set" }).absoluteFile
        val candidateFiles =
            generateSequence(workingDirectory) { current ->
                current.parentFile
            }.flatMap { baseDirectory ->
                sequenceOf(
                    File(baseDirectory, "app/src/main/res/xml/$xmlFileName"),
                    File(baseDirectory, "src/main/res/xml/$xmlFileName"),
                )
            }

        return candidateFiles.firstOrNull(File::isFile)
            ?: error(
                "Unable to locate $xmlFileName from ${workingDirectory.absolutePath} " +
                    "using app/src/main/res/xml or src/main/res/xml",
            )
    }

    private companion object {
        val expectedDeviceTokenSharedPrefsFile = "${SharedPreferencesTokenPersistence.PREFS_NAME}.xml"
    }
}
