package dev.wizardlauncher.core.install

import com.google.gson.JsonObject
import dev.wizardlauncher.core.Json
import dev.wizardlauncher.core.security.Hashes
import java.nio.file.Path

/**
 * Records what has been installed, keyed by a fingerprint of *what was
 * asked for* (versions, URLs, hashes). A launch whose fingerprints all match
 * does no network I/O and no re-hashing at all - which is both why a warm
 * start is fast and why it works with the Wi-Fi off.
 */
class InstallState(private val file: Path) {
    private val data: JsonObject = Json.read(file)?.takeIf { it.isJsonObject }?.asJsonObject ?: JsonObject()

    @Synchronized fun matches(key: String, fingerprint: String) = data.get(key)?.asString == fingerprint
    @Synchronized fun get(key: String): String? = data.get(key)?.asString

    @Synchronized fun mark(key: String, value: String) {
        data.addProperty(key, value)
        Json.write(file, data)
    }

    @Synchronized fun forget(key: String) {
        data.remove(key)
        Json.write(file, data)
    }

    companion object {
        fun fingerprint(vararg parts: Any?): String =
            Hashes.of(parts.joinToString("\u0000") { it?.toString() ?: "" }.toByteArray(), "SHA-256").take(32)
    }
}
