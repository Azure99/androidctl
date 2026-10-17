import hashlib
import json
from contextlib import contextmanager
from pathlib import Path

import pytest
from typer.testing import CliRunner

from androidctl import app as cli
from androidctl.protocol import VERSION


def test_bundled_resource_extracts_without_overwrite_or_daemon(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("ANDROIDCTL_WORKSPACE_ROOT", str(workspace))
    resource = tmp_path / "resources"
    resource.mkdir()
    names = ["reference.md", "SKILL.md", "EXTRA.md"]
    payloads = {name: name.encode() + b" fixture\x00" * 100 for name in names}
    for name, payload in payloads.items():
        (resource / name).write_bytes(payload)
    (resource / "notes.txt").write_bytes(b"not public")
    nested = resource / "nested"
    nested.mkdir()
    (nested / "hidden.md").write_bytes(b"not top-level")
    monkeypatch.setattr(cli, "files", lambda _: resource)
    monkeypatch.setattr(
        cli.LocalCall,
        "request",
        lambda *a, **k: pytest.fail("Extraction contacted daemon"),
    )
    output = tmp_path / "exported"
    first = CliRunner().invoke(cli.app, ["--json", "skill", "--out", output.name])
    assert first.exit_code == 0, first.output
    assert json.loads(first.output)["command"] == "skill"
    exported = json.loads(first.output)["data"]
    assert exported["path"] == str(output)
    assert exported["version"] == VERSION
    assert "sha256" not in exported
    assert exported["files"] == [
        {
            "name": name,
            "path": str(output / name),
            "sha256": hashlib.sha256(payloads[name]).hexdigest(),
        }
        for name in ["EXTRA.md", "SKILL.md", "reference.md"]
    ]
    assert sorted(path.name for path in output.iterdir()) == sorted(names)
    text_output = tmp_path / "text-exported"
    text = CliRunner().invoke(cli.app, ["skill", "--out", text_output.name])
    assert text.exit_code == 0, text.output
    assert str(text_output) in text.output
    assert VERSION in text.output
    assert not list(workspace.iterdir())
    second = CliRunner().invoke(cli.app, ["--json", "skill", "--out", str(output)])
    assert second.exit_code == 2
    assert json.loads(second.output)["command"] == "skill"
    for name, payload in payloads.items():
        assert (output / name).read_bytes() == payload
    absent = tmp_path / "missing-parent" / "file"
    missing_parent = CliRunner().invoke(
        cli.app, ["--json", "skill", "--out", str(absent)]
    )
    assert missing_parent.exit_code == 2
    assert json.loads(missing_parent.output)["command"] == "skill"
    assert json.loads(missing_parent.output)["result"] == "not sent"
    assert not absent.parent.exists()
    existing_file = tmp_path / "existing-file"
    existing_file.write_bytes(b"keep")
    refused = CliRunner().invoke(
        cli.app, ["--json", "skill", "--out", str(existing_file)]
    )
    assert refused.exit_code == 2
    assert json.loads(refused.output)["result"] == "not sent"
    assert existing_file.read_bytes() == b"keep"


def test_bundled_resource_retains_partial_write_failure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    resource = tmp_path / "resources"
    resource.mkdir()
    name = "reference.md"
    (resource / name).write_bytes(b"complete resource")
    (resource / "SKILL.md").write_bytes(b"complete instructions")
    monkeypatch.setattr(cli, "files", lambda _: resource)
    output = tmp_path / "partial"
    failing_path = output / "reference.md"
    original_open = Path.open

    class FullDisk:
        def __init__(self, stream):
            self.stream = stream

        def write(self, chunk):
            self.stream.write(chunk[:4])
            raise OSError("disk full")

    @contextmanager
    def open_file(path, *args, **kwargs):
        with original_open(path, *args, **kwargs) as stream:
            yield (FullDisk(stream) if path == failing_path else stream)

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "open", open_file)
        result = CliRunner().invoke(cli.app, ["--json", "skill", "--out", output.name])
    assert result.exit_code == 1
    facts = json.loads(result.output)
    assert facts["result"] == "failed"
    assert facts["data"] == {}
    assert "partial output retained" in facts["reason"]
    assert str(output) in facts["reason"]
    assert failing_path.read_bytes() == b"comp"
    assert (output / "SKILL.md").read_bytes() == b"complete instructions"
