package dev.wizardlauncher.core

import java.util.Properties

/** Values stamped in at build time (see launcher-core/build.gradle.kts). */
object BuildInfo {
    private val props = Properties().apply {
        BuildInfo::class.java.getResourceAsStream("build-info.properties")?.use(::load)
    }
    val version: String = props.getProperty("version", "dev")

    /**
     * The Azure application (client) id used for Microsoft sign-in. A public
     * client id is not a secret - it identifies the app, it grants nothing -
     * but it is still injected at build time rather than committed, so forks
     * cannot accidentally ship under somebody else's app registration.
     */
    val microsoftClientId: String = System.getenv("MC_LAUNCHER_CLIENT_ID")?.takeIf { it.isNotBlank() }
        ?: props.getProperty("microsoftClientId", "")

    val userAgent = "WizardLauncher/$version"
}
