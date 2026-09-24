package dev.wizardlauncher.core

import java.util.Properties

object BuildInfo {
    private val props = Properties().apply {
        BuildInfo::class.java.getResourceAsStream("build-info.properties")?.use(::load)
    }
    val version: String = props.getProperty("version", "dev")

    val microsoftClientId: String = System.getenv("MC_LAUNCHER_CLIENT_ID")?.takeIf { it.isNotBlank() }
        ?: props.getProperty("microsoftClientId", "")

    val userAgent = "WizardLauncher/$version"
}
