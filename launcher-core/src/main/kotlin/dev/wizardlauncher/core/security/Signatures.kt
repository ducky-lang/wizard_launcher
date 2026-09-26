package dev.wizardlauncher.core.security

import java.security.KeyFactory
import java.security.Signature
import java.security.spec.X509EncodedKeySpec
import java.util.Base64

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
