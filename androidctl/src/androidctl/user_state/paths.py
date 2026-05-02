import os
from pathlib import Path


def user_home_dir() -> Path:
    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        return Path(user_profile)
    return Path.home()


def user_state_root() -> Path:
    return user_home_dir() / ".androidctl"
