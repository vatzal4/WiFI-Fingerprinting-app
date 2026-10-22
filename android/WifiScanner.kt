package com.campusnav.survey

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.net.wifi.WifiManager
import android.net.wifi.ScanResult
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import kotlinx.coroutines.suspendCancellableCoroutine
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import kotlin.coroutines.resume

/**
 * WifiScanner performs the one thing a browser or iOS app cannot do:
 * read the raw list of nearby access points and their signal strength (RSSI).
 *
 * This requires:
 *  - ACCESS_FINE_LOCATION permission (Android ties WiFi scanning to location
 *    permission for privacy reasons)
 *  - WiFi to be turned on (scanning works even if not connected to any AP)
 */
class WifiScanner(private val context: Context) {

    private val wifiManager =
        context.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager

    fun hasPermission(): Boolean {
        return ContextCompat.checkSelfPermission(
            context, Manifest.permission.ACCESS_FINE_LOCATION
        ) == PackageManager.PERMISSION_GRANTED
    }

    /**
     * Triggers a scan and returns the visible access points as a list of
     * {bssid, ssid, rssi} maps, ready to send to the backend.
     *
     * Note: Android rate-limits how often an app can request a new scan
     * (a handful of times every 2 minutes on newer OS versions). For a
     * survey tool, scanResults (the last completed scan) is usually fresh
     * enough without needing to force a new scan every time.
     */
    fun getCurrentReadings(): List<Map<String, Any>> {
        if (!hasPermission()) {
            throw SecurityException("ACCESS_FINE_LOCATION permission not granted")
        }

        val results: List<ScanResult> = wifiManager.scanResults

        return results.map { result ->
            mapOf(
                "bssid" to result.BSSID,
                "ssid" to (result.SSID ?: ""),
                "rssi" to result.level // signal strength in dBm, e.g. -55
            )
        }
    }

    /** Ask the OS to perform a fresh scan. Call this, wait for the scan-results
     *  broadcast (or a short delay), then call getCurrentReadings(). */
    fun requestFreshScan(): Boolean {
        return wifiManager.startScan()
    }
}

/**
 * Posts one surveyed fingerprint to the backend API.
 * Runs on a background thread -- call from a coroutine (Dispatchers.IO).
 */
object FingerprintUploader {

    fun uploadFingerprint(
        baseUrl: String,       // e.g. "http://10.0.0.5:5050"
        floorId: Int,
        x: Double,
        y: Double,
        nodeId: String?,
        readings: List<Map<String, Any>>
    ): Boolean {
        val url = URL("$baseUrl/api/fingerprints")
        val conn = url.openConnection() as HttpURLConnection
        conn.requestMethod = "POST"
        conn.setRequestProperty("Content-Type", "application/json")
        conn.doOutput = true

        val readingsArray = JSONArray()
        for (r in readings) {
            val obj = JSONObject()
            obj.put("bssid", r["bssid"])
            obj.put("ssid", r["ssid"])
            obj.put("rssi", r["rssi"])
            readingsArray.put(obj)
        }

        val body = JSONObject().apply {
            put("floor_id", floorId)
            put("x", x)
            put("y", y)
            if (nodeId != null) put("node_id", nodeId)
            put("readings", readingsArray)
        }

        conn.outputStream.use { it.write(body.toString().toByteArray()) }

        val success = conn.responseCode in 200..299
        conn.disconnect()
        return success
    }
}
