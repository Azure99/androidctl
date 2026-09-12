import base64
import datetime as dt
import ssl

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import NameOID

from androidctl.private_files import atomic_write


def tls_context(directory, key, pem):
    atomic_write(directory / "cert.pem", pem.encode())
    atomic_write(
        directory / "key.pem",
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(directory / "cert.pem", directory / "key.pem")
    return context


def certificate(key, name="phone.androidctl.local", *, expired=False, future=False):
    now = dt.datetime.now(dt.UTC)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    value = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now + dt.timedelta(days=1 if future else -2))
        .not_valid_after(now + dt.timedelta(days=-1 if expired else 3650))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), False)
        .sign(key, hashes.SHA256())
    )
    return (
        value.public_bytes(serialization.Encoding.PEM).decode(),
        base64.b64encode(value.public_bytes(serialization.Encoding.DER)).decode(),
    )
