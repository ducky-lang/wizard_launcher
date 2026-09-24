package dev.wizardlauncher.pack

/** What the converter did and what it could not do, in plain words. */
class ConversionReport {
    val info = ArrayList<String>()
    val warnings = ArrayList<String>()
    var filesWritten = 0
    var filesRewritten = 0

    fun info(message: String) { info += message }
    fun warn(message: String) { warnings += message }

    fun render(): String = buildString {
        appendLine("Wizard Launcher resource pack conversion (1.16.5 -> 1.20.1)")
        appendLine("files written: $filesWritten, rewritten: $filesRewritten, warnings: ${warnings.size}")
        appendLine()
        if (info.isNotEmpty()) {
            appendLine("Changes:")
            info.forEach { appendLine("  - $it") }
            appendLine()
        }
        if (warnings.isNotEmpty()) {
            appendLine("Needs attention (could not be translated automatically):")
            warnings.forEach { appendLine("  ! $it") }
        } else {
            appendLine("Nothing needs attention.")
        }
    }
}
