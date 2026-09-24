rootProject.name = "wizard-launcher"

include("launcher-core", "pack-converter", "server-host", "client-boot", "launcher-app")

dependencyResolutionManagement {
    repositories { mavenCentral() }
}
