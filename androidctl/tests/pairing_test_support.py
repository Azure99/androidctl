import json
import uuid
from contextlib import asynccontextmanager
from http import HTTPStatus

from cryptography.hazmat.primitives.asymmetric import ec
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from androidctl.identity import DeviceIdentity
from androidctl.protocol import VERSION, Fault
from androidctl.state import ActivePairing, PairingRecord
from certificate_test_support import certificate, tls_context


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


class Gateway:
    def __init__(self, directory, *, identifier=None, variant="normal", dispatch=None):
        directory.mkdir()
        identifier = identifier or str(uuid.uuid4())
        name = f"{identifier}.androidctl.local"
        key = ec.generate_private_key(ec.SECP256R1())
        pem, der = certificate(
            key,
            "bad.androidctl.local" if variant == "san" else name,
            expired=variant == "expired",
            future=variant == "future",
        )
        self.tls = tls_context(directory, key, pem)
        self.identity = {"deviceId": identifier, "name": name, "certificate": der}
        self.headers = []
        self.credentials = {}
        self.registers = []
        self.lose_registration = False
        self.reject_confirmation = False
        self.wrong_welcome = False
        self.welcome_release = None
        self.connections = []
        self.dispatch = dispatch

    async def opening(self, connection, request):
        if request.path != "/androidctl":
            return connection.respond(HTTPStatus.NOT_FOUND, "wrong gateway path")
        return None

    async def reply(self, connection, value):
        block = uuid.uuid4().hex
        await connection.send(json.dumps({**value, "blockId": block}))
        ack = json.loads(await connection.recv())
        if ack != {"ack": block}:
            raise Fault("protocol", "Reply requires its matching acknowledgement")

    async def connected(self, connection):
        self.connections.append(connection)
        headers = connection.request.headers
        code = headers.get("X-AndroidCtl-Pairing")
        authorization = headers.get("Authorization", "")
        credential = authorization.removeprefix("Bearer ")
        self.headers.append((code, authorization))
        try:
            if self.welcome_release is not None:
                await self.welcome_release.wait()
            if headers.get("X-AndroidCtl-Version") != VERSION:
                rejection = "protocolMismatch"
            elif (
                not authorization
                and code == "000042"
                or authorization.startswith("Bearer ")
                and credential in self.credentials
                and not self.reject_confirmation
            ):
                rejection = None
            else:
                rejection = "unauthorized"
            if rejection is not None:
                await connection.send(
                    json.dumps(
                        {
                            "event": "rejected",
                            "code": rejection,
                            "blockId": uuid.uuid4().hex,
                        }
                    )
                )
                await connection.recv()
                return
            await self.reply(
                connection,
                {
                    "event": "connected",
                    "version": VERSION,
                    "deviceId": self.identity["deviceId"],
                    "name": (
                        "wrong.androidctl.local"
                        if self.wrong_welcome
                        else self.identity["name"]
                    ),
                    "pairingId": self.credentials.get(credential),
                },
            )
            async for encoded in connection:
                request = json.loads(encoded)
                if request["method"] == "pair.register":
                    self.registers.append(request)
                    pairing_id = "pair-" + str(len(self.registers))
                    self.credentials[request["args"]["credential"]] = pairing_id
                    if self.lose_registration:
                        await connection.close()
                        return
                    response = {
                        "id": request["id"],
                        "result": {"pairingId": pairing_id},
                    }
                else:
                    try:
                        response = {
                            "id": request["id"],
                            "result": await self.dispatch(
                                request["method"], request.get("args", {})
                            ),
                        }
                    except Fault as error:
                        response = {"id": request["id"], "error": error.problem.wire()}
                await self.reply(connection, response)
        except (ConnectionClosed, Fault):
            pass
        finally:
            await connection.close()

    @asynccontextmanager
    async def listening(self):
        async with serve(
            self.connected, "127.0.0.1", 0, ssl=self.tls, process_request=self.opening
        ) as server:
            self.identity["endpoint"] = (
                f"wss://127.0.0.1:{server.sockets[0].getsockname()[1]}/androidctl"
            )
            yield self

    def args(self, **extra):
        return {
            "endpoint": self.identity["endpoint"],
            "code": "000042",
            "name": "phone",
            "timeoutMs": 20000,
            **extra,
        }
