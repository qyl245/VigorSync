package com.vigorsync.vigorsync_mobile

import android.content.Intent
import android.net.Uri
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(
            flutterEngine.dartExecutor.binaryMessenger,
            "com.vigorsync.mobile/native",
        ).setMethodCallHandler { call, result ->
            if (call.method == "openUrl") {
                val url = call.arguments as? String
                if (url.isNullOrBlank()) {
                    result.error("invalid_url", "URL is empty", null)
                } else {
                    try {
                        startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
                        result.success(true)
                    } catch (error: Exception) {
                        result.error("open_failed", error.message, null)
                    }
                }
            } else {
                result.notImplemented()
            }
        }
    }
}
