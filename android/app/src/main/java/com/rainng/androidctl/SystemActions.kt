package com.rainng.androidctl

import android.accessibilityservice.AccessibilityService
import android.annotation.SuppressLint
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.SystemClock
import android.view.InputDevice
import android.view.KeyEvent
import androidx.core.net.toUri
import org.json.JSONArray
import org.json.JSONObject

@SuppressLint("QueryPermissionsNeeded") // Package queries run under Shizuku shell UID 2000, not the App UID.
internal class SystemActions(
    context: Context,
    private val ui: UiBackend,
) {
    private val packages = context.createPackageContext("com.android.shell", 0).packageManager

    @Suppress("DEPRECATION")
    fun apps(): JSONObject {
        val intent = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        val entries = packages.queryIntentActivities(intent, 0).map { it.activityInfo.applicationInfo }.distinctBy { it.packageName }
        return outcome(
            "apps",
            data =
                json(
                    "apps" to
                        JSONArray(
                            entries.sortedBy { it.packageName }.map {
                                json("package" to it.packageName, "name" to packages.getApplicationLabel(it).toString())
                            },
                        ),
                ),
        )
    }

    fun perform(run: UiRun): JSONObject {
        run.select(run.command, key = run.args.optString("key").takeIf { run.command == "key" })
        var result =
            when (run.command) {
                "wake" -> wake(run)
                "open" -> open(run)
                "notifications" -> notifications(run)
                "key" -> key(run, keyCode(run))
                "back" -> key(run, KeyEvent.KEYCODE_BACK)
                "home" -> key(run, KeyEvent.KEYCODE_HOME)
                "recents" -> key(run, KeyEvent.KEYCODE_APP_SWITCH)
                else -> error("Unexpected system action: ${run.command}")
            }
        run.remember(result)
        if (run.dispatchStarted) {
            val observed = ui.postAction.settle(run, null)
            result = observed.appliedTo(result)
            run.remember(result)
        }
        return result
    }

    fun keyCode(run: UiRun): Int {
        if (!run.args.has("ref") && (run.args.optJSONObject("scope")?.length() ?: 0) > 0) {
            throw Rejected("scope", "A scoped key requires a REF; omit scope for global input")
        }
        return when (run.args.optString("key")) {
            "enter" -> KeyEvent.KEYCODE_ENTER
            "tab" -> KeyEvent.KEYCODE_TAB
            "del" -> KeyEvent.KEYCODE_DEL
            "escape" -> KeyEvent.KEYCODE_ESCAPE
            else -> throw Rejected("key", "Use key enter|tab|del|escape [REF]")
        }
    }

    private fun notifications(run: UiRun): JSONObject {
        val automation = ui.automation(run)
        ready(run)
        ui.dispatch(run)
        val returned = automation.performGlobalAction(AccessibilityService.GLOBAL_ACTION_NOTIFICATIONS)
        return outcome(
            run.command,
            if (returned) Outcome.DONE else Outcome.UNKNOWN,
            if (returned) "Platform dispatch completed" else "Platform did not confirm dispatch",
            json("platformReturned" to returned),
        )
    }

    private fun ready(run: UiRun): JSONObject {
        val state = ui.deviceFacts()
        if (state.optInt("userId", -1) != 0) throw Rejected("userScope", "The current user must be user 0")
        if (run.command != "wake") inputReady(state)
        ui.current(run)
        return state
    }

    private fun awake(state: JSONObject): JSONObject =
        outcome(
            "wake",
            reason =
                if (state.optBoolean("keyguardShowing") || state.optBoolean("deviceLocked")) {
                    "Display is interactive; user unlock required"
                } else {
                    "Display is interactive"
                },
            data = json("display" to state),
        )

    private fun wake(run: UiRun): JSONObject {
        val state = ready(run)
        if (state.isNull("interactive")) throw Rejected("stateUnknown", "Display state is unavailable")
        if (state.getBoolean("interactive")) return awake(state)
        val input = key(run, KeyEvent.KEYCODE_WAKEUP)
        if (input.getString("result") != Outcome.DONE) return input
        while (run.budget.remaining() > 0) {
            val current = ui.deviceFacts()
            if (!current.isNull("interactive")) {
                if (current.getBoolean("interactive")) return awake(current)
            }
            run.budget.pause()
        }
        return outcome("wake", Outcome.UNKNOWN, "Wake input returned; display state was not confirmed")
    }

    fun key(
        run: UiRun,
        code: Int,
    ): JSONObject {
        val automation = ui.automation(run)
        val display = HiddenApi.displayId
        ready(run)
        val down = SystemClock.uptimeMillis()

        fun event(action: Int): KeyEvent =
            KeyEvent(
                down,
                SystemClock.uptimeMillis(),
                action,
                code,
                0,
                0,
                -1,
                0,
                KeyEvent.FLAG_FROM_SYSTEM,
                InputDevice.SOURCE_KEYBOARD,
            ).also { display.invoke(it, 0) }
        val pressed = event(KeyEvent.ACTION_DOWN)
        val released = event(KeyEvent.ACTION_UP)
        ui.dispatch(run, outcome(run.command, Outcome.UNKNOWN, "Platform key input pending"))
        var up = false
        val accepted =
            try {
                automation.injectInputEvent(pressed, true)
            } finally {
                up = automation.injectInputEvent(released, true)
            }
        if (!accepted || !up) {
            return outcome(run.command, Outcome.UNKNOWN, "Platform did not confirm key input")
        }
        return outcome(run.command, reason = "Platform key input completed; App outcome is not verified")
    }

    @Suppress("DEPRECATION")
    private fun open(run: UiRun): JSONObject {
        val intent =
            if (run.args.has("package")) {
                val name = run.args.getString("package")
                if (!name.matches(Regex("[a-zA-Z][a-zA-Z0-9_]*(\\.[a-zA-Z0-9_]+)+"))) throw Rejected("package", "Invalid package name")
                packages.getLaunchIntentForPackage(name) ?: throw Rejected("appNotLaunchable", "No launcher activity for $name")
            } else {
                val uri = run.args.getString("url").toUri()
                if (uri.scheme.isNullOrBlank()) throw Rejected("url", "URL requires an explicit scheme")
                Intent(Intent.ACTION_VIEW, uri).addCategory(Intent.CATEGORY_BROWSABLE).also {
                    if (packages.resolveActivity(it, PackageManager.MATCH_DEFAULT_ONLY) == null) {
                        return outcome("open", Outcome.FAILED, "No activity handles this URL")
                    }
                }
            }.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        val code =
            try {
                launch(run, intent)
            } catch (error: java.lang.reflect.InvocationTargetException) {
                val cause = rootCause(error)
                if (cause is SecurityException || cause is ActivityNotFoundException) return outcome("open", Outcome.FAILED, message(error))
                throw error
            }
        return outcome(
            "open",
            if (code in 0..LAST_SUCCESSFUL_LAUNCH) Outcome.DONE else Outcome.FAILED,
            "Platform launch returned $code; App outcome is not verified",
            json("launchCode" to code),
        )
    }

    private fun launch(
        run: UiRun,
        intent: Intent,
    ): Int {
        val service = HiddenApi.activityService.invoke(null)
        val method = HiddenApi.startActivity
        ready(run)
        ui.dispatch(run)
        // startActivityAsUser: shell caller, no result/profiler/options, and supported user 0.
        return method.invoke(service, null, "com.android.shell", null, intent, null, null, null, 0, 0, null, null, 0) as Int
    }

    private companion object {
        const val LAST_SUCCESSFUL_LAUNCH = 99
    }
}
