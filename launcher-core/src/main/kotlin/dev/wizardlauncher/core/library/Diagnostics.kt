package dev.wizardlauncher.core.library

import com.google.gson.JsonParser
import dev.wizardlauncher.core.BuildInfo
import dev.wizardlauncher.core.Log
import dev.wizardlauncher.core.net.SecureDownloader
import java.nio.file.Files
import java.nio.file.Path

data class CrashReport(val file: String, val summary: String, val text: String)

object CrashReports {
    fun since(gameDir: Path, startedAt: Long): CrashReport? {
        val candidates = listOf(gameDir.resolve("crash-reports"), gameDir)
            .filter(Files::isDirectory)
            .flatMap { dir ->
                Files.list(dir).use { s ->
                    s.filter {
                        val n = it.fileName.toString()
                        (n.startsWith("crash-") && n.endsWith(".txt")) || (n.startsWith("hs_err_pid") && n.endsWith(".log"))
                    }.toList()
                }
            }
            .filter { Files.getLastModifiedTime(it).toMillis() >= startedAt - 5000 }
        val newest = candidates.maxByOrNull { Files.getLastModifiedTime(it).toMillis() } ?: return null
        val text = runCatching { Files.readString(newest) }.getOrDefault("")
        val summary = text.lines().firstOrNull { it.startsWith("Description:") }?.removePrefix("Description:")?.trim()
            ?: text.lines().firstOrNull { it.contains("Exception") || it.contains("Error") }?.trim()?.take(200)
            ?: "The game stopped unexpectedly."
        return CrashReport(newest.fileName.toString(), summary, text.take(200_000))
    }

    fun clientLogTail(logFile: Path, lines: Int = 80): String =
        runCatching { Files.readAllLines(logFile).takeLast(lines).joinToString("\n") }.getOrDefault("")
}

data class UpdateInfo(val current: String, val latest: String, val url: String, val newer: Boolean)

object UpdateChecker {
    private const val API = "https://api.github.com/repos/ducky-lang/wizard_launcher/releases/latest"

    fun check(): UpdateInfo? = runCatching {
        val text = SecureDownloader(setOf("api.github.com"), maxRetries = 0).fetchText(API)
        val json = JsonParser.parseString(text).asJsonObject
        val tag = json.get("tag_name").asString.removePrefix("v")
        val url = json.get("html_url").asString
        UpdateInfo(BuildInfo.version, tag, url, compare(tag, BuildInfo.version) > 0)
    }.onFailure { Log.file("Update check skipped: ${it.message}") }.getOrNull()

    fun compare(a: String, b: String): Int {
        val pa = a.split('.', '-', '+').map { it.toIntOrNull() ?: 0 }
        val pb = b.split('.', '-', '+').map { it.toIntOrNull() ?: 0 }
        for (i in 0 until maxOf(pa.size, pb.size)) {
            val c = (pa.getOrElse(i) { 0 }).compareTo(pb.getOrElse(i) { 0 })
            if (c != 0) return c
        }
        return 0
    }
}
