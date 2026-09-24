package dev.wizardlauncher.pack

import java.nio.file.Path
import java.util.zip.ZipFile

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

    fun blockOf(path: String): String? {
        val m = Regex("^assets/([^/]+)/blockstates/(.+)\\.json$").matchEntire(path) ?: return null
        return "${m.groupValues[1]}:${m.groupValues[2]}"
    }
}

class VanillaAssets(private val paths: Set<String>) {
    fun has(path: String) = path in paths

    companion object {
        fun fromClientJar(jar: Path): VanillaAssets = ZipFile(jar.toFile()).use { zip ->
            VanillaAssets(zip.entries().asSequence().map { it.name }.filter { it.startsWith("assets/") }.toHashSet())
        }
    }
}
