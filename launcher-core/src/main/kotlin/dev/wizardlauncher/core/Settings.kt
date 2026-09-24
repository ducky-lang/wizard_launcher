package dev.wizardlauncher.core

import com.google.gson.JsonObject
import java.lang.management.ManagementFactory
import java.nio.file.Path

class Settings(private val file: Path) {
    var serverRamMb = 0
    var clientRamMb = 0
    var memoryProfile = MemoryProfile.BALANCED
    var allowLan = false
    var offlineOnly = false
    var autoRestartServer = true
    var convertResourcePack = true
    var javaPath = ""
    var closeLauncherOnPlay = false
    var offlineName = ""
    var keepLogDays = 7

    enum class MemoryProfile { LOW, BALANCED, HIGH }

    fun load(): Settings {
        val o = Json.read(file)?.takeIf { it.isJsonObject }?.asJsonObject ?: return this
        serverRamMb = ram(o, "server_ram_mb")
        clientRamMb = ram(o, "client_ram_mb")
        memoryProfile = runCatching { MemoryProfile.valueOf(o.get("memory_profile").asString) }.getOrDefault(MemoryProfile.BALANCED)
        allowLan = bool(o, "allow_lan", false)
        offlineOnly = bool(o, "offline_only", false)
        autoRestartServer = bool(o, "auto_restart_server", true)
        convertResourcePack = bool(o, "convert_resource_pack", true)
        javaPath = o.get("java_path")?.takeIf { it.isJsonPrimitive }?.asString ?: ""
        closeLauncherOnPlay = bool(o, "close_launcher_on_play", false)
        offlineName = o.get("offline_name")?.takeIf { it.isJsonPrimitive }?.asString?.takeIf(::validName) ?: ""
        keepLogDays = runCatching { o.get("keep_log_days").asInt.coerceIn(1, 365) }.getOrDefault(7)
        return this
    }

    fun save() {
        val o = JsonObject().apply {
            addProperty("server_ram_mb", serverRamMb)
            addProperty("client_ram_mb", clientRamMb)
            addProperty("memory_profile", memoryProfile.name)
            addProperty("allow_lan", allowLan)
            addProperty("offline_only", offlineOnly)
            addProperty("auto_restart_server", autoRestartServer)
            addProperty("convert_resource_pack", convertResourcePack)
            addProperty("java_path", javaPath)
            addProperty("close_launcher_on_play", closeLauncherOnPlay)
            addProperty("offline_name", offlineName)
            addProperty("keep_log_days", keepLogDays)
        }
        Json.write(file, o)
    }

    val bindAddress get() = if (allowLan) "0.0.0.0" else "127.0.0.1"

    val effectiveServerRamMb: Int get() = serverRamMb.takeIf { it > 0 } ?: when (memoryProfile) {
        MemoryProfile.LOW -> 768
        MemoryProfile.BALANCED -> if (SystemInfo.totalRamMb <= 8192) 1280 else 1536
        MemoryProfile.HIGH -> if (SystemInfo.totalRamMb <= 8192) 1536 else 2560
    }

    val effectiveClientRamMb: Int get() = clientRamMb.takeIf { it > 0 } ?: when {
        memoryProfile == MemoryProfile.LOW -> 1536
        SystemInfo.totalRamMb <= 6144 -> 1536
        SystemInfo.totalRamMb <= 8192 -> 2048
        SystemInfo.totalRamMb <= 16384 -> 3072
        else -> 4096
    }

    val viewDistance: Int get() = when (memoryProfile) { MemoryProfile.LOW -> 6; MemoryProfile.BALANCED -> 8; MemoryProfile.HIGH -> 10 }

    companion object {
        private fun ram(o: JsonObject, key: String) =
            runCatching { o.get(key).asInt }.getOrDefault(0).let { if (it <= 0) 0 else it.coerceIn(512, 32768) }
        private fun bool(o: JsonObject, key: String, default: Boolean) =
            runCatching { o.get(key).asBoolean }.getOrDefault(default)

        fun validName(name: String) = Regex("^[A-Za-z0-9_]{3,16}$").matches(name)
    }
}

object SystemInfo {
    val totalRamMb: Int by lazy {
        runCatching {
            (ManagementFactory.getOperatingSystemMXBean() as com.sun.management.OperatingSystemMXBean)
                .totalMemorySize / (1024 * 1024)
        }.getOrDefault(8192L).toInt()
    }
}
