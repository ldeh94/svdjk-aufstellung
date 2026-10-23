package de.svdjk.aufstellung

import android.app.Activity
import android.content.ContentValues
import android.content.Intent
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.print.PrintAttributes
import android.print.PrintManager
import android.provider.MediaStore
import android.util.Base64
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Toast
import androidx.core.content.FileProvider
import java.io.File

/**
 * Huelle um die Web-App auf GitHub Pages: Inhalte und Spieldaten kommen immer
 * aktuell von dort, die App ergaenzt nur Speichern/Teilen und Drucken.
 */
class MainActivity : Activity() {

    companion object {
        const val APP_URL = "https://ldeh94.github.io/svdjk-aufstellung/"
        const val APP_HOST = "ldeh94.github.io"
        const val SAVE_DIR = "SVDJK"
    }

    private lateinit var web: WebView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        web = WebView(this)
        setContentView(web)

        with(web.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true          // Spielerliste + Formular (localStorage)
            @Suppress("DEPRECATION")
            databaseEnabled = true
            allowFileAccess = false
            textZoom = 100
            cacheMode = if (online()) WebSettings.LOAD_DEFAULT else WebSettings.LOAD_CACHE_ELSE_NETWORK
        }
        web.addJavascriptInterface(Bridge(), "AndroidBridge")
        web.webChromeClient = WebChromeClient()   // alert()/confirm() anzeigen
        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, req: WebResourceRequest): Boolean {
                val u = req.url
                if (u.host == APP_HOST && u.path.orEmpty().startsWith("/svdjk-aufstellung")) return false
                // fussball.de, Karten usw. im Browser bzw. der passenden App oeffnen
                return try { startActivity(Intent(Intent.ACTION_VIEW, u)); true } catch (e: Exception) { true }
            }

            override fun onReceivedError(view: WebView, req: WebResourceRequest, err: WebResourceError) {
                if (!req.isForMainFrame) return
                view.loadDataWithBaseURL(
                    null,
                    """<html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head>
                    <body style="font-family:sans-serif;text-align:center;padding:60px 24px;color:#333">
                    <h2 style="color:#8a1c28">Keine Verbindung</h2>
                    <p>Die App braucht beim ersten Start Internet. Danach geht sie auch offline.</p>
                    <p><a href="$APP_URL" style="display:inline-block;background:#8a1c28;color:#fff;padding:10px 22px;border-radius:8px;text-decoration:none">Erneut versuchen</a></p>
                    </body></html>""",
                    "text/html", "utf-8", null
                )
            }
        }

        if (savedInstanceState == null || web.restoreState(savedInstanceState) == null) web.loadUrl(APP_URL)
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        web.saveState(outState)
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        if (web.canGoBack()) web.goBack() else @Suppress("DEPRECATION") super.onBackPressed()
    }

    private fun online(): Boolean {
        val cm = getSystemService(CONNECTIVITY_SERVICE) as ConnectivityManager
        val caps = cm.getNetworkCapabilities(cm.activeNetwork) ?: return false
        return caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
    }

    /** In "Download/SVDJK" ablegen und direkt das Teilen-Menue (WhatsApp ...) oeffnen. */
    private fun saveAndShare(name: String, mime: String, bytes: ByteArray) {
        var savedTo: String? = null
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                val cv = ContentValues().apply {
                    put(MediaStore.MediaColumns.DISPLAY_NAME, name)
                    put(MediaStore.MediaColumns.MIME_TYPE, mime)
                    put(MediaStore.MediaColumns.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/" + SAVE_DIR)
                }
                val uri = contentResolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, cv)
                if (uri != null) {
                    contentResolver.openOutputStream(uri)?.use { it.write(bytes) }
                    savedTo = "Download/$SAVE_DIR"
                }
            } else {
                val dir = File(getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS), SAVE_DIR).apply { mkdirs() }
                File(dir, name).writeBytes(bytes)
                savedTo = dir.absolutePath
            }
        } catch (e: Exception) { /* Teilen geht trotzdem */ }

        val shareDir = File(cacheDir, "share").apply { mkdirs() }
        shareDir.listFiles()?.forEach { it.delete() }
        val f = File(shareDir, name).apply { writeBytes(bytes) }
        val uri: Uri = FileProvider.getUriForFile(this, "$packageName.files", f)
        val send = Intent(Intent.ACTION_SEND).apply {
            type = mime
            putExtra(Intent.EXTRA_STREAM, uri)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
        if (savedTo != null) Toast.makeText(this, "Gespeichert in $savedTo", Toast.LENGTH_SHORT).show()
        startActivity(Intent.createChooser(send, "Aufgebot teilen"))
    }

    inner class Bridge {
        private fun decode(dataUrl: String): ByteArray =
            Base64.decode(dataUrl.substringAfter(","), Base64.DEFAULT)

        @JavascriptInterface
        fun savePng(name: String, dataUrl: String) = saveFile(name, "image/png", dataUrl)

        @JavascriptInterface
        fun saveFile(name: String, mime: String, dataUrl: String) {
            val bytes = decode(dataUrl)
            val safe = name.replace(Regex("[^A-Za-z0-9._-]"), "_")
            runOnUiThread {
                try { saveAndShare(safe, mime, bytes) } catch (e: Exception) {
                    Toast.makeText(this@MainActivity, "Speichern fehlgeschlagen", Toast.LENGTH_LONG).show()
                }
            }
        }

        @JavascriptInterface
        fun printPage() {
            runOnUiThread {
                val pm = getSystemService(PRINT_SERVICE) as PrintManager
                val attrs = PrintAttributes.Builder().setMediaSize(PrintAttributes.MediaSize.ISO_A4).build()
                pm.print("Aufgebot", web.createPrintDocumentAdapter("Aufgebot"), attrs)
            }
        }
    }
}
