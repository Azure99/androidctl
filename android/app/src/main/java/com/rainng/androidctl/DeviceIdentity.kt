package com.rainng.androidctl

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import org.bouncycastle.asn1.x500.X500Name
import org.bouncycastle.asn1.x509.BasicConstraints
import org.bouncycastle.asn1.x509.ExtendedKeyUsage
import org.bouncycastle.asn1.x509.Extension
import org.bouncycastle.asn1.x509.GeneralName
import org.bouncycastle.asn1.x509.GeneralNames
import org.bouncycastle.asn1.x509.KeyPurposeId
import org.bouncycastle.asn1.x509.KeyUsage
import org.bouncycastle.cert.jcajce.JcaX509CertificateConverter
import org.bouncycastle.cert.jcajce.JcaX509v3CertificateBuilder
import org.bouncycastle.operator.jcajce.JcaContentSignerBuilder
import java.io.File
import java.math.BigInteger
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.PrivateKey
import java.security.PublicKey
import java.security.SecureRandom
import java.security.cert.CertificateExpiredException
import java.security.cert.CertificateNotYetValidException
import java.security.cert.X509Certificate
import java.security.spec.ECGenParameterSpec
import java.util.Base64
import java.util.Date
import javax.net.ssl.KeyManagerFactory
import javax.net.ssl.SSLContext

internal class DeviceIdentity(
    context: Context,
) {
    val id: String
    val name: String
    private val keys = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
    private var cert: X509Certificate
    val certificate: String get() = Base64.getEncoder().encodeToString(cert.encoded)
    val tls: SSLContext
        get() {
            try {
                cert.checkValidity()
            } catch (_: CertificateNotYetValidException) {
                throw Rejected("certificateClock", "Certificate is not yet valid; check the device clock, then refresh status")
            } catch (_: CertificateExpiredException) {
                throw Rejected(
                    "certificateExpired",
                    "Certificate expired; check the device clock",
                )
            }
            val managers = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm()).apply { init(keys, null) }
            return SSLContext.getInstance("TLS").apply { init(managers.keyManagers, null, null) }
        }

    init {
        val identity = File(context.filesDir, "identity")
        id =
            if (identity.exists()) {
                identity.readText().also {
                    java.util.UUID.fromString(
                        it,
                    )
                }
            } else {
                token().also { persist(identity, it.toByteArray()) }
            }
        name = "$id.androidctl.local"
        if (!keys.containsAlias(ALIAS)) generate()
        cert = keys.getCertificate(ALIAS) as X509Certificate
        cert.verify(cert.publicKey)
        check(keys.getKey(ALIAS, null).encoded == null)
    }

    private fun generate() {
        val generator = KeyPairGenerator.getInstance("EC", "AndroidKeyStore")
        generator.initialize(
            KeyGenParameterSpec
                .Builder(ALIAS, KeyProperties.PURPOSE_SIGN)
                .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
                .setDigests(KeyProperties.DIGEST_SHA256, KeyProperties.DIGEST_NONE)
                .setUserAuthenticationRequired(false)
                .setUnlockedDeviceRequired(false)
                .build(),
        )
        val pair = generator.generateKeyPair()
        issue(pair.private, pair.public)
    }

    private fun issue(
        privateKey: PrivateKey,
        publicKey: PublicKey,
    ) {
        val subject = X500Name("CN=$name")
        val now = System.currentTimeMillis()
        val builder =
            JcaX509v3CertificateBuilder(
                subject,
                BigInteger(SERIAL_BITS, SecureRandom()).setBit(SERIAL_BITS - 1),
                Date(now - DAY_MS),
                Date(now + CERTIFICATE_MS),
                subject,
                publicKey,
            )
        builder.addExtension(Extension.basicConstraints, true, BasicConstraints(false))
        builder.addExtension(Extension.keyUsage, true, KeyUsage(KeyUsage.digitalSignature))
        builder.addExtension(Extension.extendedKeyUsage, false, ExtendedKeyUsage(KeyPurposeId.id_kp_serverAuth))
        builder.addExtension(Extension.subjectAlternativeName, false, GeneralNames(GeneralName(GeneralName.dNSName, name)))
        val cert =
            JcaX509CertificateConverter().getCertificate(
                builder.build(JcaContentSignerBuilder("SHA256withECDSA").build(privateKey)),
            )
        cert.verify(publicKey)
        keys.setKeyEntry(ALIAS, privateKey, null, arrayOf(cert))
    }

    private companion object {
        const val ALIAS = "androidctl-device"
        const val SERIAL_BITS = 159
        const val DAY_MS = 86400000L
        const val CERTIFICATE_MS = 3650L * DAY_MS
    }
}
