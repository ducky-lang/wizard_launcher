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

class AppPaths(val root: Path) {
    val resources: Path = root.resolve("resources")
    val serverDir: Path get() = resources.resolve("servers").resolve(Catalog.current.minecraft.serverVersion)
    val worldDir: Path get() = serverDir.resolve("world")
    val proxyDir: Path = resources.resolve("proxy")
    val contentCache: Path = resources.resolve("copy")
    val clientBase: Path = resources.resolve("client")
    val shared: Path = clientBase.resolve("shared")
    val libraries: Path = shared.resolve("libraries")
    val assets: Path = shared.resolve("assets")
    val versions: Path = shared.resolve("versions")
    val runtimes: Path = resources.resolve("runtimes")
    @Volatile var instanceId: String = Catalog.current.instances.first().id
    val clientRoot: Path get() = instanceRoot(instanceId)
    val gameDir: Path get() = gameDir(instanceId)
    val modpackCache: Path get() = clientRoot.resolve("modpack")

    fun instanceRoot(id: String): Path = clientBase.resolve(id)
    fun gameDir(id: String): Path = instanceRoot(id).resolve("minecraft")
    val backups: Path = root.resolve("backups")
    val logs: Path = root.resolve("logs")
    val secrets: Path = root.resolve("secure")
    val tools: Path = root.resolve("tools")

    fun ensure(): AppPaths {
        listOf(root, resources, logs, backups).forEach { Files.createDirectories(it) }
        migrateSharedStore()
        return this
    }

    private fun migrateSharedStore() {
        val old = gameDir(Catalog.current.instances.first().id)
        for (name in listOf("libraries", "assets", "versions")) {
            val from = old.resolve(name)
            val to = shared.resolve(name)
            if (!Files.isDirectory(from)) continue
            runCatching {
                Files.createDirectories(shared)
                if (!Files.exists(to)) {
                    Files.move(from, to, java.nio.file.StandardCopyOption.ATOMIC_MOVE)
                } else {
                    Files.walk(from).use { s -> s.filter(Files::isRegularFile).toList() }.forEach { file ->
                        val dest = to.resolve(from.relativize(file).toString())
                        if (!Files.exists(dest)) {
                            Files.createDirectories(dest.parent)
                            Files.move(file, dest)
                        }
                    }
                    Files.walk(from).use { s -> s.sorted(Comparator.reverseOrder()).toList() }.forEach { Files.deleteIfExists(it) }
                }
            }.onFailure { Log.file("Could not move $from to the shared game store: ${it.message}") }
        }
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
