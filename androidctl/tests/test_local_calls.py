import time

import pytest

from androidctl.protocol import TransportFailure
from androidctl.transport import Peer
from connection_test_support import local_server


@pytest.mark.parametrize("failure", [KeyError, TypeError, ValueError, RuntimeError])
async def test_local_dispatch_internal_failure_retains_uncertain_execution(
    tmp_path, failure
):
    effects = []

    async def dispatch(method, args, deadline):
        effects.append(method)
        raise failure("internal failure after observable effect")

    async with local_server(tmp_path / "state", dispatch) as (material, _):
        peer = await Peer.open(material, source="local")
        try:
            with pytest.raises(TransportFailure, match="internal failure") as caught:
                await peer.request("change", deadline=time.monotonic() + 10)
            assert effects == ["change"]
            assert caught.value.problem.stage is None
        finally:
            await peer.close()
