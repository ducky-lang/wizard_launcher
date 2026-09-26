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
    var javaPath = ""
    var afterLaunch = AfterLaunch.MINIMIZE
    var offlineName = ""
    var keepLogDays = 7
    var animations = true
    var hardwareAcceleration = true
    var gameWidth = 0
    var gameHeight = 0
    var fullscreen = false
    var checkUpdates = true
    var onboardingDone = false
    var selectedInstance = ""

    enum class MemoryProfile { LOW, BALANCED, HIGH }
    enum class AfterLaunch { KEEP_OPEN, MINIMIZE, CLOSE }

    val closeLauncherOnPlay get() = afterLaunch == AfterLaunch.CLOSE

    fun load(): Settings {
        val o = Json.read(file)?.takeIf { it.isJsonObject }?.asJsonObject ?: return this
        apply(o)
        return this
    }

    fun apply(o: JsonObject) {
        if (o.has("server_ram_mb")) serverRamMb = ram(o, "server_ram_mb")
        if (o.has("client_ram_mb")) clientRamMb = ram(o, "client_ram_mb")
        if (o.has("memory_profile")) memoryProfile = runCatching { MemoryProfile.valueOf(o.get("memory_profile").asString) }.getOrDefault(memoryProfile)
        allowLan = bool(o, "allow_lan", allowLan)
        offlineOnly = bool(o, "offline_only", offlineOnly)
        autoRestartServer = bool(o, "auto_restart_server", autoRestartServer)
        if (o.has("java_path")) javaPath = o.get("java_path")?.takeIf { it.isJsonPrimitive }?.asString?.trim() ?: ""
        if (o.has("close_launcher_on_play") && bool(o, "close_launcher_on_play", false)) afterLaunch = AfterLaunch.CLOSE
        if (o.has("after_launch")) afterLaunch = runCatching { AfterLaunch.valueOf(o.get("after_launch").asString) }.getOrDefault(afterLaunch)
        if (o.has("offline_name")) offlineName = o.get("offline_name")?.takeIf { it.isJsonPrimitive }?.asString?.takeIf(::validName) ?: ""
        if (o.has("keep_log_days")) keepLogDays = runCatching { o.get("keep_log_days").asInt.coerceIn(1, 365) }.getOrDefault(keepLogDays)
        animations = bool(o, "animations", animations)
        hardwareAcceleration = bool(o, "hardware_acceleration", hardwareAcceleration)
        if (o.has("game_width")) gameWidth = runCatching { o.get("game_width").asInt }.getOrDefault(0).let { if (it <= 0) 0 else it.coerceIn(640, 7680) }
        if (o.has("game_height")) gameHeight = runCatching { o.get("game_height").asInt }.getOrDefault(0).let { if (it <= 0) 0 else it.coerceIn(480, 4320) }
        fullscreen = bool(o, "fullscreen", fullscreen)
        checkUpdates = bool(o, "check_updates", checkUpdates)
        onboardingDone = bool(o, "onboarding_done", onboardingDone)
        if (o.has("selected_instance")) selectedInstance = o.get("selected_instance")?.takeIf { it.isJsonPrimitive }?.asString?.takeIf { Regex("^[A-Za-z0-9._-]{1,64}$").matches(it) } ?: selectedInstance
    }

    fun toJson(): JsonObject = JsonObject().apply {
        addProperty("server_ram_mb", serverRamMb)
        addProperty("client_ram_mb", clientRamMb)
        addProperty("memory_profile", memoryProfile.name)
        addProperty("allow_lan", allowLan)
        addProperty("offline_only", offlineOnly)
        addProperty("auto_restart_server", autoRestartServer)
        addProperty("java_path", javaPath)
        addProperty("after_launch", afterLaunch.name)
        addProperty("offline_name", offlineName)
        addProperty("keep_log_days", keepLogDays)
        addProperty("animations", animations)
        addProperty("hardware_acceleration", hardwareAcceleration)
        addProperty("game_width", gameWidth)
        addProperty("game_height", gameHeight)
        addProperty("fullscreen", fullscreen)
        addProperty("check_updates", checkUpdates)
        addProperty("onboarding_done", onboardingDone)
        addProperty("selected_instance", selectedInstance)
    }

    fun save() = Json.write(file, toJson())

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
