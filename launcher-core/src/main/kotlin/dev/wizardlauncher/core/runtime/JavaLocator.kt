package dev.wizardlauncher.core.runtime

import dev.wizardlauncher.core.LauncherException
import dev.wizardlauncher.core.Log
import dev.wizardlauncher.core.Platform
import dev.wizardlauncher.core.Settings
import java.nio.file.Files
import java.nio.file.Path
import java.util.concurrent.TimeUnit

object JavaLocator {
    fun find(settings: Settings, requiredMajor: Int): Path {
        settings.javaPath.takeIf { it.isNotBlank() }?.let { configured ->
            val path = Path.of(configured)
            val major = probeMajor(path)
            if (major != null && major >= requiredMajor) return path
            Log.info("The Java set in Settings ($configured) is not Java $requiredMajor+; using the bundled one.")
        }
        val own = Path.of(System.getProperty("java.home"), "bin", if (Platform.current == Platform.WINDOWS) "java.exe" else "java")
        if (Files.isExecutable(own) && Runtime.version().feature() >= requiredMajor) return own
        throw LauncherException("Java $requiredMajor or newer is required. Reinstall Wizard Launcher (it includes Java), " +
            "or set a Java $requiredMajor+ path in Settings.")
    }

    fun probeMajor(java: Path): Int? = runCatching {
        val p = ProcessBuilder(java.toString(), "-version").redirectErrorStream(true).start()
        val text = p.inputStream.readBytes().toString(Charsets.UTF_8)
        if (!p.waitFor(10, TimeUnit.SECONDS)) { p.destroyForcibly(); return null }
        val m = Regex("version \"(\\d+)(?:\\.(\\d+))?").find(text) ?: return null
        val first = m.groupValues[1].toInt()
        if (first == 1) m.groupValues[2].toInt() else first
    }.getOrNull()
}
