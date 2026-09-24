package dev.wizardlauncher.pack

import java.nio.file.Path
import java.util.zip.ZipFile

/** `namespace:path` helpers and the file each kind of reference points at. */
object Res {
    fun normalize(ref: String): String = when {
        ref.startsWith("#") -> ref
        ':' in ref -> ref
        else -> "minecraft:$ref"
    }

    fun split(id: String): Pair<String, String> {
        val n = normalize(id)
        val i = n.indexOf(':')
        return n.substring(0, i) to n.substring(i + 1)
    }

    fun modelFile(id: String) = split(id).let { (ns, p) -> "assets/$ns/models/$p.json" }
    fun textureFile(id: String) = split(id).let { (ns, p) -> "assets/$ns/textures/$p.png" }
    fun blockstateFile(id: String) = split(id).let { (ns, p) -> "assets/$ns/blockstates/$p.json" }

    /** `assets/ns/blockstates/x.json` -> `ns:x`, or null for other paths. */
    fun blockOf(path: String): String? {
        val m = Regex("^assets/([^/]+)/blockstates/(.+)\\.json$").matchEntire(path) ?: return null
        return "${m.groupValues[1]}:${m.groupValues[2]}"
    }
}

/**
 * The file list of the target game version's own assets (its client jar).
 *
 * Optional. With it the converter can tell a reference that is merely
 * *not in the pack* (fine - vanilla provides it) from one that points at
 * nothing at all, and say so in the report instead of leaving the player to
 * find a purple-and-black checkerboard in game.
 */
class VanillaAssets(private val paths: Set<String>) {
    fun has(path: String) = path in paths

    companion object {
        fun fromClientJar(jar: Path): VanillaAssets = ZipFile(jar.toFile()).use { zip ->
            VanillaAssets(zip.entries().asSequence().map { it.name }.filter { it.startsWith("assets/") }.toHashSet())
        }
    }
}
