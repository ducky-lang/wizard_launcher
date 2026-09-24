package dev.wizardlauncher.app

import dev.wizardlauncher.core.AppPaths
import dev.wizardlauncher.app.ui.WebApp
import dev.wizardlauncher.core.BuildInfo
import dev.wizardlauncher.core.Launcher
import dev.wizardlauncher.core.LauncherException
import dev.wizardlauncher.core.auth.Account
import dev.wizardlauncher.core.install.OfflineBundle
import dev.wizardlauncher.core.install.Progress
import dev.wizardlauncher.legacy.PackExporter
import java.nio.channels.FileChannel
import java.nio.channels.FileLock
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardOpenOption
import java.security.KeyPairGenerator
import java.security.KeyFactory
import java.security.Signature
import java.security.spec.PKCS8EncodedKeySpec
import java.util.Base64
import javax.swing.JOptionPane
import javax.swing.SwingUtilities
import kotlin.system.exitProcess

fun main(args: Array<String>) {
    try {
        if (args.isNotEmpty() && runCli(args)) return
    } catch (e: LauncherException) {
        System.err.println(e.message)
        exitProcess(1)
    }
    val paths = AppPaths.default().ensure()
    val lock = SingleInstance.acquire(paths.root)
    if (lock == null) {
        JOptionPane.showMessageDialog(null, "Wizard Launcher is already running.\nCheck your taskbar - the window may be minimised.",
            "Wizard Launcher", JOptionPane.INFORMATION_MESSAGE)
        return
    }
    val launcher = try {
        Launcher(paths)
    } catch (t: Throwable) {
        JOptionPane.showMessageDialog(null, "Wizard Launcher could not start:\n${t.message}", "Wizard Launcher", JOptionPane.ERROR_MESSAGE)
        exitProcess(1)
    }
    Thread.setDefaultUncaughtExceptionHandler { thread, e ->
        dev.wizardlauncher.core.Log.error("Unhandled error in ${thread.name}: ${e.message}", e)
    }
    if ("--classic" !in args && System.getProperty("wizard.classicUi") != "true") {
        try {
            WebApp(launcher).start()
            return
        } catch (t: Throwable) {
            dev.wizardlauncher.core.Log.error("The web interface could not start (${t.message}); using the classic window.", t)
        }
    }
    Theme.install()
    SwingUtilities.invokeLater { MainWindow(launcher).isVisible = true }
}

private fun runCli(args: Array<String>): Boolean {
    val progress = Progress { f, m -> println(if (f != null) "[%3d%%] %s".format((f * 100).toInt(), m) else "[....] $m") }
    when (args[0]) {
        "--version" -> println("Wizard Launcher ${BuildInfo.version}")
        "--help" -> println(HELP)
        "--convert-pack", "--export-pack" -> {
            require(args.size >= 3) { HELP }
            val rules = args.drop(3).windowed(2, 2).filter { it[0] == "--rules" }.map { Files.readString(Path.of(it[1])) }
            val overlay = PackExporter.export(Path.of(args[1]), Path.of(args[2]), rules, null)
            println(overlay.report())
        }
        "--verify-install" -> println(Launcher().verifyInstall(progress))
        "--smoke-client" -> {
            val timeout = args.getOrNull(args.indexOf("--timeout") + 1)?.takeIf { "--timeout" in args }?.toLong() ?: 240
            val pack = args.getOrNull(args.indexOf("--pack") + 1)?.takeIf { "--pack" in args }?.let(Path::of)
            val report = Launcher().smokeClient(progress, timeout, pack)
            println(report)
            if (report.lines().any { it.startsWith("[--]") }) exitProcess(3)
        }
        "--world-selftest" -> println(Launcher().selfTestWorld(progress))
        "--export-bundle" -> OfflineBundle.export(AppPaths.default().ensure(), Path.of(args[1]), progress)
        "--import-bundle" -> OfflineBundle.import(AppPaths.default().ensure(), Path.of(args[1]), progress)
        "--play" -> {
            val launcher = Launcher()
            val name = args.getOrNull(args.indexOf("--name") + 1)?.takeIf { "--name" in args }
            val account = name?.let(Account::offline) ?: launcher.accounts.current()
                ?: throw LauncherException("No account: pass --name <player> or sign in from the launcher window once.")
            val client = launcher.play(account, progress)
            client.waitFor()
        }
        "--catalog-keygen" -> {
            val kp = KeyPairGenerator.getInstance("Ed25519").generateKeyPair()
            println("public_key (put in catalog.json signing.public_key):\n" + Base64.getEncoder().encodeToString(kp.public.encoded))
            println("\nprivate key (keep secret, never commit):\n" + Base64.getEncoder().encodeToString(kp.private.encoded))
        }
        "--catalog-sign" -> {
            val key = System.getenv("WIZARD_CATALOG_KEY") ?: throw LauncherException("Set WIZARD_CATALOG_KEY to the base64 private key.")
            val file = Path.of(args[1])
            val sig = Signature.getInstance("Ed25519").run {
                initSign(KeyFactory.getInstance("Ed25519").generatePrivate(PKCS8EncodedKeySpec(Base64.getDecoder().decode(key))))
                update(Files.readAllBytes(file)); sign()
            }
            Files.writeString(file.resolveSibling(file.fileName.toString() + ".sig"), Base64.getEncoder().encodeToString(sig))
            println("Wrote ${file.fileName}.sig")
        }
        else -> return false
    }
    return true
}

private val HELP = """
    Wizard Launcher ${BuildInfo.version}
      (no arguments)                         open the launcher
      --play [--name <player>]               install if needed and play, no window
      --export-pack <in> <out.zip> [--rules states.json]...
                                             write a 1.20.1-format copy of an older pack
      --verify-install                       install or repair the game and check every file
      --smoke-client [--pack p.zip] [--timeout s]   start Minecraft briefly and check it loads
      --world-selftest                       start the world + bridge, ping it as 1.20.1, stop (offline)
      --export-bundle <file.wizardpack>      pack this install for an offline computer
      --import-bundle <file.wizardpack>      install from a bundle, no internet needed
      --catalog-keygen / --catalog-sign <catalog.json>   maintainer tools
""".trimIndent()

object SingleInstance {
    private var channel: FileChannel? = null
    private var lock: FileLock? = null

    fun acquire(root: Path): FileLock? {
        val ch = FileChannel.open(root.resolve("WizardLauncher.lock"), StandardOpenOption.CREATE, StandardOpenOption.WRITE)
        val l = runCatching { ch.tryLock() }.getOrNull() ?: run { ch.close(); return null }
        channel = ch; lock = l
        return l
    }
}
