package com.rainng.androidctl

import android.content.Context
import android.graphics.Point
import android.hardware.display.DisplayManager
import android.os.Build
import android.os.PowerManager
import android.os.SystemClock
import android.view.Display
import org.json.JSONObject
import java.lang.reflect.Method

@Suppress("DEPRECATION")
internal fun deviceFacts(context: Context): JSONObject {
    val state = json("sampleMs" to SystemClock.elapsedRealtime())
    val power = context.getSystemService(PowerManager::class.java)
    field(state, "interactive") { power.isInteractive }
    field(state, "deepIdle") { power.isDeviceIdleMode }
    field(state, "userId") { HiddenApi.currentUser.invoke(null) }
    val display = context.getSystemService(DisplayManager::class.java).getDisplay(Display.DEFAULT_DISPLAY)
    if (display != null) {
        val size = Point()
        display.getRealSize(size)
        state
            .put("displayId", display.displayId)
            .put("displayState", display.state)
            .put("width", size.x)
            .put("height", size.y)
            .put("rotation", display.rotation)
    } else {
        state.put("displayError", "defaultDisplayUnavailable")
    }
    field(state, "keyguardShowing") { platform("window", HiddenApi.windowInterface, HiddenApi.keyguardLocked) }
    field(state, "keyguardSecure") { platform("window", HiddenApi.windowInterface, HiddenApi.keyguardSecure, 0) }
    field(state, "deviceLocked") {
        val user = state.getInt("userId")
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            platform("trust", HiddenApi.trustInterface, HiddenApi.deviceLockedOnDisplay, user, 0)
        } else {
            platform("trust", HiddenApi.trustInterface, HiddenApi.deviceLocked, user)
        }
    }
    return state
}

// Unavailable platform facts are explicit nulls with a cause.
private fun field(
    state: JSONObject,
    name: String,
    read: () -> Any?,
) {
    try {
        state.put(name, read() ?: JSONObject.NULL)
    } catch (error: Exception) {
        state.put(name, JSONObject.NULL).put("${name}Error", rootCause(error).toString())
    }
}

@Suppress("SpreadOperator") // Reflection requires varargs; each call has at most two integer arguments.
private fun platform(
    service: String,
    asInterface: Method,
    method: Method,
    vararg args: Int,
): Any? {
    val binder = HiddenApi.serviceBinder.invoke(null, service)
    val remote = asInterface.invoke(null, binder)
    return method.invoke(remote, *args.toTypedArray())
}

internal fun inputReady(state: JSONObject) {
    val unlocked = listOf("keyguardShowing", "deviceLocked").all { !state.isNull(it) && !state.getBoolean(it) }
    if (state.optInt("userId", -1) != 0 || !state.optBoolean("interactive") || !unlocked) {
        throw Rejected("inputUnavailable", "Display must be interactive and unlocked for the current user")
    }
}
