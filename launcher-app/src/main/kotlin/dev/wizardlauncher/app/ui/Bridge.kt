package dev.wizardlauncher.app.ui

import com.google.gson.Gson
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import dev.wizardlauncher.core.BuildInfo
import dev.wizardlauncher.core.Catalog
import dev.wizardlauncher.core.Launcher
import dev.wizardlauncher.core.LauncherException
import dev.wizardlauncher.core.Log
import dev.wizardlauncher.core.SystemInfo
import dev.wizardlauncher.core.auth.MicrosoftAuth
import dev.wizardlauncher.core.install.ContentInstaller
import dev.wizardlauncher.core.install.OfflineBundle
import dev.wizardlauncher.core.install.Progress
import dev.wizardlauncher.core.library.CrashReports
import dev.wizardlauncher.core.library.HeroArt
import dev.wizardlauncher.core.library.ModLibrary
import dev.wizardlauncher.core.library.PackLibrary
import dev.wizardlauncher.core.library.ScreenshotLibrary
import dev.wizardlauncher.core.library.ShaderLibrary
import dev.wizardlauncher.core.library.UpdateChecker
import dev.wizardlauncher.core.library.UpdateInfo
import dev.wizardlauncher.core.library.Worlds
import dev.wizardlauncher.core.install.ModpackInstaller
import dev.wizardlauncher.core.net.Connectivity
import dev.wizardlauncher.core.net.SecureDownloader
import dev.wizardlauncher.legacy.PackExporter
import java.awt.Desktop
import java.awt.Toolkit
import java.awt.datatransfer.StringSelection
import java.net.URI
import java.nio.file.Files
import java.nio.file.Path
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import javax.swing.JFileChooser
import javax.swing.SwingUtilities
import javax.swing.filechooser.FileNameExtensionFilter

class Bridge(private val launcher: Launcher, private val host: Host) {
    interface Host {
        fun emit(event: String, data: Any?)
        fun chooseFiles(title: String, save: Boolean, dirs: Boolean, suggested: String?, filter: FileNameExtensionFilter?, multi: Boolean): List<Path>
        fun gameStarted()
        fun gameEnded()
        fun quit()
    }

    private val gson = Gson()
    private val pool = Executors.newFixedThreadPool(4) { r -> Thread(r, "ui-bridge").apply { isDaemon = true } }
    private val paths = launcher.paths
    private val modpack get() = ModpackInstaller(paths, launcher.state, SecureDownloader(Catalog.current.download.mods), Progress.NONE)
    private val mods get() = ModLibrary(paths.gameDir.resolve("mods")) { modpack.managedFiles() }
    private val packs get() = PackLibrary(paths.gameDir)
    private val shaders get() = ShaderLibrary(paths.gameDir)
    private val shots get() = ScreenshotLibrary(paths.gameDir, paths.root.resolve("cache"))
    private val worlds get() = Worlds(paths.worldDir, paths.backups)
    private val hero get() = HeroArt(paths.gameDir, paths.root.resolve("cache"), shots)
    private val avatars = Avatars(launcher)
    @Volatile private var gameState = "idle"
    @Volatile private var update: UpdateInfo? = null
    @Volatile private var online = false
    private val msCancel = AtomicBoolean(false)

    fun start() {
        pool.submit {
            online = !launcher.settings.offlineOnly && Connectivity.online()
            if (online && launcher.settings.checkUpdates) update = UpdateChecker.check()
            host.emit("info.changed", null)
        }
    }

    fun handle(request: String, reply: (String) -> Unit, fail: (String) -> Unit) {
        val parsed = runCatching { JsonParser.parseString(request).asJsonObject }.getOrNull()
        val cmd = parsed?.get("cmd")?.asString
        if (cmd == null || !Regex("^[a-zA-Z]+\\.[a-zA-Z]+$").matches(cmd)) {
            fail("bad request")
            return
        }
        val args = parsed.getAsJsonObject("args") ?: JsonObject()
        pool.submit {
            try {
                reply(gson.toJson(dispatch(cmd, args)))
            } catch (e: LauncherException) {
                fail(e.message ?: "Failed")
            } catch (e: Exception) {
                Log.error("UI command $cmd failed: ${e.message}", e)
                fail(e.message ?: e.javaClass.simpleName)
            }
        }
    }

    private fun JsonObject.str(key: String): String = get(key)?.takeIf { it.isJsonPrimitive }?.asString
        ?: throw LauncherException("Missing $key")

    private fun dispatch(cmd: String, a: JsonObject): Any? = when (cmd) {
        "app.info" -> info()
        "ui.ready" -> null
        "settings.get" -> launcher.settings.toJson()
        "settings.set" -> {
            val before = launcher.settings.offlineOnly
            launcher.settings.apply(a)
            launcher.settings.save()
            if (before != launcher.settings.offlineOnly) start()
            launcher.settings.toJson()
        }
        "accounts.list" -> mapOf(
            "accounts" to launcher.accounts.list().map { mapOf("id" to it.id, "type" to it.type, "name" to it.name, "uuid" to it.uuid) },
            "selected" to launcher.accounts.selectedId,
        )
        "accounts.select" -> launcher.accounts.select(a.str("id"))
        "accounts.remove" -> launcher.accounts.remove(a.str("id"))
        "accounts.addOffline" -> launcher.accounts.addOffline(a.str("name")).let { mapOf("id" to it.id) }
        "accounts.msBegin" -> beginMicrosoft()
        "accounts.msCancel" -> msCancel.set(true)
        "game.status" -> mapOf("state" to gameState)
        "game.play" -> play()
        "game.stop" -> { launcher.stop { host.emit("launch.progress", mapOf("fraction" to null, "message" to it, "step" to 3)) }; null }
        "mods.list" -> mods.list()
        "mods.toggle" -> mods.setEnabled(a.str("name"), a.get("enabled").asBoolean)
        "mods.remove" -> mods.remove(a.str("name"))
        "mods.import" -> mapOf("count" to mods.import(host.chooseFiles("Add mods", false, false, null, FileNameExtensionFilter("Fabric mods", "jar"), true)))
        "mods.openFolder" -> open(paths.gameDir.resolve("mods"))
        "packs.list" -> packs.list()
        "packs.toggle" -> packs.setEnabled(a.str("name"), a.get("enabled").asBoolean)
        "packs.remove" -> packs.remove(a.str("name"))
        "packs.import" -> mapOf("count" to packs.import(host.chooseFiles("Add resource packs", false, true, null, null, true)))
        "packs.openFolder" -> open(paths.gameDir.resolve("resourcepacks"))
        "packs.export" -> exportPack(a.str("name"))
        "shaders.list" -> shaders.list()
        "shaders.toggle" -> null
        "shaders.remove" -> shaders.remove(a.str("name"))
        "shaders.import" -> mapOf("count" to shaders.import(host.chooseFiles("Add shader packs", false, false, null, FileNameExtensionFilter("Shader packs", "zip"), true)))
        "shaders.openFolder" -> open(paths.gameDir.resolve("shaderpacks"))
        "screenshots.list" -> shots.list()
        "screenshots.open" -> shots.resolve(a.str("name"))?.let { open(it) }
        "screenshots.delete" -> shots.delete(a.str("name"))
        "screenshots.openFolder" -> open(paths.gameDir.resolve("screenshots"))
        "worlds.info" -> worlds.info()
        "worlds.backup" -> { requireIdle(); launcher.backupWorld(); null }
        "worlds.reset" -> { requireIdle(); launcher.resetWorld(progress()); null }
        "worlds.restore" -> { requireIdle(); worlds.restore(a.str("name")) { launcher.backupWorld() }; null }
        "worlds.deleteBackup" -> worlds.deleteBackup(a.str("name"))
        "worlds.openFolder" -> open(paths.worldDir)
        "worlds.selftest" -> { requireIdle(); mapOf("report" to launcher.selfTestWorld(progress())) }
        "logs.recent" -> mapOf("lines" to Log.recentLines())
        "logs.openFolder" -> open(paths.logs)
        "crash.openFolder" -> open(paths.gameDir.resolve("crash-reports").takeIf(Files::isDirectory) ?: paths.gameDir)
        "tools.repair" -> { requireIdle(); launcher.verifyInstall(progress()); null }
        "tools.exportBundle" -> host.chooseFiles("Save offline bundle", true, false, "WizardLauncher-offline.wizardpack", null, false).firstOrNull()
            ?.let { OfflineBundle.export(paths, it, progress()); mapOf("path" to it.toString()) }
        "tools.importBundle" -> host.chooseFiles("Choose an offline bundle", false, false, null, FileNameExtensionFilter("Offline bundle", "wizardpack"), false).firstOrNull()
            ?.let { requireIdle(); OfflineBundle.import(paths, it, progress()); mapOf("done" to true) }
        "tools.stateRules" -> openRules()
        "tools.openData" -> open(paths.root)
        "system.openUrl" -> openUrl(a.str("url"))
        "system.copy" -> Toolkit.getDefaultToolkit().systemClipboard.setContents(StringSelection(a.str("text")), null)
        else -> throw LauncherException("Unknown command $cmd")
    }

    private fun requireIdle() {
        if (gameState != "idle" || launcher.supervisor.anyRunning()) throw LauncherException("Stop the game first.")
    }

    private fun info(): Map<String, Any?> {
        val s = launcher.settings
        val catalog = Catalog.current
        return mapOf(
            "version" to BuildInfo.version,
            "clientVersion" to catalog.minecraft.clientVersion,
            "serverVersion" to catalog.minecraft.serverVersion,
            "modpack" to "${catalog.modpack.name} ${catalog.modpack.version}",
            "online" to online,
            "offlineReady" to runCatching { launcher.readyOffline() }.getOrDefault(false),
            "legacySupport" to Files.isRegularFile(paths.resources.resolve("mods").resolve(ContentInstaller.LEGACY_MOD)),
            "microsoftAvailable" to launcher.accounts.microsoftAvailable,
            "downloadGb" to (catalog.approxDownloadMb / 1000 + 1),
            "dataDir" to paths.root.toString(),
            "memory" to mapOf("totalMb" to SystemInfo.totalRamMb, "clientMb" to s.effectiveClientRamMb, "serverMb" to s.effectiveServerRamMb),
            "world" to runCatching { worlds.info() }.getOrNull(),
            "update" to update,
        )
    }

    private fun progress(stage: Int = -1) = Progress { fraction, message ->
        host.emit("launch.progress", mapOf("fraction" to fraction, "message" to message, "step" to stage))
    }

    @Volatile private var currentStage = 0

    private fun play(): Any? {
        if (gameState != "idle") throw LauncherException("The game is already starting.")
        val account = launcher.accounts.current() ?: throw LauncherException("Choose an account first.")
        setState("installing")
        Thread({
            val started = System.currentTimeMillis()
            try {
                val progress = Progress { fraction, message ->
                    host.emit("launch.progress", mapOf("fraction" to fraction, "message" to message, "step" to currentStage))
                }
                val client = launcher.play(account, progress) { stage ->
                    currentStage = stage
                    if (stage >= 3) setState("launching")
                    host.emit("launch.progress", mapOf("fraction" to null, "message" to "", "step" to stage))
                }
                setState("playing")
                host.gameStarted()
                val code = client.waitFor()
                Log.info(if (code == 0) "Minecraft closed." else "Minecraft exited with code $code.")
                if (code != 0) {
                    val crash = CrashReports.since(paths.gameDir, started)
                        ?: dev.wizardlauncher.core.library.CrashReport("client-output.log", "Minecraft exited with code $code.",
                            CrashReports.clientLogTail(paths.logs.resolve("client-output.log"), 120))
                    host.emit("game.crash", crash)
                }
                runCatching { launcher.server?.stop() }
                setState("idle")
                host.gameEnded()
            } catch (e: Exception) {
                val message = if (e is LauncherException) e.message else "${e.javaClass.simpleName}: ${e.message}"
                Log.error("Launch failed: $message", e)
                setState("error", message)
                setState("idle")
                host.gameEnded()
            }
        }, "play").start()
        return null
    }

    private fun setState(state: String, message: String? = null) {
        gameState = if (state == "error") "idle" else state
        host.emit("launch.state", mapOf("state" to state, "message" to message))
    }

    private fun beginMicrosoft(): Map<String, String> {
        msCancel.set(false)
        val code: MicrosoftAuth.DeviceCode = launcher.accounts.beginMicrosoftLogin()
        pool.submit {
            try {
                val account = launcher.accounts.finishMicrosoftLogin(code) { msCancel.get() }
                host.emit("accounts.msResult", mapOf("ok" to true, "name" to account.name))
            } catch (e: Exception) {
                if (!msCancel.get()) host.emit("accounts.msResult", mapOf("ok" to false, "error" to (e.message ?: "Sign-in failed")))
            }
        }
        return mapOf("code" to code.userCode, "uri" to code.verificationUri)
    }

    private fun exportPack(name: String): Any? {
        val source = packs.resolve(name) ?: throw LauncherException("That resource pack is no longer installed.")
        val target = host.chooseFiles("Export as a 1.20.1 pack", true, false, "${name.removeSuffix(".zip")} (1.20.1).zip", null, false).firstOrNull()
            ?: return null
        val rules = paths.root.resolve("wizard-states.json").takeIf(Files::isRegularFile)?.let(Files::readString)
        PackExporter.export(source, target, listOfNotNull(rules), null)
        return mapOf("path" to target.toString())
    }

    private fun openRules(): Any? {
        val file = paths.root.resolve("wizard-states.json")
        if (!Files.exists(file)) Files.writeString(file, RULES_TEMPLATE)
        return open(file)
    }

    private fun open(path: Path): Any? {
        if (!Files.exists(path)) Files.createDirectories(path)
        SwingUtilities.invokeLater { runCatching { Desktop.getDesktop().open(path.toFile()) }.onFailure { Log.info("Open it manually: $path") } }
        return null
    }

    private fun openUrl(url: String): Any? {
        val uri = URI(url)
        if (uri.scheme != "https") throw LauncherException("Only https links can be opened.")
        SwingUtilities.invokeLater { runCatching { Desktop.getDesktop().browse(uri) } }
        return null
    }

    fun media(path: String): Pair<ByteArray, String>? {
        val slash = path.indexOf('/')
        val kind = if (slash < 0) path else path.substring(0, slash)
        val name = if (slash < 0) "" else path.substring(slash + 1)
        return when (kind) {
            "hero.jpg" -> hero.image()?.let { it to "image/jpeg" }
            "logo.png" -> Bridge::class.java.getResourceAsStream("/dev/wizardlauncher/app/logo.png")?.use { it.readBytes() }?.let { it to "image/png" }
            "avatar" -> avatars.get(name)?.let { it to "image/png" }
            "modicon" -> mods.icon(name)?.let { it to "image/png" }
            "packicon" -> packs.icon(name)?.let { it to "image/png" }
            "thumb" -> shots.thumbnail(name)?.let { it to "image/jpeg" }
            "shot" -> shots.resolve(name)?.let { Files.readAllBytes(it) to "image/png" }
            else -> null
        }
    }

    companion object {
        val RULES_TEMPLATE = """
            {
              "format": 1,
              "states": {
                "minecraft:note_block": {
                  "instrument=harp,note=1,powered=false": { "model": "minecraft:block/note_block" }
                }
              },
              "items": {
                "minecraft:stick": [
                  { "predicate": { "custom_model_data": 1001 }, "model": "minecraft:item/stick" }
                ]
              },
              "split_blockstates": [],
              "rename_references": { "models": {}, "textures": {} },
              "copy_files": []
            }
        """.trimIndent() + "\n"
    }
}
