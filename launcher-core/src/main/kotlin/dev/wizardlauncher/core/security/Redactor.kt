package dev.wizardlauncher.core.security

object Redactor {
    private val patterns = listOf(
        Regex("(--accessToken\\s+)\\S+"),
        Regex("(\"?(?:access_token|refresh_token|id_token|accessToken|refreshToken|Token)\"?\\s*[:=]\\s*\"?)[^\"\\s,}]+", RegexOption.IGNORE_CASE),
        Regex("(Authorization:\\s*(?:Bearer|XBL3\\.0)\\s+)\\S+", RegexOption.IGNORE_CASE),

        Regex("()eyJ[A-Za-z0-9_-]{10,}\\.[A-Za-z0-9_-]{10,}\\.[A-Za-z0-9_-]+"),

        Regex("()M\\.[A-Z0-9]{3,}_[A-Za-z0-9!*$.-]{40,}"),
    )

    fun redact(text: String): String =
        patterns.fold(text) { acc, re -> re.replace(acc) { m -> m.groupValues[1] + "[redacted]" } }
}
