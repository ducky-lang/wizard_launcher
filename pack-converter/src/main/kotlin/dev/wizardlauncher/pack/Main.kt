package dev.wizardlauncher.pack

import java.nio.file.Path
import kotlin.system.exitProcess

fun main(args: Array<String>) {
    if (args.size < 2) {
        System.err.println("usage: <input pack> <output pack> [--rules file.json]... [--vanilla-jar client-1.20.1.jar]")
        exitProcess(64)
    }
    val rules = ArrayList<Path>()
    var vanilla: VanillaAssets? = null
    var i = 2
    while (i < args.size) {
        when (args[i]) {
            "--rules" -> rules.add(Path.of(args[++i]))
            "--vanilla-jar" -> vanilla = VanillaAssets.fromClientJar(Path.of(args[++i]))
            else -> { System.err.println("unknown option ${args[i]}"); exitProcess(64) }
        }
        i++
    }
    val report = PackConverter(vanilla, ::println).convert(Path.of(args[0]), Path.of(args[1]), rules)
    println(report.render())
}
