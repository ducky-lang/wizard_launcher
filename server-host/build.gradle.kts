plugins { java }

// Dependency-free: it shares a JVM with the vanilla server and ViaProxy, and
// every class it brought along would be one more thing to collide with theirs.
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
