package com.rainng.androidctl

import android.annotation.SuppressLint
import android.app.UiAutomation
import android.content.Intent
import android.os.Bundle
import android.os.IBinder
import android.os.Looper
import android.view.InputEvent
import android.view.accessibility.AccessibilityManager
import android.view.accessibility.AccessibilityNodeInfo
import java.lang.reflect.Constructor
import java.lang.reflect.Field
import java.lang.reflect.Method
import java.util.concurrent.ConcurrentHashMap

// Resolve metadata on demand; callers still acquire thread-bound clients and live services.
@SuppressLint("PrivateApi", "SoonBlockedPrivateApi", "BlockedPrivateApi")
internal object HiddenApi {
    const val CALLBACK_DESCRIPTOR = "android.view.accessibility.IAccessibilityInteractionConnectionCallback"

    private val node = AccessibilityNodeInfo::class.java
    private val client: Class<*> by lazy { Class.forName("android.view.accessibility.AccessibilityInteractionClient") }
    private val service: Class<*> by lazy { Class.forName("android.accessibilityservice.IAccessibilityServiceConnection") }
    private val callback: Class<*> by lazy { Class.forName(CALLBACK_DESCRIPTOR) }
    private val callbackStub: Class<*> by lazy { Class.forName("$CALLBACK_DESCRIPTOR\$Stub") }
    private val automationConnection: Class<*> by lazy { Class.forName("android.app.UiAutomationConnection") }
    private val windowManager: Class<*> by lazy { Class.forName("android.view.IWindowManager") }
    private val trustManager: Class<*> by lazy { Class.forName("android.app.trust.ITrustManager") }

    val sourceNodeId: Method by lazy { node.getMethod("getSourceNodeId") }
    val nodeConnectionId: Method by lazy { node.getMethod("getConnectionId") }
    val childId: Method by lazy { node.getMethod("getChildId", Int::class.javaPrimitiveType) }
    val parentId: Method by lazy { node.getMethod("getParentNodeId") }
    val accessibilityViewId: Method by lazy { node.getMethod("getAccessibilityViewId", Long::class.javaPrimitiveType) }
    val undefinedItemId: Field by lazy { node.getField("UNDEFINED_ITEM_ID") }
    val leashedNodeId: Field by lazy { node.getField("LEASHED_NODE_ID") }
    val rootNodeId: Field by lazy { node.getField("ROOT_NODE_ID") }
    val undefinedNodeId: Field by lazy { node.getField("UNDEFINED_NODE_ID") }

    // NodeAccess uses these fields only on API 30-32; API 33+ uses public child/parent access.
    val leashedChild: Field by lazy { node.getDeclaredField("mLeashedChild").apply { isAccessible = true } }
    val leashedParent: Field by lazy { node.getDeclaredField("mLeashedParent").apply { isAccessible = true } }
    val leashedParentNodeId: Field by lazy { node.getDeclaredField("mLeashedParentNodeId").apply { isAccessible = true } }
    val clientInstance: Method by lazy { client.getMethod("getInstance") }
    val nodeByWindow: Method by lazy { findNode(Int::class.javaPrimitiveType!!) }
    val nodeByLeash: Method by lazy { findNode(IBinder::class.java) }
    val clientConnection: Method by lazy { client.getMethod("getConnection", Int::class.javaPrimitiveType) }
    val clientCache: Method by lazy { client.getMethod("getCache", Int::class.javaPrimitiveType) }

    val newAutomationConnection: Constructor<*> by lazy { automationConnection.getConstructor() }
    val newAutomation: Constructor<UiAutomation> by lazy {
        UiAutomation::class.java.getConstructor(Looper::class.java, Class.forName("android.app.IUiAutomationConnection"))
    }
    val automationConnectionId: Method by lazy { UiAutomation::class.java.getMethod("getConnectionId") }
    val automationCache: Method by lazy { UiAutomation::class.java.getMethod("getCache") }
    val connectWithTimeout: Method by lazy {
        UiAutomation::class.java.getMethod("connectWithTimeout", Int::class.javaPrimitiveType, Long::class.javaPrimitiveType)
    }
    val connect: Method by lazy { UiAutomation::class.java.getMethod("connect", Int::class.javaPrimitiveType) }
    val disconnect: Method by lazy { UiAutomation::class.java.getMethod("disconnect") }
    val disconnectConnection: Method by lazy { automationConnection.getMethod("disconnect") }
    val remoteCallbackThread: Field by lazy {
        UiAutomation::class.java
            .getDeclaredField(
                "mRemoteCallbackThread",
            ).apply { isAccessible = true }
    }
    val windows: Method by lazy { service.getMethod("getWindows") }
    val magnificationConfig: Method by lazy { service.getMethod("getMagnificationConfig", Int::class.javaPrimitiveType) }
    val windowTransformation: Method by lazy {
        AccessibilityManager::class.java.getMethod(
            "getWindowTransformationSpec",
            Int::class.javaPrimitiveType,
        )
    }
    val callbackInterface: Method by lazy { callbackStub.getMethod("asInterface", IBinder::class.java) }
    val actionTransaction: Field by lazy {
        callbackStub.getDeclaredField("TRANSACTION_setPerformAccessibilityActionResult").apply { isAccessible = true }
    }
    val performAction: Method by lazy {
        service.getMethod(
            "performAccessibilityAction",
            Int::class.javaPrimitiveType,
            Long::class.javaPrimitiveType,
            Int::class.javaPrimitiveType,
            Bundle::class.java,
            Int::class.javaPrimitiveType,
            callback,
            Long::class.javaPrimitiveType,
        )
    }

    val displayId: Method by lazy { InputEvent::class.java.getMethod("setDisplayId", Int::class.javaPrimitiveType) }
    val injectInput: Method by lazy {
        UiAutomation::class.java.getMethod(
            "injectInputEvent",
            InputEvent::class.java,
            Boolean::class.javaPrimitiveType,
            Boolean::class.javaPrimitiveType,
        )
    }
    val activityService: Method by lazy { Class.forName("android.app.ActivityTaskManager").getMethod("getService") }
    val startActivity: Method by lazy {
        Class.forName("android.app.IActivityTaskManager").getMethod(
            "startActivityAsUser",
            Class.forName("android.app.IApplicationThread"),
            String::class.java,
            String::class.java,
            Intent::class.java,
            String::class.java,
            IBinder::class.java,
            String::class.java,
            Int::class.javaPrimitiveType,
            Int::class.javaPrimitiveType,
            Class.forName("android.app.ProfilerInfo"),
            Bundle::class.java,
            Int::class.javaPrimitiveType,
        )
    }
    val currentUser: Method by lazy { Class.forName("android.app.ActivityManager").getMethod("getCurrentUser") }
    val serviceBinder: Method by lazy { Class.forName("android.os.ServiceManager").getMethod("getService", String::class.java) }
    val windowInterface: Method by lazy { Class.forName("android.view.IWindowManager\$Stub").getMethod("asInterface", IBinder::class.java) }
    val trustInterface: Method by lazy {
        Class
            .forName(
                "android.app.trust.ITrustManager\$Stub",
            ).getMethod("asInterface", IBinder::class.java)
    }
    val keyguardLocked: Method by lazy { windowManager.getMethod("isKeyguardLocked") }
    val keyguardSecure: Method by lazy { windowManager.getMethod("isKeyguardSecure", Int::class.javaPrimitiveType) }
    val deviceLocked: Method by lazy { trustManager.getMethod("isDeviceLocked", Int::class.javaPrimitiveType) }
    val deviceLockedOnDisplay: Method by lazy {
        trustManager.getMethod(
            "isDeviceLocked",
            Int::class.javaPrimitiveType,
            Int::class.javaPrimitiveType,
        )
    }

    private val methods = ConcurrentHashMap<MethodKey, Method>()
    private val fields = ConcurrentHashMap<Pair<Class<*>, String>, Field>()

    private data class MethodKey(
        val type: Class<*>,
        val name: String,
        val parameters: List<Class<*>>,
    )

    @Suppress("SpreadOperator") // Reflection takes the member signature as varargs.
    fun method(
        type: Class<*>,
        name: String,
        vararg parameters: Class<*>,
    ): Method = methods.computeIfAbsent(MethodKey(type, name, parameters.toList())) { type.getMethod(name, *parameters) }

    fun field(
        type: Class<*>,
        name: String,
    ): Field = fields.computeIfAbsent(type to name) { type.getField(name) }

    private fun findNode(window: Class<*>): Method =
        client.getMethod(
            "findAccessibilityNodeInfoByAccessibilityId",
            Int::class.javaPrimitiveType,
            window,
            Long::class.javaPrimitiveType,
            Boolean::class.javaPrimitiveType,
            Int::class.javaPrimitiveType,
            Bundle::class.java,
        )
}
