package dev.wizardlauncher.core.install

/** Launch progress: a fraction in 0..1 (null = indeterminate) and a message. */
fun interface Progress {
    fun update(fraction: Double?, message: String)

    companion object {
        val NONE = Progress { _, _ -> }
    }
}

fun formatBytes(bytes: Long): String = when {
    bytes >= 1L shl 30 -> "%.2f GB".format(bytes / (1024.0 * 1024 * 1024))
    bytes >= 1L shl 20 -> "%.1f MB".format(bytes / (1024.0 * 1024))
    bytes >= 1L shl 10 -> "%.0f KB".format(bytes / 1024.0)
    else -> "$bytes B"
}
