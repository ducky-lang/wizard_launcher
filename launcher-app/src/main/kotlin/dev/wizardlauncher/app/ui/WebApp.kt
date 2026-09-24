package dev.wizardlauncher.app.ui

import com.google.gson.Gson
import dev.wizardlauncher.core.BuildInfo
import dev.wizardlauncher.core.Launcher
import dev.wizardlauncher.core.Log
import dev.wizardlauncher.core.Platform
import dev.wizardlauncher.core.Settings
import me.friwi.jcefmaven.CefAppBuilder
import me.friwi.jcefmaven.EnumProgress
import me.friwi.jcefmaven.MavenCefAppHandlerAdapter
import org.cef.CefApp
import org.cef.CefClient
import org.cef.CefSettings
import org.cef.browser.CefBrowser
import org.cef.browser.CefFrame
import org.cef.browser.CefMessageRouter
import org.cef.callback.CefContextMenuParams
import org.cef.callback.CefMenuModel
import org.cef.callback.CefQueryCallback
import org.cef.handler.CefContextMenuHandlerAdapter
import org.cef.handler.CefDisplayHandlerAdapter
import org.cef.handler.CefLifeSpanHandlerAdapter
import org.cef.handler.CefMessageRouterHandlerAdapter
import org.cef.handler.CefRequestHandlerAdapter
import org.cef.network.CefRequest
import java.awt.BorderLayout
import java.awt.Color
import java.awt.Desktop
import java.awt.Dimension
import java.awt.Font
import java.awt.Image
import java.awt.MenuItem
import java.awt.PopupMenu
import java.awt.SystemTray
import java.awt.TrayIcon
import java.awt.event.WindowAdapter
import java.awt.event.WindowEvent
import java.io.File
import java.net.URI
import java.nio.file.Path
import javax.imageio.ImageIO
import javax.swing.BorderFactory
import javax.swing.JFileChooser
import javax.swing.JFrame
import javax.swing.JLabel
import javax.swing.JPanel
import javax.swing.JProgressBar
import javax.swing.JWindow
import javax.swing.SwingConstants
import javax.swing.SwingUtilities
import javax.swing.filechooser.FileNameExtensionFilter
import kotlin.system.exitProcess

class WebApp(private val launcher: Launcher) : Bridge.Host {
    private val gson = Gson()
    private lateinit var app: CefApp
    private lateinit var client: CefClient
    private var browser: CefBrowser? = null
    private lateinit var frame: JFrame
    private lateinit var bridge: Bridge
    private var tray: TrayIcon? = null
    private val logo: Image? = runCatching { ImageIO.read(WebApp::class.java.getResource("/dev/wizardlauncher/app/logo.png")) }.getOrNull()

    fun start() {
        val earlyAwt = Platform.current == Platform.WINDOWS
        val splash = if (earlyAwt) Splash(logo).also { s -> SwingUtilities.invokeAndWait { s.isVisible = true } } else null
        try {
            app = buildCef(splash)
        } finally {
            splash?.let { SwingUtilities.invokeLater { it.dispose() } }
        }
        dev.wizardlauncher.app.Theme.install()
        bridge = Bridge(launcher, this)
        SwingUtilities.invokeAndWait { createWindow() }
        bridge.start()
        Log.listenLines { line -> queueLog(line) }
    }

    private fun buildCef(splash: Splash?): CefApp {
        val builder = CefAppBuilder()
        builder.setInstallDir(launcher.paths.root.resolve("runtime").resolve("jcef-146").toFile())
        builder.setProgressHandler { state, percent ->
            val text = when (state) {
                EnumProgress.EXTRACTING -> "Preparing the launcher for first use..."
                EnumProgress.INITIALIZING -> "Starting..."
                else -> "Loading..."
            }
            if (splash != null) SwingUtilities.invokeLater { splash.update(text, if (percent >= 0) percent else -1f) } else Log.file("UI engine: $text ${percent.toInt()}%")
        }
        val settings = builder.cefSettings
        settings.windowless_rendering_enabled = false
        settings.cache_path = launcher.paths.root.resolve("cache").resolve("ui").toString()
        settings.root_cache_path = launcher.paths.root.resolve("cache").toString()
        settings.persist_session_cookies = false
        settings.log_severity = CefSettings.LogSeverity.LOGSEVERITY_WARNING
        settings.log_file = launcher.paths.logs.resolve("ui-engine.log").toString()
        settings.user_agent_product = "WizardLauncher/${BuildInfo.version}"
        settings.locale = if (launcher.settings.language == "vi") "vi" else "en-US"
        settings.background_color = settings.ColorType(255, 7, 7, 12)
        builder.addJcefArgs(*PRIVATE_ARGS)
        if (!launcher.settings.hardwareAcceleration) builder.addJcefArgs("--disable-gpu", "--disable-gpu-compositing")
        builder.setAppHandler(object : MavenCefAppHandlerAdapter() {
            override fun onContextInitialized() {
                CefApp.getInstance().registerSchemeHandlerFactory("https", WebResources.HOST, WebResources { bridge.media(it) })
            }
        })
        return builder.build()
    }

    private fun createWindow() {
        client = app.createClient()
        val router = CefMessageRouter.create()
        router.addHandler(object : CefMessageRouterHandlerAdapter() {
            override fun onQuery(browser: CefBrowser, frame: CefFrame, queryId: Long, request: String, persistent: Boolean, callback: CefQueryCallback): Boolean {
                if (!frame.url.startsWith(WebResources.ORIGIN)) {
                    callback.failure(403, "forbidden")
                    return true
                }
                bridge.handle(request, { callback.success(it) }, { callback.failure(500, it) })
                return true
            }
        }, true)
        client.addMessageRouter(router)
        client.addRequestHandler(object : CefRequestHandlerAdapter() {
            override fun onBeforeBrowse(browser: CefBrowser, frame: CefFrame, request: CefRequest, userGesture: Boolean, isRedirect: Boolean): Boolean {
                if (request.url.startsWith(WebResources.ORIGIN)) return false
                if (userGesture && request.url.startsWith("https://")) openExternal(request.url)
                return true
            }
        })
        client.addLifeSpanHandler(object : CefLifeSpanHandlerAdapter() {
            override fun onBeforePopup(browser: CefBrowser, frame: CefFrame, targetUrl: String?, targetFrameName: String?): Boolean {
                targetUrl?.takeIf { it.startsWith("https://") }?.let(::openExternal)
                return true
            }
        })
        client.addContextMenuHandler(object : CefContextMenuHandlerAdapter() {
            override fun onBeforeContextMenu(browser: CefBrowser, frame: CefFrame, params: CefContextMenuParams, model: CefMenuModel) {
                if (System.getProperty("wizard.devtools") != "true") model.clear()
            }
        })
        client.addDisplayHandler(object : CefDisplayHandlerAdapter() {
            override fun onConsoleMessage(browser: CefBrowser, level: CefSettings.LogSeverity, message: String, source: String, line: Int): Boolean {
                if (level == CefSettings.LogSeverity.LOGSEVERITY_ERROR || level == CefSettings.LogSeverity.LOGSEVERITY_WARNING) {
                    Log.file("[UI] $message ($source:$line)")
                }
                return true
            }
        })

        frame = JFrame("Wizard Launcher")
        frame.iconImage = logo
        frame.defaultCloseOperation = JFrame.DO_NOTHING_ON_CLOSE
        frame.background = Color(7, 7, 12)
        frame.contentPane.background = Color(7, 7, 12)
        frame.minimumSize = Dimension(1040, 680)
        frame.size = Dimension(1280, 800)
        frame.setLocationRelativeTo(null)
        attachBrowser()
        frame.addWindowListener(object : WindowAdapter() {
            override fun windowClosing(e: WindowEvent) = closeRequested()
        })
        frame.isVisible = true
        installTray()
    }

    private fun attachBrowser() {
        val b = client.createBrowser(WebResources.ORIGIN + "index.html", false, false)
        browser = b
        frame.contentPane.removeAll()
        frame.contentPane.add(b.uiComponent, BorderLayout.CENTER)
        frame.revalidate()
    }

    private fun releaseBrowser() {
        val b = browser ?: return
        browser = null
        frame.contentPane.removeAll()
        b.close(true)
    }

    private fun installTray() {
        if (!SystemTray.isSupported() || logo == null) return
        runCatching {
            val menu = PopupMenu()
            menu.add(MenuItem("Open Wizard Launcher").apply { addActionListener { restore() } })
            menu.addSeparator()
            menu.add(MenuItem("Quit").apply { addActionListener { quit() } })
            val icon = TrayIcon(logo!!.getScaledInstance(32, 32, Image.SCALE_SMOOTH), "Wizard Launcher", menu)
            icon.isImageAutoSize = true
            icon.addActionListener { restore() }
            SystemTray.getSystemTray().add(icon)
            tray = icon
        }.onFailure { Log.file("System tray unavailable: ${it.message}") }
    }

    private fun restore() = SwingUtilities.invokeLater {
        if (browser == null) attachBrowser()
        frame.isVisible = true
        frame.extendedState = JFrame.NORMAL
        frame.toFront()
    }

    override fun gameStarted() {
        when (launcher.settings.afterLaunch) {
            Settings.AfterLaunch.CLOSE -> SwingUtilities.invokeLater { shutdown(keepGame = true) }
            Settings.AfterLaunch.MINIMIZE -> SwingUtilities.invokeLater {
                if (tray != null) {
                    frame.isVisible = false
                    releaseBrowser()
                    tray?.displayMessage("Wizard Launcher", "Enjoy the castle! The launcher is waiting in the tray.", TrayIcon.MessageType.NONE)
                } else {
                    frame.extendedState = JFrame.ICONIFIED
                }
            }
            Settings.AfterLaunch.KEEP_OPEN -> Unit
        }
    }

    override fun gameEnded() {
        if (!frame.isVisible || browser == null) restore()
    }

    private fun closeRequested() {
        if (launcher.supervisor.isRunning("client")) {
            if (tray != null) {
                frame.isVisible = false
                releaseBrowser()
                return
            }
            shutdown(keepGame = true)
            return
        }
        shutdown(keepGame = false)
    }

    override fun quit() = SwingUtilities.invokeLater { shutdown(keepGame = launcher.supervisor.isRunning("client")) }

    private fun shutdown(keepGame: Boolean) {
        frame.isVisible = false
        Thread({
            if (!keepGame && launcher.supervisor.anyRunning()) runCatching { launcher.stop() }
            if (keepGame) Log.info("Launcher closed while playing; the world stops by itself when Minecraft does.")
            SwingUtilities.invokeLater {
                runCatching { tray?.let { SystemTray.getSystemTray().remove(it) } }
                runCatching { browser?.close(true); client.dispose(); app.dispose() }
                frame.dispose()
                exitProcess(0)
            }
        }, "shutdown").start()
    }

    private val pendingLogs = ArrayList<String>()
    private var flushScheduled = false

    private fun queueLog(line: String) {
        synchronized(pendingLogs) {
            pendingLogs += line
            if (flushScheduled) return
            flushScheduled = true
        }
        javax.swing.Timer(150) {
            val batch = synchronized(pendingLogs) {
                flushScheduled = false
                pendingLogs.toList().also { pendingLogs.clear() }
            }
            if (batch.isNotEmpty()) emit("logs", mapOf("lines" to batch.takeLast(300)))
        }.apply { isRepeats = false; start() }
    }

    override fun emit(event: String, data: Any?) {
        val b = browser ?: return
        val script = "window.__wl_emit && window.__wl_emit(${gson.toJson(event)}, ${gson.toJson(data)});"
        SwingUtilities.invokeLater { runCatching { b.executeJavaScript(script, WebResources.ORIGIN, 0) } }
    }

    override fun chooseFiles(title: String, save: Boolean, dirs: Boolean, suggested: String?, filter: FileNameExtensionFilter?, multi: Boolean): List<Path> {
        var result: List<Path> = emptyList()
        SwingUtilities.invokeAndWait {
            val chooser = JFileChooser().apply {
                dialogTitle = title
                fileSelectionMode = if (dirs) JFileChooser.FILES_AND_DIRECTORIES else JFileChooser.FILES_ONLY
                isMultiSelectionEnabled = multi && !save
                filter?.let { fileFilter = it }
                suggested?.let { selectedFile = File(it) }
            }
            val answer = if (save) chooser.showSaveDialog(frame) else chooser.showOpenDialog(frame)
            if (answer == JFileChooser.APPROVE_OPTION) {
                result = if (chooser.isMultiSelectionEnabled) chooser.selectedFiles.map { it.toPath() } else listOf(chooser.selectedFile.toPath())
            }
        }
        return result
    }

    private fun openExternal(url: String) {
        SwingUtilities.invokeLater { runCatching { Desktop.getDesktop().browse(URI(url)) } }
    }

    companion object {
        val PRIVATE_ARGS = arrayOf(
            "--no-proxy-server",
            "--host-resolver-rules=MAP * ~NOTFOUND",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-domain-reliability",
            "--disable-sync",
            "--disable-default-apps",
            "--disable-extensions",
            "--disable-client-side-phishing-detection",
            "--disable-breakpad",
            "--disable-spell-checking",
            "--no-default-browser-check",
            "--no-first-run",
            "--no-pings",
            "--dns-prefetch-disable",
            "--metrics-recording-only",
            "--safebrowsing-disable-auto-update",
            "--disable-field-trial-config",
            "--disable-features=Translate,MediaRouter,OptimizationHints,OptimizationHintsFetching,OptimizationGuideModelDownloading," +
                "OptimizationTargetPrediction,AutofillServerCommunication,CertificateTransparencyComponentUpdater,NetworkTimeServiceQuerying," +
                "SafeBrowsing,InterestFeedContentSuggestions,PrivacySandboxSettings4,SpareRendererForSitePerProcess,DialMediaRouteProvider",
        )
    }

    private class Splash(logo: Image?) : JWindow() {
        private val label = JLabel("Starting...", SwingConstants.CENTER)
        private val bar = JProgressBar(0, 100)

        init {
            val panel = JPanel(BorderLayout(0, 14))
            panel.background = Color(11, 11, 20)
            panel.border = BorderFactory.createCompoundBorder(
                BorderFactory.createLineBorder(Color(242, 193, 78, 90)), BorderFactory.createEmptyBorder(28, 34, 24, 34))
            logo?.let { panel.add(JLabel(javax.swing.ImageIcon(it.getScaledInstance(72, 72, Image.SCALE_SMOOTH))), BorderLayout.NORTH) }
            label.foreground = Color(181, 179, 201)
            label.font = label.font.deriveFont(Font.PLAIN, 13f)
            panel.add(label, BorderLayout.CENTER)
            bar.isIndeterminate = true
            bar.preferredSize = Dimension(260, 6)
            bar.foreground = Color(242, 193, 78)
            bar.background = Color(30, 29, 48)
            bar.isBorderPainted = false
            panel.add(bar, BorderLayout.SOUTH)
            contentPane = panel
            pack()
            setLocationRelativeTo(null)
        }

        fun update(text: String, percent: Float) {
            label.text = text
            bar.isIndeterminate = percent < 0
            if (percent >= 0) bar.value = percent.toInt()
        }
    }
}
