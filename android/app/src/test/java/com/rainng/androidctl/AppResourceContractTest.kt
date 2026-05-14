package com.rainng.androidctl

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test
import org.w3c.dom.Element
import java.io.File
import javax.xml.parsers.DocumentBuilderFactory

class AppResourceContractTest {
    @Test
    fun chineseResourcesCoverDefaultTranslatableStrings() {
        val defaultStrings = parseStringResources("values/strings.xml").translatableStrings()
        val chineseStrings = parseStringResources("values-zh/strings.xml").translatableStrings()

        assertEquals(defaultStrings.keys, chineseStrings.keys)
    }

    @Test
    fun localizedStringPlaceholdersStayCompatible() {
        val defaultStrings = parseStringResources("values/strings.xml").translatableStrings()
        val chineseStrings = parseStringResources("values-zh/strings.xml").translatableStrings()

        defaultStrings.forEach { (key, value) ->
            assertEquals(
                "Placeholder mismatch for $key",
                value.placeholders(),
                chineseStrings.getValue(key).placeholders(),
            )
        }
    }

    @Test
    fun defaultTranslatableStringsRemainEnglishFallback() {
        val defaultStrings = parseStringResources("values/strings.xml").translatableStrings()
        val cjkPattern = Regex("""[\u3400-\u9FFF]""")

        defaultStrings.forEach { (key, value) ->
            assertFalse("Default string $key contains CJK text: $value", cjkPattern.containsMatchIn(value))
        }
    }

    @Test
    fun manifestAndServiceXmlKeepResourceContracts() {
        val manifest = parseXml("AndroidManifest.xml")
        val application = manifest.singleChild("application")
        val accessibilityService =
            manifest.singleElementByAndroidName(
                tagName = "service",
                androidName = ".agent.service.DeviceAccessibilityService",
            )
        val rpcService =
            manifest.singleElementByAndroidName(
                tagName = "service",
                androidName = ".agent.service.AgentServerService",
            )
        val accessibilityConfig = parseXml("xml/device_accessibility_service_config.xml")

        assertEquals("@string/app_name", application.androidAttribute("label"))
        assertEquals("@string/accessibility_service_label", accessibilityService.androidAttribute("label"))
        assertEquals("@string/accessibility_service_description", accessibilityConfig.androidAttribute("description"))
        assertEquals("false", rpcService.androidAttribute("exported"))
    }

    private fun parseStringResources(relativePath: String): List<StringResource> {
        val resources = parseXml(relativePath)
        return resources.children("string").map { element ->
            StringResource(
                key = element.getAttribute("name"),
                value = element.textContent,
                translatable = element.getAttribute("translatable") != "false",
            )
        }
    }

    private fun parseXml(relativePath: String): Element =
        DocumentBuilderFactory
            .newInstance()
            .newDocumentBuilder()
            .parse(resolveMainFile(relativePath))
            .documentElement

    private fun List<StringResource>.translatableStrings(): Map<String, String> =
        filter(StringResource::translatable).associate { resource -> resource.key to resource.value }

    private fun String.placeholders(): List<String> =
        placeholderPattern
            .findAll(this)
            .map { match -> match.value }
            .toList()

    private fun Element.singleChild(tagName: String): Element {
        val matches = children(tagName)
        require(matches.size == 1) {
            "Expected exactly one <$tagName> but found ${matches.size}"
        }
        return matches.single()
    }

    private fun Element.singleElementByAndroidName(
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

    private fun resolveMainFile(relativePath: String): File {
        val workingDirectory =
            File(checkNotNull(System.getProperty("user.dir")) { "user.dir is not set" }).absoluteFile
        val candidateFiles =
            generateSequence(workingDirectory) { current ->
                current.parentFile
            }.map { baseDirectory ->
                File(baseDirectory, "app/src/main/res/$relativePath")
                    .takeIf(File::isFile)
                    ?: File(baseDirectory, "app/src/main/$relativePath").takeIf(File::isFile)
                    ?: File(baseDirectory, "src/main/res/$relativePath").takeIf(File::isFile)
                    ?: File(baseDirectory, "src/main/$relativePath").takeIf(File::isFile)
            }

        return candidateFiles.firstOrNull { candidate -> candidate != null }
            ?: error(
                "Unable to locate $relativePath from ${workingDirectory.absolutePath} " +
                    "using app/src/main or src/main",
            )
    }

    private data class StringResource(
        val key: String,
        val value: String,
        val translatable: Boolean,
    )

    private companion object {
        const val ANDROID_NAMESPACE = "http://schemas.android.com/apk/res/android"

        val placeholderPattern = Regex("""%(?:\d+\$)?[sd]""")
    }
}
