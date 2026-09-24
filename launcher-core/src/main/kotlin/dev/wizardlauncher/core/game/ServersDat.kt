package dev.wizardlauncher.core.game

import java.io.DataInputStream
import java.io.DataOutputStream
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardCopyOption

object ServersDat {
    data class Entry(val name: String, val ip: String, val acceptTextures: Byte? = null)

    fun read(file: Path): List<Entry> {
        if (!Files.isRegularFile(file)) return emptyList()
        return runCatching {
            DataInputStream(Files.newInputStream(file).buffered()).use { input ->
                require(input.readByte().toInt() == 10) { "root is not a compound" }
                input.readUTF()
                val root = readCompound(input)
                @Suppress("UNCHECKED_CAST")
                (root["servers"] as? List<Map<String, Any?>>).orEmpty().mapNotNull { e ->
                    val ip = e["ip"] as? String ?: return@mapNotNull null
                    Entry(e["name"] as? String ?: ip, ip, e["acceptTextures"] as? Byte)
                }
            }
        }.getOrDefault(emptyList())
    }

    fun upsert(file: Path, name: String, ip: String) {
        val entries = listOf(Entry(name, ip, 1)) + read(file).filter { it.name != name && it.ip != ip }
        Files.createDirectories(file.parent)
        val tmp = file.resolveSibling("servers.dat.tmp")
        DataOutputStream(Files.newOutputStream(tmp).buffered()).use { out ->
            out.writeByte(10); out.writeUTF("")
            out.writeByte(9); out.writeUTF("servers"); out.writeByte(10); out.writeInt(entries.size)
            for (e in entries) {
                out.writeByte(8); out.writeUTF("name"); out.writeUTF(e.name)
                out.writeByte(8); out.writeUTF("ip"); out.writeUTF(e.ip)
                e.acceptTextures?.let { out.writeByte(1); out.writeUTF("acceptTextures"); out.writeByte(it.toInt()) }
                out.writeByte(0)
            }
            out.writeByte(0)
        }
        Files.move(tmp, file, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE)
    }

    private fun readCompound(input: DataInputStream): Map<String, Any?> {
        val map = HashMap<String, Any?>()
        while (true) {
            val type = input.readByte().toInt()
            if (type == 0) return map
            map[input.readUTF()] = readPayload(input, type)
        }
    }

    private fun readPayload(input: DataInputStream, type: Int): Any? = when (type) {
        1 -> input.readByte()
        2 -> input.readShort()
        3 -> input.readInt()
        4 -> input.readLong()
        5 -> input.readFloat()
        6 -> input.readDouble()
        7 -> ByteArray(input.readInt()).also(input::readFully)
        8 -> input.readUTF()
        9 -> { val t = input.readByte().toInt(); List(input.readInt()) { readPayload(input, t) } }
        10 -> readCompound(input)
        11 -> IntArray(input.readInt()) { input.readInt() }
        12 -> LongArray(input.readInt()) { input.readLong() }
        else -> throw IllegalStateException("bad NBT tag $type")
    }
}

object GameOptions {
    fun enableResourcePack(optionsFile: Path, packName: String, compatible: Boolean, stale: Collection<String>) {
        val lines = if (Files.isRegularFile(optionsFile)) Files.readAllLines(optionsFile).toMutableList() else mutableListOf()
        val entry = "file/$packName"
        fun edit(key: String, transform: (MutableList<String>) -> Unit) {
            val idx = lines.indexOfFirst { it.startsWith("$key:") }
            val current = if (idx >= 0) parseList(lines[idx].substringAfter(':')) else mutableListOf()
            transform(current)
            val line = "$key:" + current.joinToString(",", "[", "]") { "\"" + it.replace("\"", "\\\"") + "\"" }
            if (idx >= 0) lines[idx] = line else lines += line
        }
        edit("resourcePacks") { list ->
            list.removeAll { it in stale.map { s -> "file/$s" } }
            if ("vanilla" !in list) list.add(0, "vanilla")
            if (entry !in list) list += entry
        }
        edit("incompatibleResourcePacks") { list ->
            list.removeAll { it in stale.map { s -> "file/$s" } || it == entry }
            if (!compatible) list += entry
        }
        Files.createDirectories(optionsFile.parent)
        Files.write(optionsFile, lines)
    }

    fun setFullscreen(optionsFile: Path, fullscreen: Boolean) {
        val lines = if (Files.isRegularFile(optionsFile)) Files.readAllLines(optionsFile).toMutableList() else mutableListOf()
        val idx = lines.indexOfFirst { it.startsWith("fullscreen:") }
        val line = "fullscreen:$fullscreen"
        if (idx >= 0) lines[idx] = line else lines += line
        Files.createDirectories(optionsFile.parent)
        Files.write(optionsFile, lines)
    }

    private fun parseList(raw: String): MutableList<String> =
        Regex("\"((?:[^\"\\\\]|\\\\.)*)\"").findAll(raw).map { it.groupValues[1].replace("\\\"", "\"") }.toMutableList()
}
