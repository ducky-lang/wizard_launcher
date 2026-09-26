package dev.wizardlauncher.core

import com.google.gson.Gson
import com.google.gson.GsonBuilder
import com.google.gson.JsonElement
import com.google.gson.JsonParser
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardCopyOption

object Json {
    val gson: Gson = GsonBuilder().setPrettyPrinting().disableHtmlEscaping().create()

    fun read(path: Path): JsonElement? =
        runCatching { Files.newBufferedReader(path).use { JsonParser.parseReader(it) } }.getOrNull()

    fun write(path: Path, value: Any) = writeText(path, gson.toJson(value))

    fun writeText(path: Path, text: String) {
        path.parent?.let { Files.createDirectories(it) }
        val tmp = path.resolveSibling(path.fileName.toString() + ".tmp")
        Files.writeString(tmp, text)
        Files.move(tmp, path, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE)
    }
}
