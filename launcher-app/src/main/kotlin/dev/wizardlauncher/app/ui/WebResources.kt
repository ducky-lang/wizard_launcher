package dev.wizardlauncher.app.ui

import org.cef.browser.CefBrowser
import org.cef.browser.CefFrame
import org.cef.callback.CefCallback
import org.cef.callback.CefSchemeHandlerFactory
import org.cef.handler.CefResourceHandler
import org.cef.handler.CefResourceHandlerAdapter
import org.cef.misc.IntRef
import org.cef.misc.StringRef
import org.cef.network.CefRequest
import org.cef.network.CefResponse
import java.net.URI
import java.net.URLDecoder

class WebResources(private val media: (String) -> Pair<ByteArray, String>?) : CefSchemeHandlerFactory {
    override fun create(browser: CefBrowser?, frame: CefFrame?, schemeName: String?, request: CefRequest?): CefResourceHandler =
        Handler(request?.url ?: "")

    private inner class Handler(private val url: String) : CefResourceHandlerAdapter() {
        private var data: ByteArray = ByteArray(0)
        private var mime = "text/plain"
        private var status = 404
        private var offset = 0

        override fun processRequest(request: CefRequest, callback: CefCallback): Boolean {
            val resolved = runCatching { resolve(url) }.getOrNull()
            if (resolved != null) {
                data = resolved.first
                mime = resolved.second
                status = 200
            }
            callback.Continue()
            return true
        }

        override fun getResponseHeaders(response: CefResponse, responseLength: IntRef, redirectUrl: StringRef) {
            response.status = status
            response.mimeType = mime
            if (mime.startsWith("text/")) response.setHeaderByName("Content-Type", "$mime; charset=utf-8", true)
            response.setHeaderByName("Content-Security-Policy", CSP, true)
            response.setHeaderByName("X-Content-Type-Options", "nosniff", true)
            response.setHeaderByName("Referrer-Policy", "no-referrer", true)
            response.setHeaderByName("Cache-Control", "no-store", true)
            responseLength.set(data.size)
        }

        override fun readResponse(dataOut: ByteArray, bytesToRead: Int, bytesRead: IntRef, callback: CefCallback): Boolean {
            if (offset >= data.size) {
                bytesRead.set(0)
                return false
            }
            val n = minOf(bytesToRead, data.size - offset)
            System.arraycopy(data, offset, dataOut, 0, n)
            offset += n
            bytesRead.set(n)
            return true
        }
    }

    private fun resolve(url: String): Pair<ByteArray, String>? {
        val uri = URI(url)
        if (uri.host != HOST) return null
        var path = URLDecoder.decode(uri.rawPath ?: "/", Charsets.UTF_8)
        if (path == "/" || path.isEmpty()) path = "/index.html"
        if (path.startsWith("/media/")) return media(path.removePrefix("/media/"))
        if (!Regex("^/[a-z0-9][a-z0-9._-]*\\.(html|css|js)$").matches(path)) return null
        val bytes = WebResources::class.java.getResourceAsStream("/web$path")?.use { it.readBytes() } ?: return null
        return bytes to mimeOf(path)
    }

    companion object {
        const val HOST = "wizard-launcher.invalid"
        const val ORIGIN = "https://$HOST/"
        const val CSP = "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; " +
            "connect-src 'none'; font-src 'self' data:; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"

        fun mimeOf(path: String) = when (path.substringAfterLast('.').lowercase()) {
            "html" -> "text/html"
            "css" -> "text/css"
            "js" -> "text/javascript"
            "png" -> "image/png"
            "jpg", "jpeg" -> "image/jpeg"
            "svg" -> "image/svg+xml"
            else -> "application/octet-stream"
        }
    }
}
