package dev.wizardlauncher.app

import dev.wizardlauncher.core.Launcher
import dev.wizardlauncher.core.Log
import dev.wizardlauncher.core.Settings
import dev.wizardlauncher.core.SystemInfo
import java.awt.BorderLayout
import java.awt.Desktop
import java.awt.Dimension
import java.awt.FlowLayout
import java.awt.Font
import java.awt.GridBagConstraints
import java.awt.GridBagLayout
import java.awt.Insets
import java.awt.Toolkit
import java.awt.datatransfer.StringSelection
import java.net.URI
import javax.swing.BorderFactory
import javax.swing.JButton
import javax.swing.JCheckBox
import javax.swing.JComboBox
import javax.swing.JDialog
import javax.swing.JFrame
import javax.swing.JLabel
import javax.swing.JPanel
import javax.swing.JScrollPane
import javax.swing.JSpinner
import javax.swing.JTextArea
import javax.swing.JTextField
import javax.swing.SpinnerNumberModel
import javax.swing.SwingUtilities
import kotlin.concurrent.thread

class SettingsDialog(owner: JFrame, private val settings: Settings, private val onSaved: () -> Unit) : JDialog(owner, "Settings", true) {
    private val profile = JComboBox(arrayOf("Low memory", "Balanced", "High quality"))
    private val serverRam = JSpinner(SpinnerNumberModel(settings.serverRamMb, 0, 32768, 256))
    private val clientRam = JSpinner(SpinnerNumberModel(settings.clientRamMb, 0, 32768, 256))
    private val lan = JCheckBox("Let devices on my network join (LAN)", settings.allowLan)
    private val offline = JCheckBox("Offline mode - never use the internet", settings.offlineOnly)
    private val restart = JCheckBox("Restart the world automatically if it crashes", settings.autoRestartServer)
    private val convert = JCheckBox("Convert the 1.16.5 resource pack for 1.20.1 (recommended)", settings.convertResourcePack)
    private val closeOnPlay = JCheckBox("Close the launcher when the game starts", settings.closeLauncherOnPlay)
    private val javaPath = JTextField(settings.javaPath, 28)

    init {
        profile.selectedIndex = settings.memoryProfile.ordinal
        val form = JPanel(GridBagLayout()).apply { border = BorderFactory.createEmptyBorder(16, 18, 8, 18) }
        var row = 0
        fun add(label: String?, component: java.awt.Component, hint: String? = null) {
            val c = GridBagConstraints().apply { gridy = row++; insets = Insets(4, 4, 4, 4); anchor = GridBagConstraints.WEST }
            if (label != null) form.add(JLabel(label), c.apply { gridx = 0 })
            form.add(component, c.apply { gridx = if (label == null) 0 else 1; gridwidth = if (label == null) 2 else 1 })
            hint?.let { form.add(JLabel(it).apply { foreground = Theme.textFaint; font = font.deriveFont(11f) },
                GridBagConstraints().apply { gridy = row++; gridx = 1; anchor = GridBagConstraints.WEST; insets = Insets(0, 4, 6, 4) }) }
        }
        add("Memory profile", profile, "This computer has ${SystemInfo.totalRamMb / 1024} GB. Low memory also shortens the world's view distance.")
        add("World memory (MB)", serverRam, "0 = automatic (now ${settings.effectiveServerRamMb} MB)")
        add("Game memory (MB)", clientRam, "0 = automatic (now ${settings.effectiveClientRamMb} MB)")
        add("Java (optional)", javaPath, "Empty = the Java 17 bundled with the launcher")
        listOf(offline, lan, restart, convert, closeOnPlay).forEach { add(null, it) }

        val save = JButton("Save").apply { addActionListener { save() } }
        val cancel = JButton("Cancel").apply { addActionListener { dispose() } }
        contentPane = JPanel(BorderLayout()).apply {
            add(form, BorderLayout.CENTER)
            add(JPanel(FlowLayout(FlowLayout.RIGHT)).apply { add(cancel); add(save) }, BorderLayout.SOUTH)
        }
        rootPane.defaultButton = save
        pack()
        setLocationRelativeTo(owner)
    }

    private fun save() {
        settings.memoryProfile = Settings.MemoryProfile.entries[profile.selectedIndex]
        settings.serverRamMb = (serverRam.value as Int).let { if (it <= 0) 0 else it.coerceIn(512, 32768) }
        settings.clientRamMb = (clientRam.value as Int).let { if (it <= 0) 0 else it.coerceIn(512, 32768) }
        settings.allowLan = lan.isSelected
        settings.offlineOnly = offline.isSelected
        settings.autoRestartServer = restart.isSelected
        settings.convertResourcePack = convert.isSelected
        settings.closeLauncherOnPlay = closeOnPlay.isSelected
        settings.javaPath = javaPath.text.trim()
        settings.save()
        if (settings.allowLan) Log.info("LAN play is ON - other devices on your network can join the world.")
        onSaved()
        dispose()
    }
}

class MicrosoftLoginDialog(owner: JFrame, private val launcher: Launcher, private val onDone: () -> Unit) : JDialog(owner, "Sign in with Microsoft", true) {
    @Volatile private var cancelled = false
    private val code = JLabel("…").apply { font = Font(Font.MONOSPACED, Font.BOLD, 30); foreground = Theme.gold }
    private val info = JLabel("Contacting Microsoft...")

    init {
        val copy = JButton("Copy code & open page")
        val cancel = JButton("Cancel").apply { addActionListener { cancelled = true; dispose() } }
        contentPane = JPanel(BorderLayout(0, 12)).apply {
            border = BorderFactory.createEmptyBorder(18, 22, 14, 22)
            add(info, BorderLayout.NORTH)
            add(JPanel(FlowLayout(FlowLayout.CENTER)).apply { add(code) }, BorderLayout.CENTER)
            add(JPanel(FlowLayout(FlowLayout.RIGHT)).apply { add(cancel); add(copy) }, BorderLayout.SOUTH)
        }
        preferredSize = Dimension(460, 220)
        pack()
        setLocationRelativeTo(owner)
        addWindowListener(object : java.awt.event.WindowAdapter() {
            override fun windowClosing(e: java.awt.event.WindowEvent) { cancelled = true }
        })
        thread(isDaemon = true, name = "ms-login") {
            try {
                val device = launcher.accounts.beginMicrosoftLogin()
                SwingUtilities.invokeLater {
                    code.text = device.userCode
                    info.text = "<html>Go to <b>${device.verificationUri}</b> and enter this code.</html>"
                    copy.addActionListener {
                        Toolkit.getDefaultToolkit().systemClipboard.setContents(StringSelection(device.userCode), null)
                        runCatching { Desktop.getDesktop().browse(URI(device.verificationUri)) }
                    }
                }
                val account = launcher.accounts.finishMicrosoftLogin(device) { cancelled }
                Log.info("Signed in as ${account.name}.")
                SwingUtilities.invokeLater { onDone(); dispose() }
            } catch (e: Exception) {
                if (!cancelled) SwingUtilities.invokeLater {
                    info.text = "<html>${e.message}</html>"
                    code.text = ""
                }
            }
        }
    }
}

class ReportDialog(owner: JFrame, text: String) : JDialog(owner, "Conversion report", false) {
    init {
        contentPane = JScrollPane(JTextArea(text, 24, 90).apply { isEditable = false; font = Font(Font.MONOSPACED, Font.PLAIN, 12) })
        pack()
        setLocationRelativeTo(owner)
    }
}
