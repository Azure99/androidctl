import json
import multiprocessing
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from androidctl import history, private_files, ui_client, ui_view, user_files
from androidctl.cli_options import CliOptions
from androidctl.private_files import LifetimeLock, private_directory
from androidctl.protocol import Fault, Problem, Result
from ui_test_support import tree


@pytest.mark.parametrize("folder", [".androidctl", ".androidctl/snapshots"])
def test_cache_nondirectory_reports_type_before_permissions_and_preserves_file(
    tmp_path, folder
):
    if folder.endswith("snapshots"):
        private_directory(tmp_path / ".androidctl")
    blocker = tmp_path / folder
    blocker.write_bytes(b"keep original")
    with pytest.raises(Fault) as caught:
        history.save(
            tmp_path, "body", None, app="", title="", deadline=time.monotonic() + 2
        )
    assert caught.value.problem.code == "history"
    assert "not a directory" in str(caught.value)
    assert str(blocker) in str(caught.value)
    assert "--workspace-root" in str(caught.value)
    assert blocker.read_bytes() == b"keep original"


@pytest.mark.parametrize("os_error", [False, True])
def test_cache_directory_diagnostic_keeps_os_or_problem_facts(
    tmp_path, monkeypatch, os_error
):
    problem = Problem(
        code="privatePermissions",
        message="permission detail",
        stage="local.test",
        diagnostic_truncated=True,
    )
    error = (
        PermissionError(13, "controlled permission failure")
        if os_error
        else Fault.from_problem(problem)
    )

    def denied(path):
        raise error

    monkeypatch.setattr(history, "private_directory", denied)
    with pytest.raises(Fault) as caught:
        history.save(
            tmp_path, "body", None, app="", title="", deadline=time.monotonic() + 2
        )
    message = str(caught.value)
    assert str(error) in message
    assert "--workspace-root" in message
    if os_error:
        assert str(tmp_path / ".androidctl") in message
        assert caught.value.problem.code == "history"
    else:
        assert caught.value.problem.code == problem.code
        assert caught.value.problem.stage == problem.stage
        assert caught.value.problem.diagnostic_truncated is True
        assert problem.message == "permission detail"
    assert not list(tmp_path.iterdir())


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DACL diagnostic")
def test_cache_dacl_diagnostic_does_not_repair_existing_acl(tmp_path):
    root = tmp_path / ".androidctl"
    private_directory(root)
    owned = root / "keep.bin"
    owned.write_bytes(b"preserve")
    subprocess.run(
        ["icacls", str(root), "/grant", "*S-1-1-0:(RX)"],
        check=True,
        capture_output=True,
    )
    before = subprocess.check_output(["icacls", str(root)])
    with pytest.raises(Fault) as caught:
        history.save(
            tmp_path, "body", None, app="", title="", deadline=time.monotonic() + 2
        )
    assert caught.value.problem.code == "privatePermissions"
    assert "Windows DACL" in str(caught.value)
    assert "chmod" not in str(caught.value)
    assert subprocess.check_output(["icacls", str(root)]) == before
    assert owned.read_bytes() == b"preserve"
    assert list(root.iterdir()) == [owned]


@pytest.mark.parametrize(("uid", "mode"), [(2000, 0o700), (1000, 0o755)])
def test_posix_private_diagnostic_reports_observed_owner_and_mode(
    tmp_path, monkeypatch, uid, mode
):
    info = SimpleNamespace(st_uid=uid, st_mode=mode)
    with monkeypatch.context() as boundary:
        boundary.setattr(private_files, "sys", SimpleNamespace(platform="linux"))
        boundary.setattr(private_files, "os", SimpleNamespace(getuid=lambda: 1000))
        boundary.setattr(type(tmp_path), "stat", lambda self: info)
        with pytest.raises(Fault) as caught:
            private_files.check_private(tmp_path)
    assert caught.value.problem.code == "privatePermissions"
    message = str(caught.value)
    assert "POSIX" in message
    assert f"owner UID={uid}" in message
    assert "expected UID=1000" in message
    assert f"mode={oct(mode)}" in message
    assert "Windows" not in message


def test_failed_reclamation_remains_counted_and_can_be_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "MAX_FILES", 1)

    def save():
        return history.save(
            tmp_path,
            "<screen/>",
            None,
            app="fixture",
            title="retained",
            deadline=time.monotonic() + 2,
        )

    first = save().snapshot_path
    unlink = Path.unlink

    def denied(path, **kwargs):
        if path == first:
            raise PermissionError("controlled deletion failure")
        return unlink(path, **kwargs)

    monkeypatch.setattr(Path, "unlink", denied)
    with pytest.raises(Fault, match="quota remains occupied") as caught:
        save()
    assert str(first) in str(caught.value)
    assert list(first.parent.iterdir()) == [first]
    assert first.read_bytes() == b"<screen/>"
    monkeypatch.setattr(Path, "unlink", unlink)
    recovered = save()
    assert recovered.snapshot_path.read_bytes() == b"<screen/>"
    assert recovered.warning is None
    assert not first.exists()
    assert recovered.snapshot_path.name.startswith("000002-")
    assert list(first.parent.iterdir()) == [recovered.snapshot_path]


@pytest.mark.parametrize("replace", [False, True])
def test_history_reclaims_edited_or_replaced_groups_and_preserves_unmatched_files(
    tmp_path, monkeypatch, replace
):
    monkeypatch.setattr(history, "MAX_FILES", 3)
    deadline = time.monotonic() + 10
    body = ui_view.header({"command": "snapshot"}) + '<screen title="Cart" />\n'
    first_xml, first_png, first_error, first_warning = history.save(
        tmp_path, body, b"first", app="shop", title="Cart", deadline=deadline
    )
    assert first_error is None
    assert first_warning is None
    assert first_xml.stem == first_png.stem
    assert first_xml.read_text(encoding="utf-8") == body
    if replace:
        first_png.unlink()
    first_png.write_bytes(b"user changed this file")
    foreign = first_xml.parent / "000000-user.xml"
    foreign.write_bytes(b"user file")
    second_xml, second_png, second_error, second_warning = history.save(
        tmp_path,
        '{"screen":"Cart"}',
        b"second",
        app="shop",
        title="Cart",
        deadline=deadline,
        suffix=".json",
    )
    assert second_error is None
    assert second_warning is None
    assert not first_xml.exists()
    assert not first_png.exists()
    assert foreign.read_bytes() == b"user file"
    assert second_xml.stem == second_png.stem
    assert second_xml.suffix == ".json"
    assert json.loads(second_xml.read_bytes()) == {"screen": "Cart"}
    assert second_xml.name.startswith("000002-")


def test_history_scans_old_files_by_actual_size_and_numeric_sequence(
    tmp_path, monkeypatch
):
    root = tmp_path / ".androidctl"
    snapshots = root / "snapshots"
    private_directory(root)
    private_directory(snapshots)
    foreign_file = root / "foreign.json"
    foreign_file.write_bytes(b"unrelated file")
    older = snapshots / "999999-20261006T120000000000Z-fixture-Edited.xml"
    newer = snapshots / "1000000-20261006T120001000000Z-fixture-View.xml"
    older.write_bytes(b"12345678")
    newer.write_bytes(b"x")
    monkeypatch.setattr(history, "MAX_BYTES", 10)
    saved = history.save(
        tmp_path,
        "new",
        None,
        app="fixture",
        title="View",
        deadline=time.monotonic() + 5,
    ).snapshot_path
    assert not older.exists()
    assert newer.read_bytes() == b"x"
    assert saved.read_bytes() == b"new"
    assert saved.name.startswith("1000001-")
    assert foreign_file.read_bytes() == b"unrelated file"
    newer.unlink()
    saved.unlink()
    restarted = history.save(
        tmp_path,
        "new",
        None,
        app="fixture",
        title="View",
        deadline=time.monotonic() + 5,
    ).snapshot_path
    assert restarted.name.startswith("000001-")


def test_explicit_image_in_matching_cache_path_is_reclaimable(tmp_path, monkeypatch):
    root = tmp_path / ".androidctl"
    private_directory(root)
    private_directory(root / "shots")
    name = "000001-20261006T120000000000Z-fixture-View.png"
    cached = root / "shots" / name
    outside = tmp_path / name
    deadline = time.monotonic() + 5
    for path in (cached, outside):
        user_files.create(path.parent, path.name, b"PNG")
    monkeypatch.setattr(history, "MAX_FILES", 1)
    saved = history.save(
        tmp_path, "body", None, app="fixture", title="next", deadline=deadline
    )
    assert saved.snapshot_path.read_bytes() == b"body"
    assert not cached.exists()
    assert outside.read_bytes() == b"PNG"


def test_history_rejects_capture_over_limit_without_creating_files(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(history, "MAX_BYTES", 2)
    with pytest.raises(Fault, match="size limit"):
        history.save(
            tmp_path,
            "<screen />",
            None,
            app="",
            title="",
            deadline=time.monotonic() + 5,
        )
    assert not list(tmp_path.iterdir())


def save_history_group(args):
    directory, number, barrier = args
    barrier.wait(timeout=10)
    paths = history.save(
        directory,
        f'<screen title="{number}" />\n',
        str(number).encode(),
        app="fixture",
        title="Concurrent",
        deadline=time.monotonic() + 10,
    )
    return os.getpid(), paths


def test_history_concurrent_writers_use_distinct_complete_groups(tmp_path):
    context = multiprocessing.get_context("spawn")
    with (
        context.Manager() as manager,
        ProcessPoolExecutor(max_workers=4, mp_context=context) as pool,
    ):
        barrier = manager.Barrier(4)
        saved = list(
            pool.map(save_history_group, [(tmp_path, n, barrier) for n in range(4)])
        )
    assert len({pid for pid, _ in saved}) == 4
    groups = [paths for _, paths in saved]
    assert len({xml.name for xml, _, _, _ in groups}) == 4
    for xml, png, error, warning in groups:
        assert error is None
        assert warning is None
        assert xml.stem == png.stem
        title = ET.fromstring(xml.read_bytes()).attrib["title"]
        assert title.encode() == png.read_bytes()
    assert {int(xml.name.split("-", 1)[0]) for xml, _, _, _ in groups} == {1, 2, 3, 4}


@pytest.mark.skipif(sys.platform != "win32", reason="Windows real open-handle gate")
def test_blocked_history_counts_owned_file_and_tries_other_groups(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(history, "MAX_FILES", 1)

    def save():
        return history.save(
            tmp_path,
            '<screen title="\u8d26\u5355"/>',
            None,
            app="shop",
            title="\u8d26\u5355",
            deadline=time.monotonic() + 5,
        )[0]

    first = save()
    assert "\u8d26\u5355" in first.name
    with first.open("rb"):
        with pytest.raises(Fault, match="quota remains occupied"):
            save()
        assert list(first.parent.glob("*.xml")) == [first]
        monkeypatch.setattr(history, "MAX_FILES", 2)
        second = save()
        third = save()
        assert first.exists()
        assert not second.exists()
        assert third.exists()
        assert set(first.parent.glob("*.xml")) == {first, third}
        monkeypatch.chdir(tmp_path)
        for as_json in (False, True):
            result = Result(command="snapshot", result="done")
            ui_client.deliver(
                result,
                tree(),
                None,
                ui_client.View(),
                time.monotonic() + 5,
                command="snapshot",
                invocation={"command": "snapshot", "args": {}},
                context=CliOptions(None, "default", as_json, workspace_root=tmp_path),
                binding=None,
                paths=ui_client.DeliveryPaths(),
            )
            output = capsys.readouterr().out
            assert result.result == "done"
            assert str(first) in result.data["historyError"]
            assert (tmp_path / result.data["snapshotPath"]).exists()
            if as_json:
                assert (
                    json.loads(output)["data"]["historyError"]
                    == result.data["historyError"]
                )
            else:
                assert "historyError=" in output


@pytest.mark.skipif(sys.platform != "win32", reason="Windows real open-handle gate")
@pytest.mark.parametrize("failure", ["write", "close"])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_failed_history_creation_cleans_or_retains_file_and_releases_lock(
    tmp_path, monkeypatch, failure, cleanup_fails
):
    original = user_files.Directory.create
    handles = []

    def failing_create(parent, name):
        stream, owned = original(parent, name)
        if parent.path.name != "snapshots":
            return stream, owned

        @contextmanager
        def failing_stream():
            try:
                with stream:

                    def write(data):
                        count = stream.write(data)
                        stream.flush()
                        if failure == "write":
                            raise OSError("injected write failure")
                        return count

                    yield SimpleNamespace(fileno=stream.fileno, write=write)
                    raise OSError("injected close failure")
            finally:
                if cleanup_fails:
                    handles.append((parent.path / name).open("rb"))

        return failing_stream(), owned

    monkeypatch.setattr(user_files.Directory, "create", failing_create)
    body = '<screen title="retained"/>'
    try:
        with pytest.raises(
            Fault if cleanup_fails else OSError,
            match="cleanup failed" if cleanup_fails else f"injected {failure} failure",
        ) as caught:
            history.save(
                tmp_path,
                body,
                None,
                app="fixture",
                title="retained",
                deadline=time.monotonic() + 5,
            )
        retained = list(tmp_path.glob(".androidctl/snapshots/*.xml"))
        assert len(retained) == int(cleanup_fails)
        if cleanup_fails:
            assert str(retained[0]) in str(caught.value)
            assert retained[0].read_bytes() == body.encode()
    finally:
        for handle in handles:
            handle.close()
    monkeypatch.setattr(user_files.Directory, "create", original)
    monkeypatch.setattr(history, "MAX_FILES", 1)
    saved, _, _, warning = history.save(
        tmp_path, body, None, app="fixture", title="next", deadline=time.monotonic() + 5
    )
    assert saved.exists()
    assert all(not path.exists() for path in retained)
    assert warning is None


def test_expired_budget_allows_uncontended_history_reclamation(tmp_path, monkeypatch):
    previous = history.save(
        tmp_path, "old", None, app="fixture", title="old", deadline=time.monotonic() + 5
    )
    monkeypatch.setattr(history, "MAX_FILES", 1)
    saved = history.save(
        tmp_path, "new", None, app="fixture", title="new", deadline=time.monotonic() - 1
    )
    assert saved.snapshot_path.read_bytes() == b"new"
    assert not previous.snapshot_path.exists()
    assert saved.warning is None


def test_png_only_history_counts_quota_and_reclaims_group(tmp_path, monkeypatch):
    saved = history.save(
        tmp_path,
        None,
        b"PNG",
        app="fixture",
        title="image",
        deadline=time.monotonic() + 5,
    )
    assert saved.snapshot_path is None
    assert saved.image_path.read_bytes() == b"PNG"
    assert not list(tmp_path.glob(".androidctl/snapshots/*"))
    monkeypatch.setattr(history, "MAX_FILES", 1)
    next_group = history.save(
        tmp_path,
        "new",
        None,
        app="fixture",
        title="body",
        deadline=time.monotonic() + 5,
    )
    assert not saved.image_path.exists()
    assert next_group.snapshot_path.read_bytes() == b"new"
    assert next_group.snapshot_path.name.startswith("000002-")


def test_occupied_history_lock_uses_original_budget(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = tmp_path / ".androidctl"
    private_directory(root)
    lock = LifetimeLock(root / "history.lock")
    assert lock.acquire()
    started = time.monotonic()
    clock = [started]

    def sleep(seconds):
        clock[0] += seconds

    monkeypatch.setattr(
        history, "time", SimpleNamespace(monotonic=lambda: clock[0], sleep=sleep)
    )
    try:
        with pytest.raises(Fault, match="History is busy"):
            history.save(
                tmp_path, "<screen/>", b"PNG", app="", title="", deadline=started + 0.04
            )
    finally:
        lock.close()
    assert not list(tmp_path.glob(".androidctl/shots/*.png"))
