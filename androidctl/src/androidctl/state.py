from __future__ import annotations

import json
import secrets
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from .identity import DeviceIdentity, endpoint, require_alias, trust_context
from .private_files import check_private, private_directory, write_json
from .protocol import Fault, Problem


class PairingSlot(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        strict=True,
        str_min_length=1,
    )

    alias: str
    endpoint: str
    credential: str


class ActivePairing(PairingSlot):
    pairing_id: str


class PairingRecord(BaseModel):
    model_config = PairingSlot.model_config

    device_id: str
    certificate: str
    active: ActivePairing | None
    candidate: PairingSlot | None

    @property
    def name(self) -> str:
        return self.identity.name

    @property
    def identity(self) -> DeviceIdentity:
        return DeviceIdentity(self.device_id, self.certificate)

    def material(self, slot: PairingSlot) -> dict[str, Any]:
        return {
            "deviceId": self.device_id,
            "name": self.name,
            "certificate": self.certificate,
            **slot.model_dump(by_alias=True),
        }

    @property
    def current_slot(self) -> PairingSlot:
        return cast(PairingSlot, self.active or self.candidate)


class HostState:
    """Only connection configuration is durable. Device work lives in memory."""

    def __init__(self, root: Path):
        self.root = root
        self.pairings = root / "pairings"
        private_directory(self.pairings)
        self.selection = root / "device.json"

    def records(self) -> Iterator[tuple[Path, PairingRecord | Fault]]:
        for path in sorted(self.pairings.glob("*.json")):
            try:
                yield path, self.record(path.stem)
            except Fault as error:
                yield path, error

    def devices(self, identifier: str | None = None) -> dict[str, Any]:
        records = list(self.records())
        selection_error = None
        source = None
        selected = None
        try:
            identifier, origin = self._selection_input(identifier)
            if identifier is not None:
                source = origin
            record = self._choose_record(
                identifier, origin, [record for _, record in records]
            )
            identifier, source = record.device_id, origin
            selected = require_active(record).device_id
        except Fault as error:
            selection_error = error.problem.wire()
        context = {"deviceSource": source or "default"}
        if selected is not None:
            context["deviceId"] = selected
        return {
            "devices": [
                {**summary(path, record), "selected": path.stem == selected}
                for path, record in records
            ],
            "selectionError": selection_error,
            "selectionDevice": identifier,
            "context": context,
        }

    def _record_path(self, identifier: str) -> Path:
        return self.pairings / f"{identifier}.json"

    def record(self, identifier: str) -> PairingRecord:
        path = self._record_path(identifier)
        try:
            device_id = str(uuid.UUID(identifier))
            if identifier != device_id:
                raise ValueError("Pairing filename must be its canonical device UUID")
            check_private(path)
            value = PairingRecord.model_validate_json(path.read_bytes(), by_name=False)
            if value.device_id != device_id:
                raise ValueError("Pairing filename and deviceId differ")
            for material in (value.active, value.candidate):
                if material is None:
                    continue
                endpoint(material.endpoint)
            trust_context(value.certificate)
            if value.active is None and value.candidate is None:
                raise ValueError("Saved pairing has no credential")
        except FileNotFoundError as error:
            raise Fault("deviceNotFound", "No saved pairing for this device") from error
        except (
            OSError,
            ValueError,
            TypeError,
            Fault,
        ) as error:
            raise Fault(
                "pairingInvalid",
                f"{path}: {error}; run androidctl devices, then androidctl device "
                "forget with this exact record path before pairing again",
                "local.configuration",
            ) from error
        return value

    def find_pairing_for_recovery(self, identifier: str) -> PairingRecord | None:
        for path, record in self.records():
            if isinstance(record, Fault):
                if identifier == path.stem:
                    raise record
                continue
            if identifier == record.device_id or any(
                part is not None and part.alias == identifier
                for part in (record.active, record.candidate)
            ):
                return record
        return None

    def resolve_selection(self, identifier: str | None) -> tuple[PairingRecord, str]:
        identifier, source = self._selection_input(identifier)
        record = self._choose_record(
            identifier, source, [value for _, value in self.records()]
        )
        return record, source

    def _selection_input(self, identifier: str | None) -> tuple[str | None, str]:
        if identifier is not None:
            return identifier, "explicit"
        identifier = self.selected_id()
        return identifier, "global" if identifier is not None else "sole"

    def _choose_record(
        self,
        identifier: str | None,
        source: str,
        records: list[PairingRecord | Fault],
    ) -> PairingRecord:
        if identifier is None:
            if any(isinstance(value, Fault) for value in records):
                raise Fault(
                    "pairingInvalid",
                    "Unusable saved pairing; run devices and select "
                    "explicitly with --device",
                )
            if not records:
                raise Fault(
                    "deviceRequired",
                    "No paired devices; install the matching APK, start Shizuku "
                    "and Gateway, then show a code and run "
                    "androidctl pair HOST:PORT CODE --name NAME",
                )
            if len(records) != 1:
                raise Fault("deviceRequired", "Select one paired device with --device")
            return cast(PairingRecord, records[0])
        matches = [
            v
            for v in records
            if isinstance(v, PairingRecord)
            and (
                identifier == v.device_id
                or source == "explicit"
                and identifier == v.current_slot.alias
            )
        ]
        if len(matches) != 1:
            raise Fault(
                "deviceNotFound",
                f"No unique paired device: {identifier}; run androidctl devices "
                "then androidctl device select NAME or use --device NAME",
            )
        return matches[0]

    def selected_id(self) -> str | None:
        try:
            if not self.selection.exists():
                return None
            check_private(self.selection)
            identifier = json.loads(self.selection.read_bytes())["deviceId"]
            if not isinstance(identifier, str) or not identifier:
                raise ValueError("Expected a selected deviceId")
            if str(uuid.UUID(identifier)) != identifier:
                raise ValueError("Selected deviceId must be a canonical UUID")
            return identifier
        except (OSError, ValueError, KeyError, TypeError, Fault) as error:
            raise Fault(
                "selectionInvalid",
                f"{self.selection}: {error}; run androidctl devices, then "
                "androidctl device select NAME to replace this selection",
                "local.configuration",
            ) from error

    def save_record(self, value: PairingRecord) -> None:
        write_json(self._record_path(value.device_id), value.model_dump(by_alias=True))

    def resolve_forget_key(self, identifier: str) -> str:
        matches = [
            path.stem
            for path, record in self.records()
            if identifier
            in (
                path.stem,
                str(path) if isinstance(record, Fault) else record.current_slot.alias,
            )
        ]
        if len(matches) != 1:
            raise Fault(
                "deviceNotFound", "Use an exact device name or bad record from devices"
            )
        return matches[0]

    def forget(self, key: str) -> dict[str, Any]:
        # The identifier comes from the directory enumeration, including bad records.
        self._record_path(key).unlink()
        result: dict[str, Any] = {"forgotten": key, "selectionCleared": False}
        try:
            if self.selected_id() == key:
                self.selection.unlink()
                result["selectionCleared"] = True
        except Fault as error:
            result["selectionError"] = error.problem.wire()
        except OSError as error:
            result["selectionError"] = Problem(
                code="localConfiguration",
                message=str(error),
                stage="local.configuration",
            ).wire()
        return result

    def prepare_pairing(
        self, identity: DeviceIdentity, address: str, alias: str
    ) -> tuple[PairingRecord, bool]:
        device_id = identity.device_id
        require_alias(alias)
        for _, saved in self.records():
            if isinstance(saved, Fault):
                continue
            aliases = [
                part.alias
                for part in (saved.active, saved.candidate)
                if part is not None
            ]
            if saved.device_id != device_id and (
                alias in (*aliases, saved.device_id) or device_id in aliases
            ):
                raise Fault("deviceName", "This device name is already paired")
        if self._record_path(device_id).exists():
            record = self.record(device_id)
            if record.certificate != identity.certificate:
                raise Fault(
                    "identityChanged", "Use device trust with a trusted certificate"
                )
            if record.candidate is not None:
                record.candidate.endpoint = address
                self.save_record(record)
                return record, False
        else:
            record = PairingRecord(
                device_id=device_id,
                certificate=identity.certificate,
                active=None,
                candidate=None,
            )
        record.candidate = PairingSlot(
            endpoint=address,
            alias=alias,
            credential=secrets.token_urlsafe(32),
        )
        self.save_record(record)
        return record, True


def require_active(record: PairingRecord) -> PairingRecord:
    if record.active is None:
        raise Fault(
            "pairingUnconfirmed",
            "Pairing is unconfirmed; run pair again with a current code. "
            "The saved credential is checked first",
        )
    return record


def summary(path: Path, record: PairingRecord | Fault) -> dict[str, Any]:
    if isinstance(record, Fault):
        return {
            "deviceId": path.stem,
            "path": str(path),
            "error": record.problem.wire(),
        }
    material = record.current_slot
    return {
        "deviceId": record.device_id,
        "alias": material.alias,
        "endpoint": material.endpoint,
        **({"pairingId": record.active.pairing_id} if record.active else {}),
        "confirmed": record.active is not None,
        "pending": record.candidate is not None,
    }
