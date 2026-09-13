import pytest

from androidctl import protocol
from androidctl.coordinator import QUERY_METHODS
from ui_test_support import load_wire_contract

WIRE_CONTRACT = load_wire_contract()


def assert_json(expected, actual):
    if isinstance(expected, dict):
        assert isinstance(actual, dict)
        assert expected.keys() == actual.keys()
        for key in expected:
            assert_json(expected[key], actual[key])
    elif isinstance(expected, list):
        assert isinstance(actual, list)
        assert len(expected) == len(actual)
        for left, right in zip(expected, actual, strict=False):
            assert_json(left, right)
    else:
        assert isinstance(expected, bool) == isinstance(actual, bool)
        assert expected == actual


@pytest.mark.parametrize(("boolean", "number"), [(True, 1), (False, 0)])
def test_json_comparison_distinguishes_boolean_from_number(boolean, number):
    with pytest.raises(AssertionError):
        assert_json({"nested": [boolean]}, {"nested": [number]})
    assert_json({"nested": [number]}, {"nested": [float(number)]})


def test_json_comparison_preserves_array_order():
    with pytest.raises(AssertionError):
        assert_json({"nodes": [1, 2]}, {"nodes": [2, 1]})


def test_wire_limits_agree_across_boundaries():
    limits = {
        "MESSAGE_BYTES": protocol.MAX_MESSAGE,
        "JSON_UTF8_BYTES": protocol.JSON_UTF8_BYTES,
        "PAIRING_NAME_UNITS": protocol.PAIRING_NAME_UNITS,
    }
    assert WIRE_CONTRACT["limits"].keys() == limits.keys()
    for device, host in limits.items():
        assert WIRE_CONTRACT["limits"][device] == host, device


def test_query_pools_share_the_expected_control_methods():
    assert WIRE_CONTRACT["queryMethods"] == sorted(QUERY_METHODS | {"pair.register"})
