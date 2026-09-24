package dev.wizardlauncher.app

import com.formdev.flatlaf.FlatDarkLaf
import java.awt.Color
import java.awt.Font
import javax.swing.UIManager

/** The castle palette, carried over from the 1.x launcher's theme.json. */
object Theme {
    val bg = Color(0x08080c)
    val panel = Color(0x111118)
    val panelAlt = Color(0x141420)
    val border = Color(0x26262f)
    val gold = Color(0xf2c14e)
    val goldBright = Color(0xffe08a)
    val arcane = Color(0x8b7ae8)
    val danger = Color(0xef4444)
    val success = Color(0x7de3b0)
    val text = Color(0xf4f4f8)
    val textSub = Color(0xa8a8b8)
    val textFaint = Color(0x6c6c7c)

    fun install() {
        System.setProperty("flatlaf.useWindowDecorations", "true")
        FlatDarkLaf.setup()
        UIManager.put("Panel.background", bg)
        UIManager.put("RootPane.background", bg)
        UIManager.put("Component.accentColor", gold)
        UIManager.put("Component.focusColor", gold)
        UIManager.put("ProgressBar.foreground", gold)
        UIManager.put("ProgressBar.background", panelAlt)
        UIManager.put("ProgressBar.arc", 999)
        UIManager.put("Button.arc", 14)
        UIManager.put("Component.arc", 10)
        UIManager.put("TextComponent.arc", 10)
        UIManager.put("TitlePane.background", bg)
        UIManager.put("TitlePane.foreground", textSub)
        UIManager.put("MenuBar.background", bg)
        UIManager.put("TextArea.background", panel)
        UIManager.put("ScrollPane.background", panel)
        UIManager.put("Label.foreground", text)
    }

    fun title(size: Float) = Font(Font.SERIF, Font.BOLD, 10).deriveFont(size)
}
