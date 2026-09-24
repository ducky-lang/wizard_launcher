package dev.wizardlauncher.pack

import java.io.InputStream
import java.nio.file.Files
import java.nio.file.Path
import java.util.zip.ZipEntry
import java.util.zip.ZipFile
import java.util.zip.ZipOutputStream
import kotlin.io.path.isDirectory
import kotlin.io.path.isRegularFile
import kotlin.io.path.relativeTo

/**
 * Read-only view of a resource pack, either a folder or a .zip.
 *
 * Paths are always forward-slash, relative to the pack root (the folder that
 * holds pack.mcmeta). A zip whose only content is one top-level folder - the
 * way most packs are uploaded - is unwrapped transparently.
 */
class PackSource private constructor(
    private val zip: ZipFile?,
    private val dir: Path?,
    private val prefix: String,
    val paths: List<String>,
) : AutoCloseable {

    fun exists(path: String) = path in pathSet
    private val pathSet: Set<String> by lazy { paths.toHashSet() }

    fun open(path: String): InputStream {
        require(exists(path)) { "not in pack: $path" }
        return if (zip != null) zip.getInputStream(zip.getEntry(prefix + path))
        else Files.newInputStream(dir!!.resolve(path))
    }

    fun read(path: String): ByteArray = open(path).use { it.readBytes() }

    override fun close() {
        zip?.close()
    }

    companion object {
        fun open(input: Path): PackSource {
            if (input.isDirectory()) {
                val root = findRoot(input)
                val paths = Files.walk(root).use { stream ->
                    stream.filter { it.isRegularFile() }
                        .map { it.relativeTo(root).toString().replace('\\', '/') }
                        .toList()
                }
                return PackSource(null, root, "", paths.sorted())
            }
            val zip = ZipFile(input.toFile())
            val names = zip.entries().asSequence().filter { !it.isDirectory }.map { it.name }.toList()
            val prefix = when {
                "pack.mcmeta" in names -> ""
                else -> names.filter { it.endsWith("/pack.mcmeta") }
                    .minByOrNull { it.count { c -> c == '/' } }
                    ?.removeSuffix("pack.mcmeta") ?: ""
            }
            val paths = names.filter { it.startsWith(prefix) }
                .map { it.removePrefix(prefix) }
                .filter { safePath(it) }
            return PackSource(zip, null, prefix, paths.sorted())
        }

        private fun findRoot(dir: Path): Path {
            if (dir.resolve("pack.mcmeta").isRegularFile()) return dir
            val children = Files.list(dir).use { it.toList() }
            val single = children.singleOrNull { it.isDirectory() }
            return if (single != null && single.resolve("pack.mcmeta").isRegularFile()) single else dir
        }

        /** Rejects entries that could escape the output (zip slip) or are junk. */
        internal fun safePath(path: String): Boolean {
            if (path.isEmpty() || path.startsWith("/") || path.contains('\\') || path.contains(':')) return false
            return path.split('/').none { it == ".." || it == "." || it.isEmpty() }
        }
    }
}

/**
 * Writes the converted pack. Unchanged files are streamed straight from the
 * source, so a 400 MB pack is never held in memory - only the few JSON and
 * shader files that were actually rewritten are.
 */
class PackSink(private val output: Path) : AutoCloseable {
    private val written = HashSet<String>()
    private val asZip = output.fileName.toString().endsWith(".zip", ignoreCase = true)
    private val tmp: Path = output.resolveSibling(output.fileName.toString() + ".tmp")
    private val zip: ZipOutputStream?

    init {
        output.parent?.let { Files.createDirectories(it) }
        zip = if (asZip) ZipOutputStream(Files.newOutputStream(tmp)) else null
        if (!asZip) {
            deleteRecursively(tmp)
            Files.createDirectories(tmp)
        }
    }

    fun put(path: String, data: ByteArray) = put(path) { it.write(data) }

    fun copy(path: String, from: PackSource, sourcePath: String = path) =
        put(path) { out -> from.open(sourcePath).use { it.copyTo(out) } }

    private fun put(path: String, body: (java.io.OutputStream) -> Unit) {
        require(PackSource.safePath(path)) { "unsafe output path $path" }
        if (!written.add(path)) return
        if (zip != null) {
            zip.putNextEntry(ZipEntry(path).apply { time = 0 })
            body(zip)
            zip.closeEntry()
        } else {
            val target = tmp.resolve(path)
            Files.createDirectories(target.parent)
            Files.newOutputStream(target).use(body)
        }
    }

    fun contains(path: String) = path in written

    /** Atomically swaps the finished pack into place. */
    override fun close() {
        zip?.close()
        deleteRecursively(output)
        Files.move(tmp, output, java.nio.file.StandardCopyOption.ATOMIC_MOVE)
    }

    fun abort() {
        runCatching { zip?.close() }
        deleteRecursively(tmp)
    }

    private fun deleteRecursively(path: Path) {
        if (!Files.exists(path)) return
        Files.walk(path).use { s -> s.sorted(Comparator.reverseOrder()).forEach { Files.deleteIfExists(it) } }
    }
}
