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
    class Plan(val command: List<String>, val mainClass: String, val gameArgs: List<String>)

    fun plan(profile: VersionProfile, account: Account, joinAddress: String?, waitForWorld: Boolean = false): Plan {
        val classpath: List<Path> = profile.libraries.map { paths.libraries.resolve(it.path) } + listOf(profile.clientJar, bootJar)
        val missing = classpath.filterNot(Files::isRegularFile)
        if (missing.isNotEmpty()) {
            Log.file("Missing classpath entries: " + missing.joinToString())
            throw LauncherException("${missing.size} game file(s) are missing, for example:\n${missing.first().toAbsolutePath()}\n\n" +
                "Press Play again with an internet connection to repair the install, or use Tools > Repair game files.")
        }
        val width = settings.gameWidth
        val height = settings.gameHeight
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
            "quickPlayMultiplayer" to (joinAddress ?: ""),
            "resolution_width" to width.toString(),
            "resolution_height" to height.toString(),
        )
        val features = mapOf("is_quick_play_multiplayer" to (joinAddress != null), "has_custom_resolution" to (width > 0 && height > 0))
        val jvm = expand(profile.jvmArgs, vars, features)
        val game = expand(profile.gameArgs, vars, features)
        val command = ArrayList<String>()
        command += javaExe.toString()
        command += JvmFlags.client(settings.effectiveClientRamMb)
        command += "-Djava.awt.headless=true"
        if (waitForWorld && joinAddress != null) command += "-Dwizard.waitForWorld=true"
        command += jvm
        command += "dev.wizardlauncher.boot.SecureBoot"
        return Plan(command, profile.mainClass, game)
    }

    fun command(profile: VersionProfile, account: Account, joinAddress: String?): List<String> =
        plan(profile, account, joinAddress).command

    fun launch(profile: VersionProfile, account: Account, joinAddress: String?, waitForWorld: Boolean = false): Process {
        val plan = plan(profile, account, joinAddress, waitForWorld)
        val command = plan.command
        val game = plan.gameArgs
        GameOptions.setFullscreen(paths.gameDir.resolve("options.txt"), settings.fullscreen)
        GameOptions.disableModelCulling(paths.gameDir)
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
            w.write(b64.encodeToString(plan.mainClass.toByteArray()) + "\n")
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
