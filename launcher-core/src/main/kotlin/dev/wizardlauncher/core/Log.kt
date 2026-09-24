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

/**
 * One log for the whole launcher. Every line passes through [Redactor]
 * before it reaches the disk or a listener, so a token pasted into an
 * exception message can not end up in a bug report.
 */
object Log {
    private val listeners = CopyOnWriteArrayList<(String) -> Unit>()
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

    /** A user-facing line: shown in the UI and written to the file. */
    fun info(message: String) {
        val clean = Redactor.redact(message)
        file(clean)
        listeners.forEach { runCatching { it(clean) } }
    }

    /** File only - process output, stack traces, diagnostics. */
    @Synchronized
    fun file(message: String) {
        val line = "[${LocalDateTime.now().format(stamp)}] ${Redactor.redact(message)}"
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

/** Raised for failures the player can act on; the message is shown as-is. */
class LauncherException(message: String, cause: Throwable? = null) : Exception(message, cause)
