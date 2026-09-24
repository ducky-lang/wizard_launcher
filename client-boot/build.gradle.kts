plugins { java }

// Deliberately dependency-free: this jar sits on the game's classpath.
tasks.jar {
    archiveFileName.set("wizard-client-boot.jar")
    manifest { attributes("Main-Class" to "dev.wizardlauncher.boot.SecureBoot") }
}
