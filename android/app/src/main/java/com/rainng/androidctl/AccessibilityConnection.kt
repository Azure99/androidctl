package com.rainng.androidctl

import android.app.UiAutomation
import android.content.Context
import android.graphics.Matrix
import android.os.Binder
import android.os.Build
import android.os.Bundle
import android.os.Parcel
import android.os.SystemClock
import android.util.SparseArray
import android.view.accessibility.AccessibilityManager
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

internal class AccessibilityConnection(
    automation: UiAutomation,
    private val context: Context,
) {
    private val connection =
        HiddenApi.clientConnection.invoke(null, HiddenApi.automationConnectionId.invoke(automation))
            ?: throw Rejected("backendConnection", "The UI service connection is unavailable")

    init {
        // App events and late prefetch callbacks must not supply a previous observation.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            val cache =
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.VANILLA_ICE_CREAM) {
                    HiddenApi.automationCache.invoke(automation)
                } else {
                    HiddenApi.clientCache.invoke(null, HiddenApi.automationConnectionId.invoke(automation))
                } ?: throw Rejected("backendCache", "The UI node cache cannot be configured")
            HiddenApi.method(cache.javaClass, "setEnabled", Boolean::class.javaPrimitiveType!!).invoke(cache, false)
        }
    }

    fun windows(): List<AccessibilityWindowInfo> {
        val all =
            HiddenApi.windows.invoke(connection) as? SparseArray<*>
                ?: throw Rejected("windowsUnavailable", "Fresh window enumeration failed")
        val values = all[0] ?: return emptyList()
        return (values as List<*>).map { it as AccessibilityWindowInfo }
    }

    fun magnification(): JSONObject {
        val config =
            HiddenApi.magnificationConfig.invoke(connection, 0)
                ?: throw Rejected("mappingUnknown", "Magnification state is unavailable")
        val configType = config.javaClass
        return json(
            "mode" to HiddenApi.method(configType, "getMode").invoke(config),
            "activated" to HiddenApi.method(configType, "isActivated").invoke(config),
            "scale" to HiddenApi.method(configType, "getScale").invoke(config),
        )
    }

    @Suppress("UnreachableCode") // Release bytecode retains each malformed-field rejection and the valid mapping path.
    fun transformation(window: Int): JSONObject {
        val manager = context.getSystemService(AccessibilityManager::class.java)
        val spec =
            HiddenApi.windowTransformation.invoke(manager, window)
                ?: throw Rejected("mappingUnknown", "Window transformation is unavailable")
        val matrix =
            HiddenApi.field(spec.javaClass, "transformationMatrix").get(spec) as? FloatArray
                ?: throw Rejected("mappingUnknown", "Window transformation matrix is unavailable")
        if (matrix.size != MATRIX_SIZE ||
            matrix.any { !it.isFinite() }
        ) {
            throw Rejected("mappingUnknown", "Invalid window transformation matrix")
        }
        if (!Matrix().apply { setValues(matrix) }.invert(Matrix())) {
            throw Rejected("mappingUnknown", "Window transformation is not invertible")
        }
        val magnification =
            HiddenApi.field(spec.javaClass, "magnificationSpec").get(spec)
                ?: throw Rejected("mappingUnknown", "Window magnification is unavailable")
        val fields = listOf("scale", "offsetX", "offsetY").map { HiddenApi.field(magnification.javaClass, it).getFloat(magnification) }
        if (fields.any { !it.isFinite() }) throw Rejected("mappingUnknown", "Invalid window magnification")
        return json("matrix" to JSONArray(matrix.toList()), "magnification" to JSONArray(fields))
    }

    @Suppress("DEPRECATION") // Android's callback API uses the Java thread ID.
    fun action(
        target: AccessibilityNodeInfo,
        action: Int,
        arguments: Bundle?,
        budget: UiBudget,
        dispatching: () -> Unit,
    ): ActionReceipt {
        budget.check()
        val callback = ActionCallback(budget.deadline)
        val source = HiddenApi.sourceNodeId.invoke(target) as Long
        val proxy = HiddenApi.callbackInterface.invoke(null, callback)
        val method = HiddenApi.performAction
        return try {
            dispatching()
            val acceptance =
                try {
                    val accepted =
                        method.invoke(
                            connection,
                            target.windowId,
                            source,
                            action,
                            arguments,
                            ActionCallback.INTERACTION,
                            proxy,
                            Thread.currentThread().id,
                        ) as Boolean
                    ActionAcceptance.Accepted(accepted)
                } catch (error: Exception) {
                    ActionAcceptance.Failed(problem(error))
                }
            // A rejected dispatch may already have focused a window, but cannot produce a later successful callback.
            val wait = acceptance !is ActionAcceptance.Accepted || acceptance.value
            ActionReceipt(acceptance, callback.awaitResult(wait))
        } finally {
            callback.finish()
        }
    }

    private companion object {
        const val MATRIX_SIZE = 9
    }
}

private class ActionCallback(
    private val deadline: Long,
) : Binder() {
    private val transaction = HiddenApi.actionTransaction.getInt(null)
    private val gate = Any()
    private val delivered = CountDownLatch(1)
    private var open = true
    private var value: Boolean? = null

    override fun onTransact(
        code: Int,
        data: Parcel,
        reply: Parcel?,
        flags: Int,
    ): Boolean {
        if (code == INTERFACE_TRANSACTION) {
            reply?.writeString(HiddenApi.CALLBACK_DESCRIPTOR)
            return true
        }
        if (code != transaction) return super.onTransact(code, data, reply, flags)
        data.enforceInterface(HiddenApi.CALLBACK_DESCRIPTOR)
        val performed = data.readInt() != 0
        val interaction = data.readInt()
        if (data.dataAvail() != 0) return false
        accept(performed, interaction)
        return true
    }

    private fun accept(
        performed: Boolean,
        interaction: Int,
    ) {
        if (interaction != INTERACTION) return
        synchronized(gate) {
            if (open && value == null && SystemClock.elapsedRealtime() <= deadline) {
                value = performed
                delivered.countDown()
            }
        }
    }

    fun awaitResult(wait: Boolean): Boolean? {
        if (wait) delivered.await(maxOf(0, deadline - SystemClock.elapsedRealtime()), TimeUnit.MILLISECONDS)
        return synchronized(gate) {
            open = false
            value
        }
    }

    fun finish() = synchronized(gate) { open = false }

    companion object {
        const val INTERACTION = 1
    }
}
