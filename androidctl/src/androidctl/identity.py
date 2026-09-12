from __future__ import annotations

import base64
import datetime as dt
import ipaddress
import ssl
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import SplitResult, urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .private_files import atomic_write
from .protocol import Fault

DEVICE_NAME_SUFFIX = ".androidctl.local"


@dataclass(frozen=True)
class DeviceIdentity:
    device_id: str
    certificate: str

    @property
    def name(self) -> str:
        return self.device_id + DEVICE_NAME_SUFFIX


def endpoint(
    value: str, *, source: Literal["local", "device"] = "device"
) -> SplitResult:
    path = "/ipc" if source == "local" else "/androidctl"
    try:
        parsed = urlsplit(value)
    except ValueError as error:
        raise Fault("endpoint", "Invalid endpoint address") from error
    if (
        parsed.scheme != "wss"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path != path
    ):
        raise Fault("endpoint", f"Expected wss://HOST:PORT{path}")
    try:
        _ = parsed.port
    except ValueError as error:
        raise Fault("endpoint", "Invalid endpoint port") from error
    return parsed


def trust_context(certificate: str) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    der = base64.b64decode(certificate, validate=True)
    context.load_verify_locations(cadata=ssl.DER_cert_to_PEM_cert(der))
    return context


def device_endpoint(address: str) -> str:
    value = f"wss://{address}/androidctl"
    if not endpoint(value).port:
        raise Fault("endpoint", "Expected HOST:PORT (use [IPv6]:PORT for IPv6)")
    return value


def pairing_endpoint(address: str, code: str, alias: str) -> str:
    value = device_endpoint(address)
    if len(code) != 6 or not code.isascii() or not code.isdecimal():
        raise Fault(
            "pairingCode", "CODE must be exactly six digits, including leading zeros"
        )
    require_alias(alias)
    return value


def require_alias(alias: str) -> None:
    if not alias.strip() or len(alias) > 100 or any(ord(c) < 32 for c in alias):
        raise Fault(
            "deviceName", "Device name must contain 1 to 100 printable characters"
        )


def device_certificate(der: bytes) -> DeviceIdentity:
    """Read the first peer's identity without authenticating its origin (TOFU)."""
    try:
        certificate = x509.load_der_x509_certificate(der)
        names = certificate.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value.get_values_for_type(x509.DNSName)
        if len(names) != 1 or not names[0].endswith(DEVICE_NAME_SUFFIX):
            raise ValueError("Expected a canonical device UUID certificate name")
        name = names[0]
        identifier = name.removesuffix(DEVICE_NAME_SUFFIX)
        if str(uuid.UUID(identifier)) != identifier:
            raise ValueError("Certificate device UUID is not canonical")
        encoded = validate_certificate(certificate, name)
        return DeviceIdentity(identifier, encoded)
    except Fault as error:
        raise Fault(error.problem.code, str(error), "tls") from error
    except (ValueError, x509.ExtensionNotFound) as error:
        raise Fault("certificate", str(error), "tls") from error


def trusted_certificate(pem: str, name: str) -> str:
    try:
        certificate = x509.load_pem_x509_certificate(pem.encode("ascii"))
        return validate_certificate(certificate, name)
    except (ValueError, UnicodeError) as error:
        raise Fault("certificate", str(error)) from error


def validate_certificate(certificate: x509.Certificate, name: str) -> str:
    try:
        now = dt.datetime.now(dt.UTC)
        if now < certificate.not_valid_before_utc:
            raise ValueError(
                "Certificate is not yet valid; check the host and device clocks"
            )
        if now > certificate.not_valid_after_utc:
            raise ValueError("Certificate expired; check clocks")
        names = certificate.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value.get_values_for_type(x509.DNSName)
        if name not in names:
            raise ValueError("Certificate names a different device")
        return base64.b64encode(
            certificate.public_bytes(serialization.Encoding.DER)
        ).decode("ascii")
    except (ValueError, UnicodeError, x509.ExtensionNotFound) as error:
        raise Fault("certificate", str(error)) from error


def local_identity(root: Path) -> tuple[ssl.SSLContext, dict[str, Any]]:
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "AndroidCtl local daemon")]
    )
    now = dt.datetime.now(dt.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=1))
        .not_valid_after(now + dt.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    key_path, cert_path = root / "ipc.key", root / "ipc.pem"
    atomic_write(
        key_path,
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )
    atomic_write(cert_path, certificate.public_bytes(serialization.Encoding.PEM))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert_path, key_path)
    return context, {
        "name": "127.0.0.1",
        "certificate": base64.b64encode(
            certificate.public_bytes(serialization.Encoding.DER)
        ).decode("ascii"),
    }
