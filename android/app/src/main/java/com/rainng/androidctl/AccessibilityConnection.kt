package com.rainng.androidctl

import android.app.UiAutomation
import android.content.Context
import android.graphics.Matrix
import android.os.Build
import android.util.SparseArray
import android.view.accessibility.AccessibilityManager
import android.view.accessibility.AccessibilityWindowInfo
import org.json.JSONArray
import org.json.JSONObject

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

    private companion object {
        const val MATRIX_SIZE = 9
    }
}
