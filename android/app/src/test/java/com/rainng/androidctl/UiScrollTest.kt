package com.rainng.androidctl

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class UiScrollTest {
    @Test fun requestedAdvertisementPrecedesPageAndPagePrecedesGeneric() {
        for ((requested, pageId) in listOf(
            UiAction.SCROLL_UP to android.R.id.accessibilityActionPageUp,
            UiAction.SCROLL_DOWN to android.R.id.accessibilityActionPageDown,
            UiAction.SCROLL_LEFT to android.R.id.accessibilityActionPageLeft,
            UiAction.SCROLL_RIGHT to android.R.id.accessibilityActionPageRight,
        )) {
            val page = UiSemantics.actionName(pageId)
            val target = target("android.widget.ScrollView", requested.wire, page, "scroll-forward")
            assertEquals(requested.platformId, UiScroll.route(target, requested))
            target.put("actions", JSONArray(listOf(page, "scroll-forward")))
            assertEquals(pageId, UiScroll.route(target, requested))
            assertEquals(page, UiSemantics.actionName(requireNotNull(UiScroll.route(target, requested))))
            assertTrue(requested.ready(target))
            assertTrue(UiAdvice.directionAdvice(requested, target).contains(page))
            target.put("enabled", false)
            assertFalse(requested.ready(target))
            target.put("enabled", true).put("visible", false)
            assertFalse(requested.ready(target))
        }
    }

    @Test fun onlyTheThreeProviderClassesMapTheirKnownAxesToAdvertisedGenericActions() {
        for (type in listOf("android.widget.ScrollView", "android.widget.GridView", "androidx.viewpager.widget.ViewPager")) {
            val target = target(type, "scroll-forward", "scroll-backward")
            val requests =
                if (type == "androidx.viewpager.widget.ViewPager") {
                    listOf(UiAction.SCROLL_LEFT to UiAction.SCROLL_BACKWARD, UiAction.SCROLL_RIGHT to UiAction.SCROLL_FORWARD)
                } else {
                    listOf(UiAction.SCROLL_UP to UiAction.SCROLL_BACKWARD, UiAction.SCROLL_DOWN to UiAction.SCROLL_FORWARD)
                }
            for ((requested, selected) in requests) {
                assertTrue(requested.ready(target))
                assertEquals(selected.platformId, UiScroll.route(target, requested))
                target.put("actions", JSONArray())
                assertFalse(requested.ready(target))
                target.put("actions", JSONArray(listOf("scroll-forward", "scroll-backward")))
            }
            val wrongAxis = if (type == "androidx.viewpager.widget.ViewPager") UiAction.SCROLL_DOWN else UiAction.SCROLL_RIGHT
            assertFalse(wrongAxis.ready(target))
        }
    }

    @Test fun otherDirectionalAdvertisementsPreventGenericAndUnknownProvidersDoNotInferAxes() {
        for (boundary in listOf("scroll-up", "scroll-left", "page-up", "page-right")) {
            assertFalse(UiAction.SCROLL_DOWN.ready(target("android.widget.ScrollView", boundary, "scroll-forward")))
        }
        for (type in listOf(
            "android.widget.ListView",
            "androidx.recyclerview.widget.RecyclerView",
            "android.support.v4.view.ViewPager",
            "custom.Scroller",
        )) {
            val target = target(type, "scroll-forward", "scroll-backward")
            assertFalse(UiAction.SCROLL_DOWN.ready(target))
            assertFalse(UiAction.SCROLL_RIGHT.ready(target))
        }
    }

    @Test fun forwardAndBackwardKeepTheirProviderMeaningWithoutPageSubstitution() {
        val target = target("android.widget.ScrollView", "scroll-up", "page-down")
        for (requested in listOf(UiAction.SCROLL_FORWARD, UiAction.SCROLL_BACKWARD)) {
            assertFalse(requested.ready(target))
            target.getJSONArray("actions").put(requested.wire)
            assertEquals(requested.platformId, UiScroll.route(target, requested))
        }
    }

    private fun target(
        type: String,
        vararg actions: String,
    ): JSONObject = node("e1").put("className", type).put("scrollable", true).put("actions", JSONArray(actions.toList()))
}
