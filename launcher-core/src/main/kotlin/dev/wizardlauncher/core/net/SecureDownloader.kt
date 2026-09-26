package dev.wizardlauncher.core.net

import dev.wizardlauncher.core.BuildInfo
import dev.wizardlauncher.core.LauncherException
import dev.wizardlauncher.core.Log
import dev.wizardlauncher.core.security.Checksum
import dev.wizardlauncher.core.security.Hashes
import dev.wizardlauncher.core.security.Hashes.toHex
import java.io.IOException
import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardCopyOption
import java.nio.file.StandardOpenOption
import java.time.Duration
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLParameters

typealias ByteProgress = (Long, Long) -> Unit

class SecureDownloader(
    private val allowedDomains: Set<String>,
    private val maxRetries: Int = 4,
    private val backoffMs: Long = 2000,
) {
    private val client: HttpClient by lazy {
        HttpClient.newBuilder()
            .followRedirects(HttpClient.Redirect.NEVER)
            .connectTimeout(Duration.ofSeconds(15))
            .sslContext(SSLContext.getDefault())
            .sslParameters(SSLParameters().apply { protocols = arrayOf("TLSv1.3", "TLSv1.2") })
            .build()
    }

    fun isAllowed(uri: URI): Boolean {
        if (uri.scheme != "https") return false
        val host = uri.host?.lowercase()?.trimEnd('.') ?: return false
        return allowedDomains.any { host == it || host.endsWith(".$it") }
    }

    private fun check(uri: URI) {
        if (uri.scheme != "https") throw LauncherException("Refusing an insecure (non-https) download:\n$uri")
        if (!isAllowed(uri)) throw LauncherException(
            "Refusing a download from an unexpected server: ${uri.host}\n\n" +
                "If you did not change any settings, your connection may be intercepted. " +
                "Try another network, or turn off 'HTTPS scanning' in your antivirus.")
    }

    fun fetchBytes(url: String, expected: Checksum? = null, maxBytes: Long = 16L shl 20): ByteArray {
        val tmp = Files.createTempFile("wizard", ".dl")
        try {
            download(url, tmp, expected, maxBytes, resume = false)
            return Files.readAllBytes(tmp)
        } finally {
            Files.deleteIfExists(tmp)
        }
    }

    fun fetchText(url: String, expected: Checksum? = null) = fetchBytes(url, expected).toString(Charsets.UTF_8)

    fun download(
        url: String,
        target: Path,
        expected: Checksum? = null,
        maxBytes: Long = 8L shl 30,
        resume: Boolean = true,
        progress: ByteProgress = { _, _ -> },
    ): String {
        if (expected != null && Files.isRegularFile(target) && expected.verify(target)) {
            return Hashes.of(target, "SHA-256")
        }
        target.parent?.let { Files.createDirectories(it) }
        val part = target.resolveSibling(target.fileName.toString() + ".part")
        var lastError: Exception? = null
        for (attempt in 0..maxRetries) {
            try {
                val sha256 = attemptDownload(URI.create(url), part, expected, maxBytes, resume, progress)
                Files.move(part, target, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE)
                return sha256
            } catch (e: Transient) {
                lastError = e
                if (attempt == maxRetries) break
                val delay = minOf(backoffMs shl attempt, 30_000L)
                Log.info("${target.fileName}: ${e.message}. Retrying in ${delay / 1000}s...")
                Thread.sleep(delay)
            }
        }
        throw LauncherException(
            "Could not download ${target.fileName}.\n\nLast problem: ${lastError?.message}\n\n" +
                "Check your internet connection and press Play again. Everything already downloaded is kept.",
            lastError)
    }

    private class Transient(message: String) : IOException(message)

    private fun attemptDownload(
        start: URI, part: Path, expected: Checksum?, maxBytes: Long, resume: Boolean, progress: ByteProgress,
    ): String {
        var uri = start
        var already = if (resume && Files.isRegularFile(part)) Files.size(part) else 0L
        if (!resume) Files.deleteIfExists(part)
        repeat(6) {
            check(uri)
            val request = HttpRequest.newBuilder(uri)
                .timeout(Duration.ofSeconds(60))
                .header("User-Agent", BuildInfo.userAgent)
                .apply { if (already > 0) header("Range", "bytes=$already-") }
                .GET().build()
            val response = try {
                client.send(request, HttpResponse.BodyHandlers.ofInputStream())
            } catch (e: IOException) {
                throw Transient(e.message ?: e.javaClass.simpleName)
            }
            val status = response.statusCode()
            if (status in 300..399) {
                response.body().close()
                val location = response.headers().firstValue("Location").orElseThrow { Transient("redirect without Location") }
                uri = uri.resolve(location)
                return@repeat
            }
            if (status == 416 && already > 0) {
                response.body().close()
                Files.deleteIfExists(part)
                already = 0
                throw Transient("restarting download")
            }
            if (status in RETRYABLE) { response.body().close(); throw Transient("server returned $status") }
            if (status !in 200..299) {
                response.body().close()
                throw LauncherException("The server refused the download (HTTP $status):\n$uri")
            }
            val append = status == 206 && already > 0
            if (!append) already = 0
            val total = response.headers().firstValueAsLong("Content-Length").orElse(-1).let { if (it >= 0) it + already else -1 }
            if (total > maxBytes) throw LauncherException("${part.fileName} is larger than expected; download refused.")

            val digest = expected?.newDigest()
            val sha256 = java.security.MessageDigest.getInstance("SHA-256")
            if (append) {
                Files.newInputStream(part).use { input ->
                    val buf = ByteArray(1 shl 16)
                    while (true) { val n = input.read(buf); if (n < 0) break; digest?.update(buf, 0, n); sha256.update(buf, 0, n) }
                }
            }
            var done = already
            try {
                response.body().use { body ->
                    Files.newOutputStream(part, StandardOpenOption.CREATE,
                        if (append) StandardOpenOption.APPEND else StandardOpenOption.TRUNCATE_EXISTING).use { out ->
                        val buf = ByteArray(1 shl 16)
                        while (true) {
                            val n = body.read(buf)
                            if (n < 0) break
                            done += n
                            if (done > maxBytes) throw LauncherException("${part.fileName} is larger than expected; download aborted.")
                            out.write(buf, 0, n)
                            digest?.update(buf, 0, n)
                            sha256.update(buf, 0, n)
                            progress(done, total)
                        }
                    }
                }
            } catch (e: IOException) {
                throw Transient(e.message ?: "connection dropped")
            }
            if (expected != null) {
                val actual = digest!!.digest().toHex()
                if (!Hashes.matches(actual, expected.hex)) {
                    Files.deleteIfExists(part)
                    throw Transient("the file did not match its ${expected.algorithm} fingerprint")
                }
            }
            return sha256.digest().toHex()
        }
        throw LauncherException("Too many redirects while downloading $start")
    }

    companion object {
        private val RETRYABLE = setOf(408, 425, 429, 500, 502, 503, 504)
    }
}
