package com.rainng.androidctl

import android.os.BadParcelableException
import com.rainng.androidctl.agent.auth.HostTokenProvisioning
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.w3c.dom.Element
import java.io.File
import java.util.Base64
import javax.xml.parsers.DocumentBuilderFactory

class SetupActivityContractTest {
    @Test
    fun rejectsSetupActionWithoutHostTokenPayload() {
        val validation =
            SetupActivityContract.validate(
                action = SetupActivityContract.ACTION_SETUP,
                extraKeys = emptySet(),
            )

        assertFalse(validation.accepted)
        assertFalse(validation.autoStartServer)
        assertEquals("setup device token is required", validation.reason)
    }

    @Test
    fun rejectsMissingOrUnexpectedAction() {
        val missingAction = SetupActivityContract.validate(action = null)
        val wrongAction = SetupActivityContract.validate(action = "com.example.SETUP")

        assertFalse(missingAction.accepted)
        assertFalse(missingAction.autoStartServer)
        assertEquals("setup action is required", missingAction.reason)
        assertFalse(wrongAction.accepted)
        assertFalse(wrongAction.autoStartServer)
    }

    @Test
    fun acceptsHostTokenPayloadKey() {
        val validation =
            SetupActivityContract.validate(
                action = SetupActivityContract.ACTION_SETUP,
                extraKeys = setOf(HostTokenProvisioning.EXTRA_DEVICE_TOKEN),
            )

        assertTrue(validation.accepted)
        assertTrue(validation.autoStartServer)
    }

    @Test
    fun rejectsUnsupportedPayloadKey() {
        val validation =
            SetupActivityContract.validate(
                action = SetupActivityContract.ACTION_SETUP,
                extraKeys = setOf("token"),
            )

        assertFalse(validation.accepted)
        assertFalse(validation.autoStartServer)
        assertEquals("setup payload is not supported yet", validation.reason)
    }

    @Test
    fun handlerRejectsMissingHostTokenWithoutProvisioningOrStarting() {
        var startServerCount = 0
        var provisionCount = 0

        val validation =
            SetupIntentHandler.handle(
                action = SetupActivityContract.ACTION_SETUP,
                payloadReader = { SetupIntentPayload() },
                startServer = {
                    startServerCount += 1
                },
                provisionDeviceToken = { provisionCount += 1 },
            )

        assertFalse(validation.accepted)
        assertFalse(validation.autoStartServer)
        assertEquals("setup device token is required", validation.reason)
        assertEquals(0, startServerCount)
        assertEquals(0, provisionCount)
    }

    @Test
    fun handlerProvisionsValidHostTokenBeforeStartingServer() {
        val token = validHostToken()
        val operations = mutableListOf<String>()

        val validation =
            SetupIntentHandler.handle(
                action = SetupActivityContract.ACTION_SETUP,
                payloadReader = {
                    SetupIntentPayload(
                        extraKeys = setOf(HostTokenProvisioning.EXTRA_DEVICE_TOKEN),
                        deviceToken = token,
                    )
                },
                startServer = { operations += "start" },
                provisionDeviceToken = { provisionedToken -> operations += "provision:$provisionedToken" },
            )

        assertTrue(validation.accepted)
        assertEquals(listOf("provision:$token", "start"), operations)
    }

    @Test
    fun handlerRejectsInvalidHostTokenWithoutProvisioningOrStarting() {
        var startServerCount = 0
        var provisionCount = 0

        val validation =
            SetupIntentHandler.handle(
                action = SetupActivityContract.ACTION_SETUP,
                payloadReader = {
                    SetupIntentPayload(
                        extraKeys = setOf(HostTokenProvisioning.EXTRA_DEVICE_TOKEN),
                        deviceToken = "short",
                    )
                },
                startServer = { startServerCount += 1 },
                provisionDeviceToken = { provisionCount += 1 },
            )

        assertFalse(validation.accepted)
        assertFalse(validation.autoStartServer)
        assertEquals(0, startServerCount)
        assertEquals(0, provisionCount)
    }

    @Test
    fun handlerDoesNotStartServerForRejectedPayload() {
        var startServerCount = 0

        val validation =
            SetupIntentHandler.handle(
                action = SetupActivityContract.ACTION_SETUP,
                payloadReader = { SetupIntentPayload(extraKeys = setOf("token")) },
                startServer = { startServerCount += 1 },
                provisionDeviceToken = { error("token should not be provisioned") },
            )

        assertFalse(validation.accepted)
        assertFalse(validation.autoStartServer)
        assertEquals(0, startServerCount)
    }

    @Test
    fun handlerDoesNotReadPayloadForRejectedAction() {
        var startServerCount = 0

        val validation =
            SetupIntentHandler.handle(
                action = "com.example.SETUP",
                payloadReader = { error("payload should not be read") },
                startServer = { startServerCount += 1 },
                provisionDeviceToken = { error("token should not be provisioned") },
            )

        assertFalse(validation.accepted)
        assertFalse(validation.autoStartServer)
        assertEquals(0, startServerCount)
    }

    @Test
    fun handlerTreatsBadParcelableExtrasAsInvalidPayload() {
        var startServerCount = 0

        val validation =
            SetupIntentHandler.handle(
                action = SetupActivityContract.ACTION_SETUP,
                payloadReader = { throw BadParcelableException("bad extra") },
                startServer = { startServerCount += 1 },
                provisionDeviceToken = { error("token should not be provisioned") },
            )

        assertFalse(validation.accepted)
        assertFalse(validation.autoStartServer)
        assertEquals("setup payload could not be read", validation.reason)
        assertEquals(0, startServerCount)
    }

    @Test
    fun manifestExportsSetupActivityWithoutExportingRpcService() {
        val manifest = parseManifest()
        val setupActivity = manifest.singleElementByName("activity", ".SetupActivity")
        val agentServerService =
            manifest.singleElementByName(
                "service",
                ".agent.service.AgentServerService",
            )

        assertEquals("true", setupActivity.androidAttribute("exported"))
        assertEquals("singleTop", setupActivity.androidAttribute("launchMode"))
        assertEquals(
            listOf(SetupActivityContract.ACTION_SETUP),
            setupActivity.intentFilterActions(),
        )
        assertEquals(
            listOf("android.intent.category.DEFAULT"),
            setupActivity.intentFilterCategories(),
        )
        assertEquals("false", agentServerService.androidAttribute("exported"))
    }

    private fun parseManifest(): Element =
        DocumentBuilderFactory
            .newInstance()
            .newDocumentBuilder()
            .parse(resolveManifestFile())
            .documentElement

    private fun validHostToken(): String =
        Base64
            .getUrlEncoder()
            .withoutPadding()
            .encodeToString(ByteArray(HostTokenProvisioning.TOKEN_BYTE_LENGTH) { index -> index.toByte() })

    private fun Element.singleElementByName(
        tagName: String,
        androidName: String,
    ): Element {
        val matches =
            children(tagName).filter { element ->
                element.androidAttribute("name") == androidName
            }
        require(matches.size == 1) {
            "Expected exactly one <$tagName android:name=\"$androidName\"> but found ${matches.size}"
        }
        return matches.single()
    }

    private fun Element.intentFilterActions(): List<String> =
        children("intent-filter").flatMap { intentFilter ->
            intentFilter.children("action").map { action -> action.androidAttribute("name") }
        }

    private fun Element.intentFilterCategories(): List<String> =
        children("intent-filter").flatMap { intentFilter ->
            intentFilter.children("category").map { category -> category.androidAttribute("name") }
        }

    private fun Element.children(tagName: String): List<Element> =
        buildList {
            val nodes = getElementsByTagName(tagName)
            for (index in 0 until nodes.length) {
                add(nodes.item(index) as Element)
            }
        }

    private fun Element.androidAttribute(name: String): String =
        getAttributeNS(ANDROID_NAMESPACE, name).takeIf(String::isNotEmpty)
            ?: getAttribute("android:$name")

    private fun resolveManifestFile(): File {
        val workingDirectory =
            File(checkNotNull(System.getProperty("user.dir")) { "user.dir is not set" }).absoluteFile
        val candidateFiles =
            generateSequence(workingDirectory) { current ->
                current.parentFile
            }.flatMap { baseDirectory ->
                sequenceOf(
                    File(baseDirectory, "app/src/main/AndroidManifest.xml"),
                    File(baseDirectory, "src/main/AndroidManifest.xml"),
                )
            }

        return candidateFiles.firstOrNull(File::isFile)
            ?: error(
                "Unable to locate AndroidManifest.xml from ${workingDirectory.absolutePath} " +
                    "using app/src/main or src/main",
            )
    }

    private companion object {
        const val ANDROID_NAMESPACE = "http://schemas.android.com/apk/res/android"
    }
}
