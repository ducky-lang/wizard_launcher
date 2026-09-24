package dev.wizardlauncher.core

import java.nio.file.Files
import java.nio.file.Path

enum class Platform { WINDOWS, MACOS, LINUX;
    val mojangName get() = when (this) { WINDOWS -> "windows"; MACOS -> "osx"; LINUX -> "linux" }

    companion object {
        val current: Platform by lazy {
            val os = System.getProperty("os.name").lowercase()
            when {
                os.contains("win") -> WINDOWS
                os.contains("mac") || os.contains("darwin") -> MACOS
                else -> LINUX
            }
        }
        /** Normalised CPU architecture: x86_64, aarch64 or x86. */
        val arch: String by lazy {
            when (val a = System.getProperty("os.arch").lowercase()) {
                "amd64", "x86_64" -> "x86_64"
                "aarch64", "arm64" -> "aarch64"
                "x86", "i386", "i686" -> "x86"
                else -> a
            }
        }
    }
}

/**
 * Where everything lives. Same locations the 1.x (Python) launcher used, so
 * an existing install - the 1 GB world with the player's progress in it -
 * is picked up in place instead of being downloaded again.
 *
 * `WIZARD_LAUNCHER_DATA` overrides the root (tests, portable installs).
 */
class AppPaths(val root: Path) {
    val resources: Path = root.resolve("resources")
    val serverDir: Path get() = resources.resolve("servers").resolve(Catalog.current.minecraft.serverVersion)
    val worldDir: Path get() = serverDir.resolve("world")
    val proxyDir: Path = resources.resolve("proxy")
    val contentCache: Path = resources.resolve("copy")
    val clientRoot: Path get() = resources.resolve("client").resolve(Catalog.current.minecraft.clientVersion)
    val gameDir: Path get() = clientRoot.resolve("minecraft")
    val modpackCache: Path get() = clientRoot.resolve("modpack")
    val libraries: Path get() = gameDir.resolve("libraries")
    val assets: Path get() = gameDir.resolve("assets")
    val versions: Path get() = gameDir.resolve("versions")
    val backups: Path = root.resolve("backups")
    val logs: Path = root.resolve("logs")
    val secrets: Path = root.resolve("secure")
    val tools: Path = root.resolve("tools")

    fun ensure(): AppPaths {
        listOf(root, resources, logs, backups).forEach { Files.createDirectories(it) }
        return this
    }

    companion object {
        const val APP_DIR = "WizardLauncher"

        fun default(): AppPaths {
            System.getenv("WIZARD_LAUNCHER_DATA")?.takeIf { it.isNotBlank() }?.let {
                return AppPaths(Path.of(it).toAbsolutePath())
            }
            val home = Path.of(System.getProperty("user.home"))
            val root = when (Platform.current) {
                Platform.WINDOWS -> Path.of(System.getenv("LOCALAPPDATA") ?: home.resolve("AppData/Local").toString())
                Platform.MACOS -> home.resolve("Library/Application Support")
                Platform.LINUX -> System.getenv("XDG_DATA_HOME")?.takeIf { it.isNotBlank() }?.let(Path::of)
                    ?: home.resolve(".local/share")
            }
            return AppPaths(root.resolve(APP_DIR))
        }

        /** The read-only folder shipped next to the program (bundled jars). */
        fun bundledResources(): Path? {
            System.getProperty("wizard.resources")?.let { p -> Path.of(p).takeIf(Files::isDirectory)?.let { return it } }
            val code = runCatching { Path.of(AppPaths::class.java.protectionDomain.codeSource.location.toURI()) }.getOrNull()
            var dir = code?.parent
            repeat(4) {
                val candidate = dir?.resolve("resources")
                if (candidate != null && Files.isDirectory(candidate.resolve("servers"))) return candidate
                dir = dir?.parent
            }
            return null
        }
    }
}
