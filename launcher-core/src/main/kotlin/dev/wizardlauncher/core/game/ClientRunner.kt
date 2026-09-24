package dev.wizardlauncher.core.game

import com.google.gson.JsonElement
import dev.wizardlauncher.core.AppPaths
import dev.wizardlauncher.core.BuildInfo
import dev.wizardlauncher.core.LauncherException
import dev.wizardlauncher.core.Log
import dev.wizardlauncher.core.Settings
import dev.wizardlauncher.core.auth.Account
import dev.wizardlauncher.core.install.Rules
import dev.wizardlauncher.core.install.VersionProfile
import java.io.File
import java.nio.file.Files
import java.nio.file.Path
import java.util.Base64

class ClientRunner(
    private val paths: AppPaths,
    private val settings: Settings,
    private val supervisor: ProcessSupervisor,
    private val javaExe: Path,
    private val bootJar: Path,
) {
    fun launch(profile: VersionProfile, account: Account, joinAddress: String): Process {
        val classpath = (profile.libraries.map { paths.libraries.resolve(it.path) } + profile.clientJar + bootJar)
        classpath.firstOrNull { !Files.isRegularFile(it) }?.let {
            throw LauncherException("A game file is missing:\n$it\n\nConnect to the internet once and press Play to repair the install.")
        }
        val vars = mapOf(
            "auth_player_name" to account.name,
            "version_name" to profile.id,
            "game_directory" to paths.gameDir.toString(),
            "assets_root" to paths.assets.toString(),
            "assets_index_name" to profile.assetIndex,
            "auth_uuid" to account.uuid,
            "auth_access_token" to account.accessToken,
            "clientid" to account.clientId,
            "auth_xuid" to account.xuid,
            "user_type" to account.userType,
            "version_type" to profile.versionType,
            "natives_directory" to profile.nativesDir.toString(),
            "launcher_name" to "WizardLauncher",
            "launcher_version" to BuildInfo.version,
            "classpath" to classpath.joinToString(File.pathSeparator),
            "classpath_separator" to File.pathSeparator,
            "library_directory" to paths.libraries.toString(),
            "quickPlayMultiplayer" to joinAddress,
        )
        val features = mapOf("is_quick_play_multiplayer" to true)
        val jvm = expand(profile.jvmArgs, vars, features)
        val game = expand(profile.gameArgs, vars, features)

        val command = ArrayList<String>()
        command += javaExe.toString()
        command += JvmFlags.client(settings.effectiveClientRamMb)
        command += jvm
        command += "dev.wizardlauncher.boot.SecureBoot"

        Log.file("Client command: " + command.joinToString(" ") { if (it.length > 300) it.take(80) + "...(${it.length} chars)" else it })

        val output = paths.logs.resolve("client-output.log").toFile()
        val process = ProcessBuilder(command)
            .directory(paths.gameDir.toFile())
            .redirectErrorStream(true)
            .redirectOutput(ProcessBuilder.Redirect.to(output))
            .start()
        process.outputStream.bufferedWriter().let { w ->
            val b64 = Base64.getEncoder()
            w.write("WIZARD-BOOT/1\n")
            w.write(b64.encodeToString(profile.mainClass.toByteArray()) + "\n")
            w.write("${game.size}\n")
            game.forEach { w.write(b64.encodeToString(it.toByteArray()) + "\n") }
            w.flush()
        }
        supervisor.register("client", process)
        return process
    }

    private fun expand(args: List<JsonElement>, vars: Map<String, String>, features: Map<String, Boolean>): List<String> {
        val out = ArrayList<String>()
        fun sub(s: String) = Regex("\\$\\{([a-zA-Z_]+)}").replace(s) { m -> vars[m.groupValues[1]] ?: m.value }
        for (arg in args) {
            if (arg.isJsonPrimitive) { out += sub(arg.asString); continue }
            val o = arg.asJsonObject
            if (!Rules.allow(o.getAsJsonArray("rules"), features)) continue
            val value = o.get("value")
            if (value.isJsonArray) value.asJsonArray.forEach { out += sub(it.asString) } else out += sub(value.asString)
        }

        val cleaned = ArrayList<String>()
        var i = 0
        while (i < out.size) {
            val next = out.getOrNull(i + 1)
            if (out[i].startsWith("--") && next != null && next.contains("\${")) { i += 2; continue }
            if (!out[i].contains("\${")) cleaned += out[i]
            i++
        }
        return cleaned
    }
}
