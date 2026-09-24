package dev.wizardlauncher.core

import dev.wizardlauncher.core.security.Redactor
import java.io.BufferedWriter
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardOpenOption
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.util.concurrent.CopyOnWriteArrayList

object Log {
    private val listeners = CopyOnWriteArrayList<(String) -> Unit>()
    private val lineListeners = CopyOnWriteArrayList<(String) -> Unit>()
    private val recent = ArrayDeque<String>()
    private var writer: BufferedWriter? = null
    private val stamp = DateTimeFormatter.ofPattern("HH:mm:ss")

    @Synchronized
    fun init(dir: Path, keepDays: Int = 7) {
        Files.createDirectories(dir)
        val file = dir.resolve("launcher-${LocalDate.now()}.log")
        writer = Files.newBufferedWriter(file, StandardOpenOption.CREATE, StandardOpenOption.APPEND)
        prune(dir, keepDays)
        file("---- Wizard Launcher ${BuildInfo.version} on ${Platform.current}/${Platform.arch}, Java ${Runtime.version()} ----")
    }

    fun listen(listener: (String) -> Unit) { listeners += listener }

    fun listenLines(listener: (String) -> Unit) { lineListeners += listener }

    @Synchronized
    fun recentLines(): List<String> = recent.toList()

    fun info(message: String) {
        val clean = Redactor.redact(message)
        file(clean)
        listeners.forEach { runCatching { it(clean) } }
    }

    @Synchronized
    fun file(message: String) {
        val line = "[${LocalDateTime.now().format(stamp)}] ${Redactor.redact(message)}"
        recent.addLast(line)
        while (recent.size > 2000) recent.removeFirst()
        lineListeners.forEach { runCatching { it(line) } }
        val w = writer
        if (w == null) {
            System.err.println(line)
            return
        }
        runCatching { w.write(line); w.newLine(); w.flush() }
    }

    fun error(message: String, t: Throwable? = null) {
        info(message)
        if (t != null) file(t.stackTraceToString())
    }

    private fun prune(dir: Path, keepDays: Int) {
        val cutoff = System.currentTimeMillis() - keepDays * 86_400_000L
        runCatching {
            Files.list(dir).use { files ->
                files.filter { it.fileName.toString().endsWith(".log") && Files.getLastModifiedTime(it).toMillis() < cutoff }
                    .forEach { Files.deleteIfExists(it) }
            }
        }
    }
}

class LauncherException(message: String, cause: Throwable? = null) : Exception(message, cause)
