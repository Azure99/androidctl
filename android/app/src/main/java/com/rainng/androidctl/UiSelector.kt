package com.rainng.androidctl

import org.json.JSONObject

internal class UiSelector(
    selector: JSONObject,
) {
    private val fields =
        selector.keys().asSequence().associate { field ->
            if (field !in listOf("role", "name", "id", "text")) throw Rejected("selector", "Unknown selector field: $field")
            field to forms(selector.getString(field))
        }

    fun matches(node: JSONObject): Boolean? {
        if (!node.getBoolean("visible")) return false
        val answers =
            fields.map { (field, expected) -> matchField(node, field, expected) }
        return if (false in answers) {
            false
        } else if (null in answers) {
            null
        } else {
            true
        }
    }

    private fun matchField(
        node: JSONObject,
        field: String,
        expected: Set<String>,
    ): Boolean? {
        val (known, actual) =
            when (field) {
                "role" -> UiSemantics.roleComplete(node) to node.getJSONObject("view").text(field)
                "name" -> node.getJSONObject("view").getBoolean("nameComplete") to node.getJSONObject("view").text(field)
                "id" -> !node.unavailable("resourceId") to node.text("resourceId")
                else -> !node.unavailable("text") to node.text("text")
            }
        return if (!known) null else expected.any { actual == it || (field == "id" && ":id/" !in it && actual.endsWith(":id/$it")) }
    }

    companion object {
        private const val MAX_CANDIDATES = 5
        private const val SUMMARY_CHARS = 256
        private const val HEX = 16
        private val xmlCharacters = Regex("[\\x{9}\\x{A}\\x{D}\\x{20}-\\x{D7FF}\\x{E000}-\\x{FFFD}\\x{10000}-\\x{10FFFF}]")
        private val entity = Regex("&(?:amp|lt|gt|quot|apos|#[0-9]+|#x[0-9a-fA-F]+);")

        fun ambiguity(
            targets: List<JSONObject>,
            incomplete: Boolean,
        ): String =
            buildString {
                append("${targets.size} targets match; use a ref or narrower scope")
                for (node in targets.take(MAX_CANDIDATES)) {
                    val view = node.getJSONObject("view")
                    val ref =
                        if (node.optBoolean("refAvailable")) {
                            "ref=${node.getString("ref") }"
                        } else {
                            "ref-unavailable=${summary(node.text("refReason"))}"
                        }
                    append("\ncandidate $ref role=${summary(view.text("role"))} name=${summary(view.text("name"))}")
                    append(" id=${summary(node.text("resourceId"))} window=${node.opt("windowId")}")
                    append(rowSummary(node.getJSONObject("row")))
                }
                if (targets.size > MAX_CANDIDATES) append("\n${targets.size - MAX_CANDIDATES} candidates omitted; inspect snapshot")
                if (incomplete) append("\nObservation or selector fields incomplete; additional matches may be missing")
            }

        private fun rowSummary(row: JSONObject): String =
            buildString {
                if (!row.getBoolean("complete")) {
                    append(" row-unavailable")
                } else {
                    row.text("label").takeIf { it.isNotEmpty() }?.let { append(" row=${summary(it)}") }
                    row.optJSONObject("nearest")?.let { position ->
                        for (axis in listOf("row", "column")) {
                            if (!position.isNull(axis) && position.getInt(axis) >= 0) append(" ${axis}Index=${position.getInt(axis)}")
                        }
                    }
                }
            }

        private fun summary(value: String): String = JSONObject.quote(summaryText(value))

        fun summaryText(value: String): String =
            if (value.length > SUMMARY_CHARS) {
                value.take(SUMMARY_CHARS) + "... (${value.length - SUMMARY_CHARS} chars omitted)"
            } else {
                value
            }

        fun focused(node: JSONObject): Boolean = node.getBoolean("focused")

        fun forms(value: String): Set<String> =
            setOf(
                value,
                entity.replace(value) { match ->
                    when (match.value) {
                        "&amp;" -> "&"
                        "&lt;" -> "<"
                        "&gt;" -> ">"
                        "&quot;" -> "\""
                        "&apos;" -> "'"
                        else -> {
                            val content = match.value.removePrefix("&#").removeSuffix(";")
                            val code = if (content.startsWith("x")) content.drop(1).toIntOrNull(HEX) else content.toIntOrNull()
                            code
                                ?.takeIf(Character::isValidCodePoint)
                                ?.let { String(Character.toChars(it)) }
                                ?.takeIf(xmlCharacters::matches) ?: match.value
                        }
                    }
                },
            )
    }
}
