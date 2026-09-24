package dev.wizardlauncher.core.security

import java.security.KeyFactory
import java.security.Signature
import java.security.spec.X509EncodedKeySpec
import java.util.Base64

/**
 * Ed25519 detached signatures, used to accept a catalog update only when it
 * was signed with the maintainer's key. Built into Java 15+, so no crypto
 * library is bundled.
 *
 * Public keys are base64 X.509 SubjectPublicKeyInfo; `tools/sign-catalog`
 * (launcher-app `--sign-catalog`) prints one when it creates a key pair.
 */
object Signatures {
    fun verify(data: ByteArray, signatureBase64: String, publicKeyBase64: String): Boolean = runCatching {
        val key = KeyFactory.getInstance("Ed25519")
            .generatePublic(X509EncodedKeySpec(Base64.getDecoder().decode(publicKeyBase64.trim())))
        Signature.getInstance("Ed25519").run {
            initVerify(key)
            update(data)
            verify(Base64.getDecoder().decode(signatureBase64.trim()))
        }
    }.getOrDefault(false)
}
