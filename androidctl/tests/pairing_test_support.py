import uuid

from cryptography.hazmat.primitives.asymmetric import ec

from androidctl.identity import DeviceIdentity
from androidctl.state import ActivePairing, PairingRecord
from certificate_test_support import certificate


def save_active(state, value):
    value = dict(value)
    value.setdefault("pairingId", "pair")
    record = PairingRecord(
        device_id=value["deviceId"],
        certificate=value["certificate"],
        active=ActivePairing.model_validate(value),
        candidate=None,
    )
    state.save_record(record)
    return record.material(record.active)


def paired(state, material, alias):
    record, _ = state.prepare_pairing(
        DeviceIdentity(material["deviceId"], material["certificate"]),
        material["endpoint"],
        alias,
    )
    candidate = record.material(record.candidate)
    return save_active(state, candidate)


def active_material(state, identifier=None):
    record = state.resolve_selection(identifier)[0]
    return record.material(record.active)


def new_device_identity():
    identifier = str(uuid.uuid4())
    name = f"{identifier}.androidctl.local"
    return {
        "deviceId": identifier,
        "name": name,
        "certificate": certificate(ec.generate_private_key(ec.SECP256R1()), name)[1],
        "endpoint": "wss://127.0.0.1:18843/androidctl",
    }
