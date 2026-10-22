package com.campusnav.survey

import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * Minimal survey screen flow (wire this up to your actual floor-plan tap UI):
 *
 *   1. Surveyor taps a point on the SVG floor plan -> you already have x, y
 *      (and optionally the nearest node_id from your navigation graph).
 *   2. Surveyor taps "Capture" -> runs a WiFi scan right where they're standing.
 *   3. The reading is immediately POSTed to the backend and saved.
 *
 * This class shows the scan + upload call; wire its two public methods
 * (requestLocationPermission, captureAndUpload) to your floor-plan UI's
 * tap handler and capture button.
 */
class SurveyActivity : ComponentActivity() {

    // TODO: point this at your deployed backend (see ../backend/app.py)
    private val backendBaseUrl = "http://10.205.26.28:5050"

    private val currentFloorId = 1 // TODO: set from the floor currently being surveyed

    private lateinit var wifiScanner: WifiScanner

    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (!granted) {
            Toast.makeText(
                this,
                "Location permission is required to scan WiFi access points",
                Toast.LENGTH_LONG
            ).show()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        wifiScanner = WifiScanner(this)

        if (!wifiScanner.hasPermission()) {
            permissionLauncher.launch(android.Manifest.permission.ACCESS_FINE_LOCATION)
        }

        // TODO: set your floor-plan tap listener to call captureAndUpload(x, y, nodeId)
        // with the coordinates of wherever the surveyor tapped.
    }

    /**
     * Call this when the surveyor taps "Capture" while standing at (x, y)
     * on the currently displayed floor's SVG.
     */
    fun captureAndUpload(x: Double, y: Double, nodeId: String?) {
        if (!wifiScanner.hasPermission()) {
            Toast.makeText(this, "Grant location permission first", Toast.LENGTH_SHORT).show()
            return
        }

        lifecycleScope.launch {
            try {
                val readings = withContext(Dispatchers.IO) {
                    wifiScanner.getCurrentReadings()
                }

                if (readings.isEmpty()) {
                    Toast.makeText(this@SurveyActivity, "No APs visible here", Toast.LENGTH_SHORT).show()
                    return@launch
                }

                val success = withContext(Dispatchers.IO) {
                    FingerprintUploader.uploadFingerprint(
                        baseUrl = backendBaseUrl,
                        floorId = currentFloorId,
                        x = x,
                        y = y,
                        nodeId = nodeId,
                        readings = readings
                    )
                }

                val message = if (success) {
                    "Saved fingerprint at ($x, $y) -- ${readings.size} APs"
                } else {
                    "Upload failed, check backend connection"
                }
                Toast.makeText(this@SurveyActivity, message, Toast.LENGTH_SHORT).show()

            } catch (e: SecurityException) {
                Toast.makeText(this@SurveyActivity, "Permission denied", Toast.LENGTH_SHORT).show()
            } catch (e: Exception) {
                Toast.makeText(this@SurveyActivity, "Error: ${e.message}", Toast.LENGTH_SHORT).show()
            }
        }
    }
}
