plugins { java }

tasks.jar {
    archiveFileName.set("wizard-client-boot.jar")
    manifest { attributes("Main-Class" to "dev.wizardlauncher.boot.SecureBoot") }
}
