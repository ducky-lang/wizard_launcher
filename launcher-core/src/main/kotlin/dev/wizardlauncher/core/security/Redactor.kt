package dev.wizardlauncher.core.security

/**
 * Strips credentials from anything headed for a log file or the screen.
 * Pattern-based on purpose: it has to catch tokens inside exception
 * messages and third-party output, not only the ones we know we hold.
 */
object Redactor {
    private val patterns = listOf(
        Regex("(--accessToken\\s+)\\S+"),
        Regex("(\"?(?:access_token|refresh_token|id_token|accessToken|refreshToken|Token)\"?\\s*[:=]\\s*\"?)[^\"\\s,}]+", RegexOption.IGNORE_CASE),
        Regex("(Authorization:\\s*(?:Bearer|XBL3\\.0)\\s+)\\S+", RegexOption.IGNORE_CASE),
        // Bare JWTs (Minecraft access tokens are JWTs).
        Regex("()eyJ[A-Za-z0-9_-]{10,}\\.[A-Za-z0-9_-]{10,}\\.[A-Za-z0-9_-]+"),
        // Microsoft refresh tokens.
        Regex("()M\\.[A-Z0-9]{3,}_[A-Za-z0-9!*$.-]{40,}"),
    )

    fun redact(text: String): String =
        patterns.fold(text) { acc, re -> re.replace(acc) { m -> m.groupValues[1] + "[redacted]" } }
}
