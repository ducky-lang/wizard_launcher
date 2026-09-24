package dev.wizardlauncher.core

import com.google.gson.JsonObject
import com.google.gson.JsonParser
import dev.wizardlauncher.core.security.Signatures
import java.nio.file.Files
import java.nio.file.Path

class Catalog private constructor(root: JsonObject) {
    data class Minecraft(val clientVersion: String, val serverVersion: String, val fabricLoaderFallback: String, val requiredJava: Int)
    data class PinnedFile(val path: String, val sha256: String)
    data class Server(
        val mode: String, val port: Int, val proxyPort: Int, val entryName: String,
        val serverJar: PinnedFile, val proxyJar: PinnedFile, val properties: Map<String, String>,
    )
    data class Download(val content: Set<String>, val game: Set<String>, val mods: Set<String>, val maxRetries: Int, val backoffMs: Long)
    data class ExtraMod(val name: String, val path: String, val url: String, val sha512: String)
    data class Modpack(val id: String, val name: String, val version: String, val url: String, val sha512: String, val approxMb: Int, val extraMods: List<ExtraMod>)
    data class Resource(val id: String, val name: String, val kind: String, val url: String, val sha256: String, val approxMb: Int, val convertFrom: Int?)

    val minecraft: Minecraft
    val server: Server
    val download: Download
    val modpack: Modpack
    val resources: List<Resource>
    val signingKey: String

    init {
        val mc = root.obj("minecraft")
        minecraft = Minecraft(
            mc.str("client_version", "1.20.1"), mc.str("server_version", "1.16.5"),
            mc.str("fabric_loader_fallback", "0.15.11"), mc.int("required_java_major", 17))
        val s = root.obj("server")
        server = Server(
            mode = s.str("mode", "hosted"),
            port = s.int("port", 25565),
            proxyPort = s.int("proxy_port", 25566),
            entryName = s.str("entry_name", "Witchcraft and Wizardry"),
            serverJar = s.obj("server_jar").let { PinnedFile(it.str("path", "servers/1.16.5/server.jar"), it.str("sha256", "")) },
            proxyJar = s.obj("proxy_jar").let { PinnedFile(it.str("path", "proxy/ViaProxy.jar"), it.str("sha256", "")) },
            properties = s.obj("properties").entrySet()
                .filter { !it.key.startsWith("_") && it.value.isJsonPrimitive }
                .associate { it.key to it.value.asString },
        )
        val d = root.obj("download")
        download = Download(
            d.set("content_domains", setOf("huggingface.co", "hf.co")),
            d.set("game_domains", setOf("mojang.com", "minecraft.net", "fabricmc.net")),
            d.set("mod_domains", setOf("modrinth.com")),
            d.int("max_retries", 4), d.int("backoff_seconds", 2) * 1000L)
        val m = root.obj("modpack")
        modpack = Modpack(
            m.str("id", "fabulously-optimized"), m.str("name", "Fabulously Optimized"), m.str("version", ""),
            m.str("url", ""), m.str("sha512", ""), m.int("approx_mb", 120),
            (m.getAsJsonArray("extra_mods") ?: com.google.gson.JsonArray()).map { it.asJsonObject }.map {
                ExtraMod(it.str("name", ""), it.str("path", ""), it.str("url", ""), it.str("sha512", ""))
            })
        resources = (root.getAsJsonArray("resources") ?: com.google.gson.JsonArray()).map { it.asJsonObject }.map {
            Resource(it.str("id", ""), it.str("name", ""), it.str("kind", ""), it.str("url", ""),
                it.str("sha256", ""), it.int("approx_mb", 0), it.get("convert_from")?.asInt)
        }
        signingKey = root.obj("signing").str("public_key", "")
    }

    fun resource(id: String): Resource = resources.firstOrNull { it.id == id }
        ?: throw LauncherException("The catalog has no '$id' entry. Reinstall the launcher.")

    val approxDownloadMb: Int get() = resources.sumOf { it.approxMb } + modpack.approxMb + 900

    companion object {
        @Volatile private var loaded: Catalog? = null
        val current: Catalog get() = loaded ?: bundled().also { loaded = it }

        fun bundled(): Catalog = Catalog(JsonParser.parseString(bundledText()).asJsonObject)

        private fun bundledText(): String =
            Catalog::class.java.getResourceAsStream("catalog.json")!!.use { it.readBytes().toString(Charsets.UTF_8) }

        fun load(dataRoot: Path): Catalog {
            val base = bundled()
            val file = dataRoot.resolve("catalog.json")
            val sig = dataRoot.resolve("catalog.json.sig")
            loaded = base
            if (!Files.exists(file)) return base
            if (base.signingKey.isBlank()) {
                Log.file("Ignoring ${file.fileName}: this build has no catalog signing key.")
                return base
            }
            val data = Files.readAllBytes(file)
            if (!Files.exists(sig) || !Signatures.verify(data, Files.readString(sig), base.signingKey)) {
                Log.info("Ignoring an updated catalog whose signature does not verify.")
                return base
            }
            return runCatching { Catalog(JsonParser.parseString(String(data)).asJsonObject) }
                .onSuccess { Log.info("Using the signed catalog update.") }
                .getOrElse { Log.error("Signed catalog is malformed; using the bundled one.", it); base }
                .also { loaded = it }
        }

        private fun JsonObject.obj(name: String): JsonObject = getAsJsonObject(name) ?: JsonObject()
        private fun JsonObject.str(name: String, default: String): String =
            get(name)?.takeIf { it.isJsonPrimitive }?.asString ?: default
        private fun JsonObject.int(name: String, default: Int): Int =
            runCatching { get(name)?.asInt }.getOrNull() ?: default
        private fun JsonObject.set(name: String, default: Set<String>): Set<String> =
            getAsJsonArray(name)?.map { it.asString.lowercase().trim().trimStart('.') }?.filter { it.isNotEmpty() }?.toSet()
                ?.takeIf { it.isNotEmpty() } ?: default
    }
}
