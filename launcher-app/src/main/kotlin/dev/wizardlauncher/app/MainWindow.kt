package dev.wizardlauncher.app

import dev.wizardlauncher.core.BuildInfo
import dev.wizardlauncher.core.Launcher
import dev.wizardlauncher.core.LauncherException
import dev.wizardlauncher.core.Log
import dev.wizardlauncher.core.Settings
import dev.wizardlauncher.core.auth.Account
import dev.wizardlauncher.core.install.OfflineBundle
import dev.wizardlauncher.core.install.Progress
import dev.wizardlauncher.legacy.LegacyTranslator
import dev.wizardlauncher.legacy.PackExporter
import java.awt.BorderLayout
import java.awt.Cursor
import java.awt.Desktop
import java.awt.Dimension
import java.awt.FlowLayout
import java.awt.Font
import java.awt.GradientPaint
import java.awt.Graphics
import java.awt.Graphics2D
import java.awt.RenderingHints
import java.awt.event.WindowAdapter
import java.awt.event.WindowEvent
import java.io.File
import java.nio.file.Path
import javax.imageio.ImageIO
import javax.swing.BorderFactory
import javax.swing.Box
import javax.swing.BoxLayout
import javax.swing.ImageIcon
import javax.swing.JButton
import javax.swing.JComboBox
import javax.swing.JComponent
import javax.swing.JFileChooser
import javax.swing.JFrame
import javax.swing.JLabel
import javax.swing.JMenu
import javax.swing.JMenuBar
import javax.swing.JMenuItem
import javax.swing.JOptionPane
import javax.swing.JPanel
import javax.swing.JProgressBar
import javax.swing.JScrollPane
import javax.swing.JTextArea
import javax.swing.SwingConstants
import javax.swing.SwingUtilities
import javax.swing.filechooser.FileNameExtensionFilter
import kotlin.concurrent.thread

class MainWindow(private val launcher: Launcher) : JFrame("Wizard Launcher") {
    private val status = JLabel("Ready when you are.").apply { foreground = Theme.textSub }
    private val progress = JProgressBar(0, 1000).apply { isVisible = false; preferredSize = Dimension(10, 8) }
    private val accountLabel = JLabel().apply { font = font.deriveFont(Font.BOLD, 15f) }
    private val accountMode = JLabel().apply { foreground = Theme.textSub }
    private val play = JButton("PLAY").apply {
        font = Theme.title(22f)
        foreground = Theme.bg
        background = Theme.gold
        preferredSize = Dimension(260, 58)
        cursor = Cursor.getPredefinedCursor(Cursor.HAND_CURSOR)
        putClientProperty("JButton.buttonType", "roundRect")
    }
    private val stop = JButton("Stop").apply { isEnabled = false }
    private val instancePicker = JComboBox<String>().apply { preferredSize = Dimension(240, 36) }
    private var instanceIds = emptyList<String>()
    private val logArea = JTextArea(8, 60).apply {
        isEditable = false; lineWrap = true; wrapStyleWord = true
        foreground = Theme.textSub; font = Font(Font.MONOSPACED, Font.PLAIN, 12)
        border = BorderFactory.createEmptyBorder(8, 10, 8, 10)
    }
    private val signIn = JButton("Sign in with Microsoft")
    private val offlineButton = JButton("Play with a name")
    private val signOut = JButton("Sign out")
    private val offlineBadge = JLabel().apply { foreground = Theme.arcane }

    @Volatile private var busy = false

    init {
        defaultCloseOperation = DO_NOTHING_ON_CLOSE
        minimumSize = Dimension(820, 600)
        setLocationRelativeTo(null)
        runCatching { iconImage = ImageIO.read(MainWindow::class.java.getResource("logo.png")) }
        jMenuBar = menu()
        contentPane = Backdrop().apply {
            layout = BorderLayout()
            border = BorderFactory.createEmptyBorder(20, 28, 20, 28)
            add(header(), BorderLayout.NORTH)
            add(center(), BorderLayout.CENTER)
            add(logPanel(), BorderLayout.SOUTH)
        }
        pack()
        setSize(900, 660)
        setLocationRelativeTo(null)

        Log.listen { line -> SwingUtilities.invokeLater { logArea.append(line + "\n"); logArea.caretPosition = logArea.document.length } }
        play.addActionListener { onPlay() }
        stop.addActionListener { onStop() }
        signIn.addActionListener { MicrosoftLoginDialog(this, launcher) { refreshAccount() }.isVisible = true }
        offlineButton.addActionListener { askOfflineName() }
        signOut.addActionListener { launcher.accounts.signOut(); refreshAccount() }
        addWindowListener(object : WindowAdapter() { override fun windowClosing(e: WindowEvent) = onClose() })
        instancePicker.addActionListener { onInstancePicked() }
        refreshAccount()
        refreshInstances()
    }

    private fun refreshInstances() {
        val all = launcher.instances.all()
        val selected = launcher.instances.selected().id
        instanceIds = all.map { it.id }
        instancePicker.removeAllItems()
        all.forEach { instancePicker.addItem("${it.name}  ·  Minecraft ${it.minecraft}") }
        instancePicker.selectedIndex = instanceIds.indexOf(selected).coerceAtLeast(0)
        checkReady()
    }

    private fun onInstancePicked() {
        val id = instanceIds.getOrNull(instancePicker.selectedIndex) ?: return
        if (id == launcher.instances.selected().id) return
        if (busy || launcher.supervisor.anyRunning()) {
            refreshInstances()
            return
        }
        launcher.instances.select(id)
        checkReady()
    }

    private fun checkReady() {
        thread(isDaemon = true, name = "startup-check") {
            val ready = runCatching { launcher.readyOffline() }.getOrDefault(false)
            SwingUtilities.invokeLater {
                status.text = if (ready) "Installed - plays with or without internet." else
                    "First launch downloads the castle, the game and the mods (about ${dev.wizardlauncher.core.Catalog.current.approxDownloadMb / 1000 + 1} GB)."
            }
        }
    }

    private fun importModpack() {
        val file = choose("Choose a Modrinth modpack (.mrpack)", open = true, filter = FileNameExtensionFilter("Modrinth modpack", "mrpack")) ?: return
        background("Importing modpack...") {
            val added = launcher.instances.importModpack(file)
            launcher.instances.select(added.id)
            ui { refreshInstances() }
        }
    }

    private fun header(): JComponent = JPanel(BorderLayout()).apply {
        isOpaque = false
        val logo = runCatching {
            val source = ImageIO.read(MainWindow::class.java.getResource("logo.png"))
            val scaled = java.awt.image.BufferedImage(72, 72, java.awt.image.BufferedImage.TYPE_INT_ARGB)
            scaled.createGraphics().apply {
                setRenderingHint(RenderingHints.KEY_INTERPOLATION, RenderingHints.VALUE_INTERPOLATION_BICUBIC)
                clip = java.awt.geom.RoundRectangle2D.Float(0f, 0f, 72f, 72f, 18f, 18f)
                drawImage(source, 0, 0, 72, 72, null)
                dispose()
            }
            ImageIcon(scaled)
        }.getOrNull()
        add(JLabel(logo), BorderLayout.WEST)
        add(JPanel().apply {
            isOpaque = false
            layout = BoxLayout(this, BoxLayout.Y_AXIS)
            border = BorderFactory.createEmptyBorder(4, 14, 0, 0)
            add(JLabel("Witchcraft & Wizardry").apply { font = Theme.title(30f); foreground = Theme.gold })
            add(JLabel("A castle, rebuilt block by block  ·  1.16.5 world on a 1.20.1 or 1.21.1 client").apply { foreground = Theme.textSub })
        }, BorderLayout.CENTER)
        add(JPanel().apply {
            isOpaque = false
            layout = BoxLayout(this, BoxLayout.Y_AXIS)
            add(accountLabel.apply { alignmentX = RIGHT_ALIGNMENT; horizontalAlignment = SwingConstants.RIGHT })
            add(accountMode.apply { alignmentX = RIGHT_ALIGNMENT })
            add(offlineBadge.apply { alignmentX = RIGHT_ALIGNMENT })
        }, BorderLayout.EAST)
    }

    private fun center(): JComponent = JPanel().apply {
        isOpaque = false
        layout = BoxLayout(this, BoxLayout.Y_AXIS)
        add(Box.createVerticalGlue())
        add(JPanel(FlowLayout(FlowLayout.CENTER, 10, 0)).apply { isOpaque = false; add(play); add(instancePicker) })
        add(Box.createVerticalStrut(14))
        add(JPanel(FlowLayout(FlowLayout.CENTER, 8, 0)).apply { isOpaque = false; add(stop); add(signIn); add(offlineButton); add(signOut) })
        add(Box.createVerticalStrut(18))
        add(progress)
        add(Box.createVerticalStrut(6))
        add(JPanel(FlowLayout(FlowLayout.CENTER)).apply { isOpaque = false; add(status) })
        add(Box.createVerticalGlue())
    }

    private fun logPanel(): JComponent = JScrollPane(logArea).apply {
        border = BorderFactory.createLineBorder(Theme.border)
        preferredSize = Dimension(10, 170)
    }

    private fun menu() = JMenuBar().apply {
        add(JMenu("Game").apply {
            item("Play") { onPlay() }
            item("Stop") { onStop() }
            addSeparator()
            item("Back up the world now") { background("Backing up...") { launcher.backupWorld()?.let { Log.info("World backed up to $it") } } }
            item("Reset the world (keeps a backup)...") { resetWorld() }
            addSeparator()
            item("Import a modpack (.mrpack)...") { importModpack() }
            item("Open game folder") { open(launcher.paths.gameDir) }
            item("Open world backups") { open(launcher.paths.backups) }
        })
        add(JMenu("Tools").apply {
            item("Convert a 1.16.5 resource pack for this version...") { convertPack() }
            item("Edit block state rules (wizard-states.json)...") { editStateRules() }
            addSeparator()
            item("Export offline bundle...") { exportBundle() }
            item("Import offline bundle...") { importBundle() }
            addSeparator()
            item("Test the world server") {
                background("Testing the world server...") {
                    val report = launcher.selfTestWorld(uiProgress())
                    Log.info(report)
                    ui { JOptionPane.showMessageDialog(this@MainWindow, report, "World server", JOptionPane.INFORMATION_MESSAGE) }
                }
            }
            item("Open logs") { open(launcher.paths.logs) }
        })
        add(JMenu("Settings").apply { item("Settings...") { SettingsDialog(this@MainWindow, launcher.settings) { refreshAccount() }.isVisible = true } })
        add(JMenu("Help").apply {
            item("About") {
                JOptionPane.showMessageDialog(this@MainWindow,
                    "Wizard Launcher ${BuildInfo.version}\n\n" +
                        "Map: Witchcraft and Wizardry by The Floo Network.\n" +
                        "World: Minecraft 1.16.5, bridged by ViaProxy (same JVM).\n" +
                        "Client: Minecraft 1.20.1 or 1.21.1 + Fabulously Optimized.\n\n" +
                        "Data folder:\n${launcher.paths.root}", "About", JOptionPane.INFORMATION_MESSAGE)
            }
        })
    }

    private fun JMenu.item(text: String, action: () -> Unit) = add(JMenuItem(text).apply { addActionListener { action() } })

    private fun refreshAccount() {
        val profile = launcher.accounts.cachedProfile()
        val offlineName = launcher.accounts.selected()?.takeIf { !it.isMicrosoft }?.name ?: ""
        when {
            profile != null -> { accountLabel.text = profile.first; accountMode.text = "Microsoft account" }
            offlineName.isNotBlank() -> { accountLabel.text = offlineName; accountMode.text = "Offline name" }
            else -> { accountLabel.text = "Not signed in"; accountMode.text = "Sign in or choose a name" }
        }
        signIn.isVisible = profile == null && launcher.accounts.microsoftAvailable
        offlineButton.isVisible = profile == null
        signOut.isVisible = profile != null || offlineName.isNotBlank()
        offlineBadge.text = if (launcher.settings.offlineOnly) "Offline mode" else ""
    }

    private fun askOfflineName(): Boolean {
        val name = JOptionPane.showInputDialog(this, "Your name in the castle (3-16 letters, digits or _):",
            launcher.settings.offlineName)?.trim() ?: return false
        if (!Settings.validName(name)) {
            JOptionPane.showMessageDialog(this, "That name cannot be used in Minecraft.", "Name", JOptionPane.WARNING_MESSAGE)
            return false
        }
        launcher.accounts.addOffline(name)
        refreshAccount()
        return true
    }

    private fun onPlay() {
        if (busy) return
        busy = true
        play.isEnabled = false
        instancePicker.isEnabled = false
        stop.isEnabled = true
        progress.isVisible = true
        status.text = "Checking your account..."
        thread(name = "play") {
            var account = runCatching { launcher.accounts.current() }.getOrNull()
            if (account == null) {
                var named = false
                SwingUtilities.invokeAndWait { named = askOfflineName() }
                account = if (named) launcher.accounts.current() else null
            }
            if (account == null) {
                busy = false
                ui { play.isEnabled = true; instancePicker.isEnabled = true; stop.isEnabled = false; progress.isVisible = false; status.text = "Ready when you are." }
                return@thread
            }
            try {
                val client = launcher.play(account, uiProgress())
                if (launcher.settings.closeLauncherOnPlay) {
                    SwingUtilities.invokeLater { dispose(); System.exit(0) }
                    return@thread
                }
                ui { status.text = "Playing. The world saves and stops by itself when you quit Minecraft." ; progress.isVisible = false }
                val code = client.waitFor()
                Log.info(if (code == 0) "Minecraft closed." else "Minecraft exited with code $code - see logs/client-output.log.")
                launcher.server?.stop()
            } catch (e: LauncherException) {
                Log.info(e.message ?: "Launch failed.")
                ui { JOptionPane.showMessageDialog(this, e.message, "Could not start", JOptionPane.ERROR_MESSAGE) }
            } catch (e: Exception) {
                Log.error("Unexpected error: ${e.message}", e)
                ui { JOptionPane.showMessageDialog(this, "Something went wrong: ${e.message}\n\nDetails are in the log folder.", "Error", JOptionPane.ERROR_MESSAGE) }
            } finally {
                busy = false
                ui { play.isEnabled = true; instancePicker.isEnabled = true; stop.isEnabled = false; progress.isVisible = false; status.text = "Ready when you are." }
            }
        }
    }

    private fun onStop() {
        stop.isEnabled = false
        background("Stopping...") { launcher.stop { msg -> ui { status.text = msg } } }
    }

    private fun onClose() {
        if (launcher.supervisor.isRunning("client")) {
            Log.info("Launcher closed while playing; the world will stop when Minecraft does.")
            dispose(); System.exit(0)
        }
        if (launcher.supervisor.anyRunning()) {
            background("Saving the world...") { launcher.stop() }.join()
        }
        dispose()
        System.exit(0)
    }

    private fun resetWorld() {
        val ok = JOptionPane.showConfirmDialog(this,
            "This puts the castle back exactly as it ships.\nYour current world is copied to the backups folder first.",
            "Reset the world", JOptionPane.OK_CANCEL_OPTION, JOptionPane.WARNING_MESSAGE)
        if (ok == JOptionPane.OK_OPTION) background("Resetting the world...") { launcher.resetWorld(uiProgress()) }
    }

    private fun convertPack() {
        val input = choose("Choose a 1.16.5 resource pack (.zip or folder)", open = true, dirs = true) ?: return
        val instance = launcher.instances.selected()
        val format = instance.packFormat ?: LegacyTranslator.TARGET_FORMAT
        val mc = if (instance.packFormat != null) instance.minecraft else "1.20.1"
        val output = choose("Save the $mc pack as", open = false, suggested = input.fileName.toString().removeSuffix(".zip") + " ($mc).zip") ?: return
        background("Converting resource pack...") {
            val rules = launcher.paths.root.resolve("wizard-states.json").takeIf { java.nio.file.Files.isRegularFile(it) }
            val overlay = PackExporter.export(input, output, listOfNotNull(rules?.let { java.nio.file.Files.readString(it) }), null, format)
            Log.info("Exported -> $output (${overlay.files().size} files adapted, ${overlay.warnings().size} note(s)).")
            ui { ReportDialog(this, overlay.report()).isVisible = true }
        }
    }

    private fun editStateRules() {
        val file = launcher.paths.root.resolve("wizard-states.json")
        if (!java.nio.file.Files.exists(file)) {
            java.nio.file.Files.writeString(file, STATE_RULES_TEMPLATE)
        }
        open(file)
        Log.info("State rules: $file - applied the next time the resource pack is installed or converted.")
    }

    private fun exportBundle() {
        val target = choose("Save offline bundle", open = false, suggested = "WizardLauncher-offline.wizardpack") ?: return
        background("Exporting...") { OfflineBundle.export(launcher.paths, target, uiProgress()) }
    }

    private fun importBundle() {
        val source = choose("Choose an offline bundle", open = true, filter = FileNameExtensionFilter("Offline bundle", "wizardpack")) ?: return
        background("Importing...") { OfflineBundle.import(launcher.paths, source, uiProgress()) }
    }

    private fun uiProgress() = Progress { fraction, message ->
        ui {
            progress.isVisible = true
            progress.isIndeterminate = fraction == null
            fraction?.let { progress.value = (it * 1000).toInt() }
            status.text = message
        }
    }

    private fun background(message: String, playing: Boolean = false, work: () -> Unit): Thread {
        if (!playing) status.text = message
        return thread(name = "task") {
            try { work() } catch (e: Exception) {
                Log.error(e.message ?: "Failed", e)
                ui { JOptionPane.showMessageDialog(this, e.message, "Wizard Launcher", JOptionPane.ERROR_MESSAGE) }
            } finally {
                if (!playing) ui { if (!busy) { status.text = "Ready when you are."; progress.isVisible = false } }
            }
        }
    }

    private fun ui(block: () -> Unit) = SwingUtilities.invokeLater(block)

    private fun open(path: Path) = runCatching {
        java.nio.file.Files.createDirectories(if (java.nio.file.Files.isDirectory(path) || !path.toString().contains('.')) path else path.parent)
        Desktop.getDesktop().open(path.toFile())
    }.onFailure { Log.info("Open it manually: $path") }

    private fun choose(title: String, open: Boolean, dirs: Boolean = false, suggested: String? = null,
                       filter: FileNameExtensionFilter? = null): Path? {
        val chooser = JFileChooser().apply {
            dialogTitle = title
            fileSelectionMode = if (dirs) JFileChooser.FILES_AND_DIRECTORIES else JFileChooser.FILES_ONLY
            filter?.let { fileFilter = it }
            suggested?.let { selectedFile = File(it) }
        }
        val result = if (open) chooser.showOpenDialog(this) else chooser.showSaveDialog(this)
        return if (result == JFileChooser.APPROVE_OPTION) chooser.selectedFile.toPath() else null
    }

    private class Backdrop : JPanel() {
        override fun paintComponent(g: Graphics) {
            val g2 = g as Graphics2D
            g2.setRenderingHint(RenderingHints.KEY_RENDERING, RenderingHints.VALUE_RENDER_QUALITY)
            g2.paint = GradientPaint(0f, 0f, java.awt.Color(0x0b0a14), 0f, height.toFloat(), java.awt.Color(0x0d0a06))
            g2.fillRect(0, 0, width, height)
        }
    }

    companion object {
        val STATE_RULES_TEMPLATE = """
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
