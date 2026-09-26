plugins {
    id("fabric-loom") version "1.10-SNAPSHOT"
    java
}

data class Target(val loader: String, val java: Int, val sources: String, val mixins: List<String> = emptyList())

val targets = mapOf(
    "1.20.1" to Target("0.15.11", 17, "v1_20_1"),
    "1.21.1" to Target("0.16.9", 21, "v1_21_1", listOf("EffectPacketMixin", "MobEffectInstanceMixin")),
)
val mc = (findProperty("mc") as String?) ?: "1.20.1"
val target = targets[mc] ?: error("Wizard Legacy Packs does not support Minecraft $mc (supported: ${targets.keys})")

version = "3.0.0"
group = "dev.wizardlauncher"

base { archivesName.set("wizard-legacy-packs") }

repositories {
    mavenCentral()
}

dependencies {
    minecraft("com.mojang:minecraft:$mc")
    mappings(loom.officialMojangMappings())
    modImplementation("net.fabricmc:fabric-loader:${target.loader}")
}

sourceSets {
    main {
        java.srcDir("src/${target.sources}/java")
        java.srcDir("../pack-legacy/src/main/java")
        resources.srcDir("../pack-legacy/src/main/resources")
    }
}

java {
    sourceCompatibility = JavaVersion.toVersion(target.java)
    targetCompatibility = JavaVersion.toVersion(target.java)
}

tasks.withType<JavaCompile>().configureEach {
    options.release.set(target.java)
    options.encoding = "UTF-8"
}

tasks.processResources {
    val props = mapOf(
        "version" to project.version, "minecraft" to mc, "java" to target.java,
        "versionMixins" to target.mixins.joinToString("") { ",\n    \"$it\"" },
    )
    inputs.properties(props)
    filesMatching(listOf("fabric.mod.json", "wizard_legacy_packs.mixins.json")) { expand(props) }
}

tasks.remapJar {
    archiveFileName.set("wizard-legacy-packs-$mc.jar")
}
