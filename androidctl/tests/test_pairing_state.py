import pytest

from androidctl.identity import DeviceIdentity
from androidctl.private_files import private_directory
from androidctl.protocol import Fault
from androidctl.state import HostState
from pairing_test_support import new_device_identity


@pytest.mark.parametrize("alias", [" ", "x" * 101, "bad\nname", "bad\x00name"])
def test_state_rejects_invalid_alias_without_saving_candidate(tmp_path, alias):
    private_directory(tmp_path / "state")
    state = HostState(tmp_path / "state")
    material = new_device_identity()
    with pytest.raises(Fault) as caught:
        state.prepare_pairing(
            DeviceIdentity(material["deviceId"], material["certificate"]),
            material["endpoint"],
            alias,
        )
    assert caught.value.problem.code == "deviceName"
    assert state.devices()["devices"] == []
