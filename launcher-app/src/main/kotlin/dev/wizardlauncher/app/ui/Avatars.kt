package dev.wizardlauncher.app.ui

import com.google.gson.JsonParser
import dev.wizardlauncher.core.Launcher
import dev.wizardlauncher.core.Log
import dev.wizardlauncher.core.library.Images
import dev.wizardlauncher.core.net.SecureDownloader
import java.nio.file.Files
import java.util.Base64
import java.util.zip.ZipFile

class Avatars(private val launcher: Launcher) {
    private val dir = launcher.paths.root.resolve("cache").resolve("avatars")

    fun get(uuid: String): ByteArray? {
        if (!Regex("^[0-9a-fA-F-]{8,36}$").matches(uuid)) return null
        val cached = dir.resolve("$uuid.png")
        if (Files.isRegularFile(cached) && System.currentTimeMillis() - Files.getLastModifiedTime(cached).toMillis() < 86_400_000L) {
            return Files.readAllBytes(cached)
        }
        val account = launcher.accounts.list().firstOrNull { it.uuid.equals(uuid, ignoreCase = true) }
        val face = when {
            account != null && account.isMicrosoft && !launcher.settings.offlineOnly -> runCatching { fetch(uuid) }.getOrNull()
            else -> null
        } ?: defaultFace() ?: return if (Files.isRegularFile(cached)) Files.readAllBytes(cached) else null
        Files.createDirectories(dir)
        Files.write(cached, face)
        return face
    }

    private fun fetch(uuid: String): ByteArray? {
        val downloader = SecureDownloader(setOf("mojang.com", "minecraft.net"), maxRetries = 1)
        val profile = JsonParser.parseString(downloader.fetchText("https://sessionserver.mojang.com/session/minecraft/profile/${uuid.replace("-", "")}")).asJsonObject
        val textures = profile.getAsJsonArray("properties").map { it.asJsonObject }.firstOrNull { it.get("name").asString == "textures" } ?: return null
        val decoded = JsonParser.parseString(String(Base64.getDecoder().decode(textures.get("value").asString))).asJsonObject
        val skinUrl = decoded.getAsJsonObject("textures")?.getAsJsonObject("SKIN")?.get("url")?.asString ?: return null
        val skin = downloader.fetchBytes(skinUrl.replaceFirst("http://", "https://"), maxBytes = 1L shl 20)
        return Images.face(skin)
    }

    private fun defaultFace(): ByteArray? = runCatching {
        val jar = launcher.paths.versions.resolve("1.20.1").resolve("1.20.1.jar")
        if (!Files.isRegularFile(jar)) return null
        ZipFile(jar.toFile()).use { zip ->
            val entry = zip.getEntry("assets/minecraft/textures/entity/player/wide/steve.png") ?: return null
            Images.face(zip.getInputStream(entry).readBytes())
        }
    }.onFailure { Log.file("Default avatar unavailable: ${it.message}") }.getOrNull()
}
