package dev.wizardlauncher.core.library

import dev.wizardlauncher.core.LauncherException
import dev.wizardlauncher.core.install.SafeZip
import java.nio.file.Files
import java.nio.file.Path

data class WorldInfo(val installed: Boolean, val sizeBytes: Long, val lastPlayed: Long, val backups: List<Backup>)
data class Backup(val name: String, val createdAt: Long, val sizeBytes: Long)

class Worlds(private val worldDir: Path, private val backupsDir: Path) {
    fun info(): WorldInfo {
        val installed = Files.isRegularFile(worldDir.resolve("level.dat"))
        return WorldInfo(
            installed = installed,
            sizeBytes = if (installed) size(worldDir) else 0,
            lastPlayed = if (installed) Files.getLastModifiedTime(worldDir.resolve("level.dat")).toMillis() else 0,
            backups = backups(),
        )
    }

    fun backups(): List<Backup> {
        if (!Files.isDirectory(backupsDir)) return emptyList()
        return Files.list(backupsDir).use { s -> s.filter { Files.isDirectory(it) && it.fileName.toString().startsWith("world-") }.toList() }
            .map { Backup(it.fileName.toString(), Files.getLastModifiedTime(it).toMillis(), size(it)) }
            .sortedByDescending { it.createdAt }
    }

    fun restore(name: String, backupCurrent: () -> Path?) {
        if (!name.startsWith("world-") || name.contains('/') || name.contains('\\')) throw LauncherException("Unknown backup.")
        val source = backupsDir.resolve(name)
        if (!Files.isRegularFile(source.resolve("level.dat"))) throw LauncherException("That backup is incomplete and cannot be restored.")
        backupCurrent()
        val staging = worldDir.resolveSibling("world_restoring")
        SafeZip.deleteTree(staging)
        SafeZip.copyTree(source, staging)
        SafeZip.deleteTree(worldDir)
        Files.move(staging, worldDir)
    }

    fun deleteBackup(name: String) {
        if (!name.startsWith("world-") || name.contains('/') || name.contains('\\')) return
        SafeZip.deleteTree(backupsDir.resolve(name))
    }

    companion object {
        fun size(dir: Path): Long = runCatching {
            Files.walk(dir).use { s -> s.filter(Files::isRegularFile).mapToLong { Files.size(it) }.sum() }
        }.getOrDefault(0)
    }
}
