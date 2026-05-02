from __future__ import annotations

import json

from androidctl.user_state.config import LauncherConfig, UserConfig, read_user_config


def test_read_user_config_returns_launcher_only_defaults_when_file_missing(
    tmp_path,
) -> None:
    config = read_user_config(tmp_path / "config.json")

    assert config.launcher == LauncherConfig()
    assert config.model_dump() == {
        "launcher": {
            "executable": None,
            "argv": [],
            "env": {},
            "cwd": None,
        }
    }


def test_read_user_config_reads_launcher_only_config(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "launcher": {
                    "executable": "/custom/androidctld",
                    "argv": ["--port", "17171"],
                    "env": {"ANDROIDCTL_DEBUG": "1"},
                    "cwd": "/tmp/daemon",
                },
            }
        ),
        encoding="utf-8",
    )

    config = read_user_config(config_path)

    assert config.launcher == LauncherConfig(
        executable="/custom/androidctld",
        argv=["--port", "17171"],
        env={"ANDROIDCTL_DEBUG": "1"},
        cwd="/tmp/daemon",
    )
    assert config.model_dump() == {
        "launcher": {
            "executable": "/custom/androidctld",
            "argv": ["--port", "17171"],
            "env": {"ANDROIDCTL_DEBUG": "1"},
            "cwd": "/tmp/daemon",
        }
    }


def test_user_config_model_dump_is_launcher_only() -> None:
    assert UserConfig().model_dump() == {
        "launcher": {
            "executable": None,
            "argv": [],
            "env": {},
            "cwd": None,
        }
    }
