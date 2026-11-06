package com.rainng.androidctl

import android.app.UiAutomation
import android.graphics.Bitmap
import android.os.Build
import android.os.SystemClock
import android.view.InputDevice
import android.view.MotionEvent
import android.view.accessibility.AccessibilityWindowInfo
import org.json.JSONArray
import org.json.JSONObject
import java.lang.reflect.Method

// Preserve input facts across platform, Binder, and image encoding failures.
internal class VisualInput(
    private val backend: UiBackend,
    initial: Long = 1L,
) {
    @Volatile private var image: JSONObject? = null
    private var next = initial

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

    @Suppress("CyclomaticComplexMethod") // Each image admission failure has a distinct actionable diagnosis.
    fun perform(run: UiRun): JSONObject {
        run.select(run.command)
        val before = backend.snapshots.tree
        backend.current(run)
        if (Build.VERSION.SDK_INT < MIN_COORDINATE_API) {
            throw Rejected("coordinateApi", "Coordinate input requires Android API 34+; use semantic actions on this device")
        }
        val original = image ?: throw Rejected("staleImage", "Take a new screenshot first")
        if ((run.args.has("image") && original.getString("id") != run.args.getString("image")) ||
            SystemClock.elapsedRealtime() - original.getLong("at") > IMAGE_TTL_MS
        ) {
            throw Rejected("staleImage", "Image is no longer current; take a new screenshot")
        }
        val shape =
            try {
                geometry(run)
            } catch (error: Exception) {
                invalidate()
                throw error
            }
        if (shape.toString() != original.getJSONObject("shape").toString()) {
            invalidate()
            throw Rejected("staleImage", "Display, window, lock or magnification changed")
        }
        val simpleLockSwipe =
            run.command == "swipe" &&
                shape.getBoolean("keyguardShowing") &&
                !shape.getBoolean("keyguardSecure") &&
                !shape.getBoolean("deviceLocked")
        inputReady(if (simpleLockSwipe) copy(shape).put("keyguardShowing", false) else shape)
        val duration = if (run.command == "swipe") run.args.getInt("durationMs") else TAP_MS
        if (duration !in MIN_SWIPE_MS..MAX_SWIPE_MS || duration > run.budget.remaining()) {
            throw Rejected("duration", "Gesture duration must fit the remaining budget and $MIN_SWIPE_MS..$MAX_SWIPE_MS ms")
        }
        val from = run.args.getJSONArray(if (run.command == "tap") "point" else "from")
        val to = if (run.command == "swipe") run.args.getJSONArray("to") else from
        for (point in listOf(from, to)) {
            if (point.length() != 2 ||
                point.getInt(0) !in 0 until original.getInt("width") ||
                point.getInt(1) !in 0 until original.getInt("height")
            ) {
                throw Rejected("point", "Point is outside the screenshot")
            }
        }
        var result = inject(run, from, to, duration)
        run.remember(result)
        if (run.dispatchStarted) {
            val observed = backend.postAction.settle(run, before)
            result = observed.appliedTo(result)
            run.remember(result)
        }
        return result
    }

    // One gesture owns its last point and failure cleanup.
    @Suppress("LoopWithTooManyJumpStatements", "CyclomaticComplexMethod", "NestedBlockDepth")
    private fun inject(
        run: UiRun,
        from: JSONArray,
        to: JSONArray,
        duration: Int,
    ): JSONObject {
        val ui = backend.automation(run)
        // Keep synchronous receipt without UiAutomation's DOWN/UP animation wait.
        val injection = HiddenApi.injectInput
        val down = SystemClock.uptimeMillis()
        val offsets = if (run.command == "swipe") (0 until duration step FRAME_MS).toList() + duration else listOf(0, duration)
        val segments = JSONArray()
        var result = Outcome.DONE
        var x = from.getInt(0).toFloat()
        var y = from.getInt(1).toFloat()
        try {
            for ((index, offset) in offsets.withIndex()) {
                val kind =
                    when (index) {
                        0 -> MotionEvent.ACTION_DOWN
                        offsets.lastIndex -> MotionEvent.ACTION_UP
                        else -> MotionEvent.ACTION_MOVE
                    }
                val wait = down + offset - SystemClock.uptimeMillis()
                if (wait > 0) Thread.sleep(wait)
                val fraction = offset.toFloat() / duration
                x = from.getInt(0) + (to.getInt(0) - from.getInt(0)) * fraction
                y = from.getInt(1) + (to.getInt(1) - from.getInt(1)) * fraction
                val event = touchEvent(down, kind, x, y)
                if (!segment(ui, injection, run, event, segments)) {
                    result = Outcome.undelivered(run.dispatchStarted)
                    break
                }
            }
        } catch (error: Exception) {
            segments.put(json("error" to problem(error)))
            result = Outcome.undelivered(run.dispatchStarted)
        }
        val answer =
            outcome(
                run.command,
                result,
                "Platform input dispatch returned; App outcome is not verified",
                json(
                    "segments" to segments,
                ),
            )
        if (result == Outcome.UNKNOWN) {
            run.remember(answer)
            answer.getJSONObject("data").put("cancel", cancel(ui, down, x, y))
        }
        return answer
    }

    private fun cancel(
        ui: UiAutomation,
        down: Long,
        x: Float,
        y: Float,
    ): JSONObject {
        val event = touchEvent(down, MotionEvent.ACTION_CANCEL, x, y)
        return try {
            HiddenApi.displayId.invoke(event, 0)
            // CANCEL uses async injection: no input-completion wait and no DOWN/UP animation synchronization.
            json("accepted" to ui.injectInputEvent(event, false))
        } catch (error: Exception) {
            json("error" to problem(error))
        } finally {
            event.recycle()
        }
    }

    private fun touchEvent(
        down: Long,
        action: Int,
        pointX: Float,
        pointY: Float,
    ): MotionEvent {
        // Touch handlers can route by tool type independently of the input source.
        val properties =
            MotionEvent.PointerProperties().apply {
                id = 0
                toolType = MotionEvent.TOOL_TYPE_FINGER
            }
        val coordinates =
            MotionEvent.PointerCoords().apply {
                x = pointX
                y = pointY
                pressure = 1f
                size = 1f
            }
        return MotionEvent.obtain(
            down,
            SystemClock.uptimeMillis(),
            action,
            1,
            arrayOf(properties),
            arrayOf(coordinates),
            0,
            0,
            1f,
            1f,
            0,
            0,
            InputDevice.SOURCE_TOUCHSCREEN,
            0,
        )
    }

    private fun segment(
        ui: UiAutomation,
        injection: Method,
        run: UiRun,
        event: MotionEvent,
        segments: JSONArray,
    ): Boolean =
        try {
            HiddenApi.displayId.invoke(event, 0)
            if (!run.dispatchStarted) backend.dispatch(run) else backend.current(run)
            val returned = injection.invoke(ui, event, true, false) as Boolean
            segments.put(json("action" to event.action, "returned" to returned))
            returned
        } catch (error: Exception) {
            segments.put(json("action" to event.action, "error" to problem(error)))
            false
        } finally {
            event.recycle()
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
        private const val TAP_MS = 50
        private const val MIN_SWIPE_MS = 50
        private const val MAX_SWIPE_MS = 5000
        private const val FRAME_MS = 16
        private const val PNG_QUALITY = 100
    }
}
