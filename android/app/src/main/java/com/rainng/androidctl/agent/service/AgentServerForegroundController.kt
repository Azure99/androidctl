package com.rainng.androidctl.agent.service

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import com.rainng.androidctl.MainActivity
import com.rainng.androidctl.R

internal class AgentServerForegroundController(
    private val context: Context,
) {
    private val notificationManager = context.getSystemService(NotificationManager::class.java)

    fun start(service: Service) {
        ensureNotificationChannel()
        ServiceCompat.startForeground(
            service,
            NOTIFICATION_ID,
            buildNotification(service),
            ServiceInfo.FOREGROUND_SERVICE_TYPE_MANIFEST,
        )
    }

    fun stop(service: Service) {
        ServiceCompat.stopForeground(service, ServiceCompat.STOP_FOREGROUND_REMOVE)
    }

    private fun buildNotification(context: Context) =
        NotificationCompat
            .Builder(context, NOTIFICATION_CHANNEL_ID)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle(context.getString(R.string.server_notification_title))
            .setContentText(context.getString(R.string.server_notification_text))
            .setContentIntent(buildContentIntent(context))
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .build()

    private fun buildContentIntent(context: Context): PendingIntent {
        val intent =
            Intent(context, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
            }
        return PendingIntent.getActivity(
            context,
            REQUEST_CODE_OPEN_APP,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    }

    private fun ensureNotificationChannel() {
        val channel =
            NotificationChannel(
                NOTIFICATION_CHANNEL_ID,
                context.getString(R.string.server_notification_channel_name),
                NotificationManager.IMPORTANCE_LOW,
            ).apply {
                description = context.getString(R.string.server_notification_channel_description)
            }
        notificationManager?.createNotificationChannel(channel)
    }

    private companion object {
        const val NOTIFICATION_CHANNEL_ID = "agent_server"
        const val NOTIFICATION_ID = 1001
        const val REQUEST_CODE_OPEN_APP = 1002
    }
}
