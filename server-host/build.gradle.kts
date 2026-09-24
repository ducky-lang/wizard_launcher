plugins { java }

tasks.jar {
    archiveFileName.set("wizard-server-host.jar")
    manifest {
        attributes(
            "Main-Class" to "dev.wizardlauncher.host.ServerHost",
            "Premain-Class" to "dev.wizardlauncher.host.HostAgent",
            "Launcher-Agent-Class" to "dev.wizardlauncher.host.HostAgent",
        )
    }
}
