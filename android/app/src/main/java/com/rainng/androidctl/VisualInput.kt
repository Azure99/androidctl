package com.rainng.androidctl

import android.graphics.Bitmap
import android.os.Build
import android.os.SystemClock
import android.view.accessibility.AccessibilityWindowInfo
import org.json.JSONArray
import org.json.JSONObject

// Preserve input facts across platform, Binder, and image encoding failures.
internal class VisualInput(
    private val backend: UiBackend,
) {
    @Volatile private var image: JSONObject? = null
    private var next = 1L

    fun invalidate() {
        image = null
    }

    fun status(owned: Boolean): JSONObject {
        val current = if (owned) image else null
        return json(
            "apiSupported" to (Build.VERSION.SDK_INT >= MIN_COORDINATE_API),
            "image" to
                when {
                    current == null -> "not checked"
                    SystemClock.elapsedRealtime() - current.getLong("at") > IMAGE_TTL_MS -> "expired"
                    else -> "qualified at capture"
                },
        )
    }

    fun screenshot(run: UiRun): Pair<ByteArray, JSONObject> {
        invalidate()
        backend.current(run)
        var mappingError: String? = null
        val before =
            try {
                geometry(run)
            } catch (error: Exception) {
                mappingError = message(error)
                null
            }
        backend.current(run)
        val started = SystemClock.elapsedRealtime()
        val bitmap = backend.automation(run).takeScreenshot() ?: throw Rejected("screenshotUnavailable", "Platform returned no screenshot")
        try {
            backend.current(run)
            val out = BoundedBytes(Limits.IMAGE_BYTES)
            if (!bitmap.compress(
                    Bitmap.CompressFormat.PNG,
                    PNG_QUALITY,
                    out,
                )
            ) {
                throw Rejected("screenshotUnavailable", "PNG encoding failed")
            }
            backend.current(run)
            val after =
                try {
                    geometry(run)
                } catch (error: Exception) {
                    mappingError = message(error)
                    null
                }
            val known =
                before != null &&
                    after != null &&
                    before.toString() == after.toString() &&
                    after.getInt("width") == bitmap.width &&
                    after.getInt("height") == bitmap.height
            val meta =
                json(
                    "id" to "i${next++}",
                    "width" to bitmap.width,
                    "height" to bitmap.height,
                    "at" to started,
                    "mappingKnown" to known,
                    "mappingError" to mappingError,
                    "shape" to after,
                )
            if (known) image = meta
            return out.toByteArray() to meta
        } finally {
            bitmap.recycle()
        }
    }

    @Suppress("DEPRECATION")
    private fun geometry(run: UiRun): JSONObject {
        if (Build.VERSION.SDK_INT < MIN_COORDINATE_API) {
            throw Rejected("coordinateApi", "Coordinate input is not enabled on this Android API")
        }
        backend.current(run)
        val shape = displayShape()
        val platform = backend.platform(run)
        val mag = platform.magnification()
        if (mag.getBoolean("activated") || mag.getDouble("scale") != 1.0) {
            throw Rejected("magnificationActive", "Coordinate input requires inactive magnification")
        }
        return shape.put("windows", windowShapes(run, platform)).put("magnification", mag)
    }

    private fun displayShape(): JSONObject {
        val facts = backend.deviceFacts()
        val shape = JSONObject()
        for (field in listOf(
            "displayId",
            "width",
            "height",
            "rotation",
            "userId",
            "interactive",
            "keyguardShowing",
            "keyguardSecure",
            "deviceLocked",
        )) {
            if (facts.isNull(field)) throw Rejected("mappingUnknown", "Required $field is unavailable")
            shape.put(field, facts.get(field))
        }
        if (shape.getInt("displayId") != 0 || shape.getInt("userId") != 0) {
            throw Rejected("mappingUnknown", "Coordinate input requires the default display and user")
        }
        return shape
    }

    @Suppress("DEPRECATION")
    private fun windowShapes(
        run: UiRun,
        platform: AccessibilityConnection,
    ): JSONArray {
        val windows = platform.windows()
        try {
            if (windows.isEmpty() || windows.size > Limits.WINDOWS) throw Rejected("mappingUnknown", "Window mapping is incomplete")
            val mapped =
                windows.sortedBy { it.id }.map { window ->
                    backend.current(run)
                    if (window.type == AccessibilityWindowInfo.TYPE_MAGNIFICATION_OVERLAY) {
                        throw Rejected("magnificationOverlay", "Magnification overlay is still present")
                    }
                    val transform = platform.transformation(window.id)
                    val m = transform.getJSONArray("magnification")
                    if (m.getDouble(0) != 1.0 || m.getDouble(1) != 0.0 || m.getDouble(2) != 0.0) {
                        throw Rejected("magnificationActive", "Window magnification is active")
                    }
                    NodeFacts.window(window).apply {
                        remove("title")
                        remove("titleTruncated")
                        put("transformation", transform)
                    }
                }
            return JSONArray(mapped)
        } finally {
            windows.forEach { it.recycle() }
        }
    }

    companion object {
        const val MIN_COORDINATE_API = 34
        private const val IMAGE_TTL_MS = 120000L
        private const val PNG_QUALITY = 100
    }
}
