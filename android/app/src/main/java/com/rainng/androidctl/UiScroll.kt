package com.rainng.androidctl

import org.json.JSONObject

internal object UiScroll {
    fun route(
        node: JSONObject,
        requested: UiAction,
    ): Int? {
        val advertised = node.getJSONArray("actions")
        val actions = (0 until advertised.length()).map(advertised::getString)
        if (requested.platformName in actions) return requested.platformId
        val pageId =
            when (requested) {
                UiAction.SCROLL_UP -> android.R.id.accessibilityActionPageUp
                UiAction.SCROLL_DOWN -> android.R.id.accessibilityActionPageDown
                UiAction.SCROLL_LEFT -> android.R.id.accessibilityActionPageLeft
                UiAction.SCROLL_RIGHT -> android.R.id.accessibilityActionPageRight
                else -> return null
            }
        if (UiSemantics.actionName(pageId) in actions) return pageId
        if (actions.any { it in listOf("scroll-up", "scroll-down", "scroll-left", "scroll-right") || it.startsWith("page-") }) return null
        val generic = genericAction(node.text("className"), requested)
        return generic?.takeIf { it.platformName in actions }?.platformId
    }

    private fun genericAction(
        type: String,
        requested: UiAction,
    ): UiAction? =
        when (type) {
            "android.widget.ScrollView", "android.widget.GridView" ->
                when (requested) {
                    UiAction.SCROLL_UP -> UiAction.SCROLL_BACKWARD
                    UiAction.SCROLL_DOWN -> UiAction.SCROLL_FORWARD
                    else -> null
                }
            "androidx.viewpager.widget.ViewPager" ->
                when (requested) {
                    UiAction.SCROLL_LEFT -> UiAction.SCROLL_BACKWARD
                    UiAction.SCROLL_RIGHT -> UiAction.SCROLL_FORWARD
                    else -> null
                }
            else -> null
        }
}
