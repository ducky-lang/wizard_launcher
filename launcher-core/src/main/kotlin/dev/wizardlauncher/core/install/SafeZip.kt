package dev.wizardlauncher.core.install

import dev.wizardlauncher.core.LauncherException
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardCopyOption
import java.util.zip.ZipFile

object SafeZip {
    private const val MAX_TOTAL = 16L shl 30
    private const val MAX_RATIO = 200

    fun extract(zip: Path, dest: Path, progress: Progress = Progress.NONE, label: String = "files") {
        val staging = dest.resolveSibling(dest.fileName.toString() + "_staging")
        deleteTree(staging)
        Files.createDirectories(staging)
        val root = staging.toRealPath()
        val archiveSize = Files.size(zip).coerceAtLeast(1)
        ZipFile(zip.toFile()).use { file ->
            val entries = file.entries().toList()
            var written = 0L
            entries.forEachIndexed { index, entry ->
                val target = root.resolve(entry.name).normalize()
                if (!target.startsWith(root) || entry.name.contains('\u0000')) {
                    throw LauncherException("The archive contains an unsafe path (${entry.name}) and was rejected.")
                }
                if (entry.isDirectory) {
                    Files.createDirectories(target)
                    return@forEachIndexed
                }
                Files.createDirectories(target.parent)
                file.getInputStream(entry).use { input ->
                    Files.newOutputStream(target).use { out ->
                        val buf = ByteArray(1 shl 16)
                        while (true) {
                            val n = input.read(buf)
                            if (n < 0) break
                            written += n
                            if (written > MAX_TOTAL || written / archiveSize > MAX_RATIO) {
                                throw LauncherException("The archive expands to an implausible size and was rejected.")
                            }
                            out.write(buf, 0, n)
                        }
                    }
                }
                if (index % 64 == 0) progress.update(index.toDouble() / entries.size, "Unpacking $label... ${index * 100 / entries.size}%")
            }
        }
        val children = Files.list(staging).use { it.toList() }
        val content = children.singleOrNull()?.takeIf { Files.isDirectory(it) } ?: staging
        deleteTree(dest)
        Files.createDirectories(dest.parent)
        Files.move(content, dest, StandardCopyOption.ATOMIC_MOVE)
        deleteTree(staging)
    }

    fun deleteTree(path: Path) {
        if (!Files.exists(path)) return
        Files.walk(path).use { s -> s.sorted(Comparator.reverseOrder()).forEach { Files.deleteIfExists(it) } }
    }

    fun copyTree(from: Path, to: Path) {
        Files.walk(from).use { stream ->
            stream.forEach { src ->
                val dst = to.resolve(from.relativize(src).toString())
                if (Files.isDirectory(src)) Files.createDirectories(dst)
                else Files.copy(src, dst, StandardCopyOption.REPLACE_EXISTING)
            }
        }
    }
}
