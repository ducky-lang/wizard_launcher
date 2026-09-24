plugins { kotlin("jvm") }

dependencies {
    api("com.google.code.gson:gson:2.11.0")
    implementation("net.java.dev.jna:jna-platform:5.15.0")
    testImplementation(kotlin("test"))
    testImplementation("org.junit.jupiter:junit-jupiter:5.11.3")
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}

dependencies { implementation(project(":pack-converter")) }

tasks.processResources {
    val props = mapOf(
        "version" to project.version.toString(),
        // Public Azure client id, injected at build time (see BuildInfo).
        "microsoftClientId" to (System.getenv("MC_LAUNCHER_CLIENT_ID") ?: findProperty("msClientId")?.toString() ?: ""),
    )
    inputs.properties(props)
    filesMatching("**/build-info.properties") { expand(props) }
}

evaluationDependsOn(":client-boot")
tasks.test {
    val bootJar = project(":client-boot").tasks.named<Jar>("jar")
    dependsOn(bootJar)
    doFirst { systemProperty("wizard.bootJar", bootJar.get().archiveFile.get().asFile.absolutePath) }
}
