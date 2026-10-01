from __future__ import annotations

import asyncio
import hashlib
import json
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple, NoReturn

import typer

from . import history, ui_view, workspace
from .cli_options import CliOptions, command_name, options
from .local_call import LocalCall
from .output import Output, display_name
from .protocol import (
    IMAGE_BYTES,
    TREE_BYTES,
    Fault,
    Result,
    TransportFailure,
    decode_chunk,
    rejected,
    uncertain,
)
from .user_files import create, require_new_file


@dataclass(frozen=True)
class View:
    all_nodes: bool = False
    boxes: bool = False
    raw: bool = False
    out: Path | None = None
    print_body: bool = False


@dataclass(frozen=True)
class DeliveryPaths:
    snapshot: Path | None = None


@dataclass(frozen=True)
class RenderedObservation:
    body: str | None
    public_refs: set[str]
    facts: dict[str, Any]


class CaptureMetadata(NamedTuple):
    size: int
    sha256: str
    encoding: str
    decoded_size: int
    decoded_sha256: str


def result_line(
    result: Result,
    command: str,
    output: Output,
    *,
    observed: bool = True,
) -> None:
    parts = [f"{display_name(result.command, quoted=False)}: {result.result}"]
    if result.reason:
        parts.append(display_name(result.reason, quoted=False))
    if result.error is not None:
        parts.append(f"code={result.error.code}")
        if result.error.stage:
            parts.append(f"stage={result.error.stage}")
    if result.data.get("notice"):
        parts.append(display_name(result.data["notice"], quoted=False))
    completeness = result.data.get("snapshotCompleteness", {})
    gaps = ["partial"] if completeness.get("partial") else []
    for field, name in (
        ("missingCount", "missing"),
        ("unavailableNodeCount", "unavailable"),
    ):
        if completeness.get(field):
            gaps.append(f"{name}={completeness[field]}")
    if gaps:
        parts.append("observationWarning=" + compact("; ".join(gaps)))
    if not observed and result.result in {"not sent", "failed", "UNKNOWN"}:
        parts.append("Observation: unavailable; run snapshot.")
    for field in ("snapshotPath",):
        if field in result.data:
            parts.append(field + "=" + compact(result.data[field]))
    for field in ("snapshotError", "historyError"):
        if result.data.get(field):
            parts.append(field + "=" + compact(result.data[field]))
    if result.result == "UNKNOWN" and command == "snapshot":
        parts.append(
            "Read-only reply unavailable; query again. For a new sample, run snapshot."
        )
    output.echo("; ".join(parts))


def compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class UiClient:
    def __init__(self, context: CliOptions, deadline: float):
        self.deadline = deadline
        self.context = context
        self.local = LocalCall(context.device, context.session)

    async def call(self, command: str, args: dict[str, Any]) -> Result:
        return await self.local.call(command, args, deadline=self.deadline)

    async def blob(self, capture: str, kind: str) -> tuple[bytes, dict[str, Any]]:
        data = bytearray()
        metadata: CaptureMetadata | None = None
        bound = TREE_BYTES if kind == "tree" else IMAGE_BYTES
        while True:
            response = await self.call(
                "ui.read", {"capture": capture, "kind": kind, "offset": len(data)}
            )
            chunk = response.data
            if response.result != "done":
                raise Fault("captureUnavailable", response.reason or response.result)
            if chunk["state"] == "pending":
                await asyncio.sleep(
                    min(0.025, max(0, self.deadline - time.monotonic()))
                )
                continue
            if chunk["state"] != "available":
                raise Fault("captureUnavailable", chunk["state"])
            if chunk.get("capture") != capture or chunk.get("offset") != len(data):
                raise Fault(
                    "captureMismatch",
                    "Attachment does not match this capture and offset",
                )
            try:
                current = CaptureMetadata(
                    chunk["size"],
                    chunk["sha256"],
                    chunk["encoding"],
                    chunk["decodedSize"],
                    chunk["decodedSha256"],
                )
            except KeyError as error:
                raise Fault(
                    "captureMismatch",
                    f"Attachment metadata is missing required field {error.args[0]}",
                ) from error
            if metadata is None:
                metadata = current
            if (
                current != metadata
                or not 0 <= current.size <= bound
                or not 0 <= current.decoded_size <= bound
                or current.encoding
                not in ({"raw", "zlib"} if kind == "tree" else {"raw"})
            ):
                raise Fault(
                    "captureMismatch",
                    "Attachment metadata changed or exceeds its bound",
                )
            data.extend(
                decode_chunk(
                    chunk["bytes"], len(data), current.size, code="captureMismatch"
                )
            )
            if len(data) == current.size:
                encoded_hash = hashlib.sha256(data).hexdigest()
                if encoded_hash != current.sha256:
                    raise Fault("captureMismatch", "Attachment hash mismatch")
                if current.encoding == "zlib":
                    decoder = zlib.decompressobj()
                    try:
                        body = decoder.decompress(data, current.decoded_size + 1)
                    except zlib.error as error:
                        raise Fault(
                            "captureMismatch", "Invalid compressed attachment"
                        ) from error
                    if (
                        not decoder.eof
                        or decoder.unused_data
                        or decoder.unconsumed_tail
                    ):
                        raise Fault(
                            "captureMismatch",
                            "Incomplete or trailing compressed attachment",
                        )
                else:
                    body = bytes(data)
                if (
                    len(body) != current.decoded_size
                    or (
                        encoded_hash
                        if current.encoding == "raw"
                        else hashlib.sha256(body).hexdigest()
                    )
                    != current.decoded_sha256
                ):
                    raise Fault(
                        "captureMismatch", "Decoded attachment length or hash mismatch"
                    )
                return body, chunk.get("image") or {}

    async def close(self) -> None:
        await self.local.close(deadline=self.deadline)


def delivery_paths(view: View) -> DeliveryPaths:
    snapshot = view.out.absolute() if view.out is not None else None
    if snapshot is not None:
        require_new_file(
            snapshot,
            "Observation destination already exists",
            Fault(
                "snapshotPath",
                "Observation destination parent must be an existing directory",
            ),
        )
    return DeliveryPaths(snapshot=snapshot)


def execute(
    ctx: typer.Context, command: str, args: dict[str, Any], view: View
) -> NoReturn:
    actual_command = command_name(ctx)
    timeout = args["timeoutMs"]
    deadline = time.monotonic() + timeout / 1000
    context = options(ctx)
    workspace.require_root(context.workspace_root)
    if view.print_body and context.as_json:
        raise Fault("arguments", "--print and --json cannot be used together")
    paths = delivery_paths(view)
    wire_args = dict(args)
    invocation: dict[str, Any] = {
        "command": command,
        "args": {key: value for key, value in wire_args.items() if key != "value"},
    }
    if view.all_nodes:
        invocation["args"]["all"] = True
    if view.boxes:
        invocation["args"]["boxes"] = True
    client = UiClient(context, deadline)

    async def request() -> tuple[Result, dict[str, Any] | None]:
        tree = None
        try:
            try:
                result = await client.call(command, wire_args)
            except TransportFailure as error:
                result = uncertain(actual_command, error)
            except Fault as error:
                result = rejected(actual_command, error)
            result.command = actual_command
            if client.local.connect_ms is not None:
                result.data["localConnectMs"] = client.local.connect_ms
            capture = result.data.get("capture")
            if capture is not None:
                started = time.monotonic()
                try:
                    body, _ = await client.blob(capture, "tree")
                    value = json.loads(body)
                    if not isinstance(value, dict):
                        raise Fault("captureMismatch", "Snapshot must be a JSON object")
                    tree = value
                except (Fault, OSError, ValueError, KeyError, TypeError) as error:
                    result.data["snapshotError"] = str(error)
                result.data["deliveryMs"] = int((time.monotonic() - started) * 1000)
            return result, tree
        finally:
            await client.close()

    result, tree = asyncio.run(request())
    deliver(
        result,
        tree,
        view,
        deadline,
        command=command,
        invocation=invocation,
        context=context,
        paths=paths,
    )
    raise typer.Exit(result.exit_code())


def deliver(
    result: Result,
    tree: dict[str, Any] | None,
    view: View,
    deadline: float,
    *,
    command: str,
    invocation: dict[str, Any],
    context: CliOptions,
    paths: DeliveryPaths,
) -> None:
    started = time.monotonic()
    body = None
    if tree is not None:
        rendered = render_observation(
            tree,
            view,
            invocation,
            source={
                key: result.data[key]
                for key in (
                    "capture",
                    "snapshotBeforeAction",
                    "snapshotCached",
                    "snapshotAgeMs",
                )
                if key in result.data
            },
            image=None,
        )
        result.data.update(rendered.facts)
        body = rendered.body
    save_delivery(
        result,
        tree,
        body,
        paths,
        deadline,
        workspace_root=context.workspace_root,
        raw=view.raw,
    )
    finish_primary_result(result, command=command)
    if tree is not None or "deliveryMs" in result.data:
        result.data["deliveryMs"] = result.data.get("deliveryMs", 0) + int(
            (time.monotonic() - started) * 1000
        )
    print_delivery(
        result,
        observed=tree is not None,
        command=command,
        as_json=context.as_json,
        body=body if view.print_body and "snapshotPath" in result.data else None,
    )


def render_observation(
    tree: dict[str, Any],
    view: View,
    invocation: dict[str, Any],
    *,
    source: dict[str, Any],
    image: dict[str, Any] | None,
) -> RenderedObservation:
    body = None
    public_refs: set[str] = set()
    facts: dict[str, Any] = {}
    try:
        if view.raw:
            public_refs = {node["ref"] for node in tree["nodes"]}
        if "connection" in tree:
            facts["snapshotConnection"] = tree["connection"]
        unavailable = [
            {"ref": node.get("ref"), "unavailable": node["unavailable"]}
            for node in tree.get("nodes", [])
            if node.get("unavailable")
        ]
        snapshot_facts = {key: value for key, value in tree.items() if key != "nodes"}
        snapshot_facts["nodeUnavailable"] = unavailable
        facts["snapshotCompleteness"] = {
            "partial": tree.get("partial"),
            "missingCount": len(tree.get("missing", [])),
            "unavailableNodeCount": len(unavailable),
        }
        if not view.raw:
            elements = ui_view.project(tree, all_nodes=view.all_nodes, boxes=view.boxes)
            screen = ui_view.screen(tree, elements)
            public_refs = {
                element.attrib["ref"]
                for element in screen.iter()
                if "ref" in element.attrib
            }
            metadata = ui_view.metadata(
                invocation,
                source=source,
                snapshot_facts=snapshot_facts,
                image=image,
                public_refs=public_refs,
                keyboard=screen.attrib["keyboard"],
            )
            body = ui_view.header(metadata) + ui_view.render(screen)
        if view.raw:
            body = json.dumps(tree, ensure_ascii=False, separators=(",", ":")) + "\n"
    except (Fault, KeyError, TypeError) as error:
        facts["snapshotError"] = str(error)
    return RenderedObservation(body=body, public_refs=public_refs, facts=facts)


def save_delivery(
    result: Result,
    tree: dict[str, Any] | None,
    body: str | None,
    paths: DeliveryPaths,
    deadline: float,
    *,
    workspace_root: Path,
    raw: bool,
) -> None:
    if body is not None and paths.snapshot is not None:
        try:
            path = paths.snapshot
            create(path.parent, path.name, body.encode("utf-8"))
            result.data["snapshotPath"] = str(path)
        except (Fault, OSError, ValueError) as error:
            result.data["snapshotError"] = str(error)
    history_body = body if paths.snapshot is None else None
    if tree is not None and history_body is not None:
        try:
            app, title = ui_view.app_title(tree, ui_view.window(tree))
            saved = history.save(
                workspace_root,
                history_body,
                None,
                app=app,
                title=title,
                deadline=deadline,
                suffix=".json" if raw else ".xml",
            )
            if saved.snapshot_path is not None:
                result.data["snapshotPath"] = str(saved.snapshot_path)
            if saved.warning:
                result.data["historyError"] = "; ".join(
                    filter(None, (result.data.get("historyError"), saved.warning))
                )
        except (Fault, OSError, ValueError, KeyError, TypeError) as error:
            result.data["historyError"] = "; ".join(
                filter(None, (result.data.get("historyError"), str(error)))
            )
    if "snapshotPath" not in result.data and tree is not None:
        result.data.setdefault("snapshotError", "Observation file was not saved")


def finish_primary_result(result: Result, *, command: str) -> None:
    if (
        result.result == "done"
        and command == "snapshot"
        and "snapshotPath" not in result.data
    ):
        result.result = "failed"
        result.reason = (
            result.data.get("historyError")
            or result.data.get("snapshotError")
            or "Observation file was not delivered"
        )


def print_delivery(
    result: Result,
    *,
    observed: bool,
    command: str,
    as_json: bool,
    body: str | None = None,
) -> None:
    output = Output()
    if as_json:
        wire = result.wire()
        output.json(wire)
        output.finish(result)
        return
    result_line(result, command, output, observed=observed)
    if body is not None:
        output.echo(body, nl=False)
    output.finish(result)
