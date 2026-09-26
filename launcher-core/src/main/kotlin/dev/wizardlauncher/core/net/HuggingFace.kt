package dev.wizardlauncher.core.net

import com.google.gson.JsonParser
import dev.wizardlauncher.core.Log
import java.net.URLDecoder
import java.net.URLEncoder
import java.nio.charset.StandardCharsets
import java.util.concurrent.ConcurrentHashMap

object HuggingFace {
    sealed class Lookup {
        data class Found(val sha256: String?, val size: Long) : Lookup()
        data class Missing(val file: String, val repo: String) : Lookup()
        object Unknown : Lookup()
    }

    data class Location(val kind: String, val repo: String, val revision: String, val path: String)

    private val RESOLVE = Regex("^https://huggingface\\.co/(datasets/|spaces/)?([^/]+/[^/]+)/resolve/([^/]+)/([^?#]+)(?:[?#].*)?$")
    private val cache = ConcurrentHashMap<String, Pair<Long, Lookup>>()

    fun parse(url: String): Location? {
        val m = RESOLVE.matchEntire(url) ?: return null
        val kind = m.groupValues[1].trimEnd('/').ifEmpty { "models" }
        val path = URLDecoder.decode(m.groupValues[4], StandardCharsets.UTF_8)
        return Location(kind, m.groupValues[2], URLDecoder.decode(m.groupValues[3], StandardCharsets.UTF_8), path)
    }

    fun lookup(url: String, downloader: SecureDownloader): Lookup {
        val loc = parse(url) ?: return Lookup.Unknown
        cache[url]?.let { (at, value) -> if (System.currentTimeMillis() - at < 60_000) return value }
        val dir = loc.path.substringBeforeLast('/', "")
        val api = "https://huggingface.co/api/${loc.kind}/${loc.repo}/tree/${encode(loc.revision)}" +
            (if (dir.isEmpty()) "" else "/" + dir.split('/').joinToString("/") { encode(it) })
        val result = runCatching {
            val entries = JsonParser.parseString(downloader.fetchText(api)).asJsonArray.map { it.asJsonObject }
            val entry = entries.firstOrNull { it.get("path")?.asString == loc.path }
            if (entry == null) {
                Lookup.Missing(loc.path, loc.repo)
            } else {
                val lfs = entry.getAsJsonObject("lfs")
                Lookup.Found(lfs?.get("oid")?.asString, (lfs?.get("size") ?: entry.get("size"))?.asLong ?: -1)
            }
        }.getOrElse {
            Log.file("Could not ask Hugging Face about ${loc.path}: ${it.message}")
            Lookup.Unknown
        }
        cache[url] = System.currentTimeMillis() to result
        return result
    }

    private fun encode(part: String) = URLEncoder.encode(part, StandardCharsets.UTF_8).replace("+", "%20")
}
