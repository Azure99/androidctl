from pathlib import Path

import androidctl_contracts.paths as paths


def test_daemon_state_root_maps_to_dot_androidctl_daemon() -> None:
    assert paths.daemon_state_root(Path("/repo")) == Path("/repo/.androidctl/daemon")
