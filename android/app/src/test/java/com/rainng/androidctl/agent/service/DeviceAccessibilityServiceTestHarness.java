package com.rainng.androidctl.agent.service;

import android.content.Intent;

public final class DeviceAccessibilityServiceTestHarness {
    private DeviceAccessibilityServiceTestHarness() {}

    public static void onServiceConnected(DeviceAccessibilityService service) {
        service.onServiceConnected();
    }

    public static boolean onUnbind(DeviceAccessibilityService service, Intent intent) {
        return service.onUnbind(intent);
    }

    public static void onDestroy(DeviceAccessibilityService service) {
        service.onDestroy();
    }
}
