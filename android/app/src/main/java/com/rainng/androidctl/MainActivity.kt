package com.rainng.androidctl

import android.Manifest
import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import rikka.shizuku.Shizuku
import java.net.Inet6Address
import java.net.InetAddress
import java.net.NetworkInterface

class MainActivity : ComponentActivity() {
    private val statusHandler = Handler(Looper.getMainLooper())
    private var batteryExempt by mutableStateOf<Boolean?>(null)
    private var shizukuState by mutableStateOf("")
    private var pairingDisplay by mutableStateOf<PairingDisplay?>(null)
    private var localPairs: Result<List<PairingSummary>> by mutableStateOf(Result.success(emptyList()))
    private var candidateAddresses by mutableStateOf<List<String>>(emptyList())
    private val pollStatus =
        object : Runnable {
            override fun run() {
                refreshLocalManagementState()
                GatewayService.current?.pollWorkerStatus()
                statusHandler.postDelayed(this, STATUS_REFRESH_MS)
            }
        }

    override fun onResume() {
        super.onResume()
        batteryExempt = getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(packageName)
        statusHandler.post(pollStatus)
    }

    override fun onPause() {
        statusHandler.removeCallbacks(pollStatus)
        super.onPause()
    }

    // Local management remains visible when the Gateway service is absent.
    internal fun refreshLocalManagementState() {
        pairingDisplay = runCatching { Pairings.get(this).display() }.getOrNull()
        localPairs = runCatching { Pairings.get(this).list() }
        shizukuState =
            try {
                requireShellShizuku()
                getString(
                    if (Shizuku.checkSelfPermission() == PackageManager.PERMISSION_GRANTED) {
                        R.string.shizuku_authorized
                    } else {
                        R.string.shizuku_unauthorized
                    },
                )
            } catch (failure: Exception) {
                "Shizuku: ${message(failure)}"
            }
        candidateAddresses = readCandidateEndpoints()
    }

    private fun readCandidateEndpoints(): List<String> =
        candidateEndpoints(NetworkInterface.getNetworkInterfaces().toList().flatMap { it.inetAddresses.toList() })

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val bars = SystemBarStyle.light(android.graphics.Color.TRANSPARENT, android.graphics.Color.TRANSPARENT)
        enableEdgeToEdge(statusBarStyle = bars, navigationBarStyle = bars)
        candidateAddresses = readCandidateEndpoints()
        setContent { Management() }
    }

    @Composable
    private fun Management() {
        val facts by GatewayService.status.collectAsState()
        val notification = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { }
        ManagementScreen(
            facts = facts,
            readings =
                LocalManagementReadings(
                    batteryExempt = batteryExempt,
                    shizukuState = shizukuState,
                    pairingDisplay = pairingDisplay,
                    pairs = localPairs,
                    candidateAddresses = candidateAddresses,
                ),
            onGatewayEnabled = { enabled ->
                setGatewayEnabled(enabled) { notification.launch(Manifest.permission.POST_NOTIFICATIONS) }
            },
            onAuthorize = ::requestAuthorization,
            onRefresh = ::refreshManagement,
            onBatterySettings = ::openBatterySettings,
            onStopExecutor = { GatewayService.current?.stopExecutor() },
            onOpenPairingWindow = ::openPairingWindow,
            onClosePairingWindow = ::closePairingWindow,
            onRevokePairing = ::revokePairing,
        )
    }

    private fun setGatewayEnabled(
        enabled: Boolean,
        requestNotifications: () -> Unit,
    ) {
        if (enabled) {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) requestNotifications()
            startForegroundService(Intent(this, GatewayService::class.java))
        } else {
            stopService(Intent(this, GatewayService::class.java))
        }
    }

    private fun requestAuthorization(): String? =
        try {
            requireShellShizuku()
            Shizuku.requestPermission(0)
            null
        } catch (error: Exception) {
            message(error)
        }

    private fun refreshManagement() {
        refreshLocalManagementState()
        GatewayService.current?.refreshAndRecover()
    }

    private fun openBatterySettings(): String? =
        try {
            startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
            null
        } catch (_: ActivityNotFoundException) {
            getString(R.string.battery_settings_unavailable)
        }

    private fun openPairingWindow() {
        GatewayService.current?.openPairingWindow { runOnUiThread(::refreshLocalManagementState) }
    }

    // A failed local credential write must not claim revocation.
    private fun revokePairing(id: String): String =
        try {
            val service = GatewayService.current
            if (service != null) {
                service.revoke(id)
            } else {
                Pairings.get(this).revoke(id)
            }
            refreshLocalManagementState()
            getString(R.string.pairing_revoked)
        } catch (error: Exception) {
            rootCause(error).toString()
        }

    private fun closePairingWindow() {
        Pairings.get(this).closeWindow()
        refreshLocalManagementState()
    }

    private companion object {
        const val STATUS_REFRESH_MS = 2000L
    }
}

internal data class LocalManagementReadings(
    val batteryExempt: Boolean?,
    val shizukuState: String,
    val pairingDisplay: PairingDisplay?,
    val pairs: Result<List<PairingSummary>>,
    val candidateAddresses: List<String>,
)

internal fun candidateEndpoints(addresses: List<InetAddress>): List<String> =
    addresses
        .filter { !it.isLoopbackAddress }
        .map { if (it is Inet6Address) "[${it.hostAddress}]:${Limits.PORT}" else "${it.hostAddress}:${Limits.PORT}" }
        .distinct()
