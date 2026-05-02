from pathlib import Path

from androidctl.user_state.paths import user_home_dir, user_state_root


def test_user_home_dir_prefers_userprofile(monkeypatch) -> None:
    monkeypatch.setenv("USERPROFILE", "/tmp/custom-userprofile")

    assert user_home_dir() == Path("/tmp/custom-userprofile")


def test_user_home_dir_falls_back_to_path_home(monkeypatch) -> None:
    monkeypatch.delenv("USERPROFILE", raising=False)
    sentinel_home = Path("/tmp/path-home-sentinel")
    monkeypatch.setattr(
        Path,
        "home",
        classmethod(lambda cls: sentinel_home),
    )

    assert user_home_dir() == sentinel_home


def test_user_state_root_uses_user_home_dir(monkeypatch) -> None:
    monkeypatch.setenv("USERPROFILE", "/tmp/custom-userprofile")

    assert user_state_root() == Path("/tmp/custom-userprofile/.androidctl")
