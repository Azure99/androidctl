import base64
import hashlib

from androidctl.protocol import Fault, Result, completed


class Device:
    def __init__(self, body=b"ordinary file\x00\xff", *, delayed=False):
        self.body = body
        self.delayed = delayed
        self.commands = []
        self.last = None
        self.fact = None
        self.cancelled = False

    async def call(
        self,
        command,
        parameters,
        *,
        session,
        deadline,
        before_send=None,
        exchange_seconds=None,
    ):
        if before_send is not None:
            try:
                before_send()
            except Fault as error:
                return Result(
                    command=command,
                    result="not sent",
                    reason=str(error),
                    error=error.problem,
                )

        self.commands.append(command)
        if command == "file.begin":
            return Result(
                command="pull",
                result="still running",
                data={
                    "job": "f-original",
                    "ready": False,
                    "size": 0,
                    "step": None,
                    "resourcesActive": True,
                },
            )
        if command == "file.get":
            if self.fact is not None:
                return self.fact
            return Result(
                command="pull",
                result="still running",
                data={
                    "job": "f-original",
                    "ready": True,
                    "size": len(self.body),
                    "step": self.last,
                    "resourcesActive": True,
                },
            )
        if command == "file.chunk":
            chunk = completed(
                "file chunk",
                {
                    "job": "f-original",
                    "offset": parameters["offset"],
                    "bytes": base64.b64encode(self.body).decode(),
                },
            )
            self.last = {"offset": parameters["offset"], "result": chunk.wire()}
            return (
                Result(
                    command="pull",
                    result="still running",
                    data={
                        "job": "f-original",
                        "ready": False,
                        "size": len(self.body),
                        "step": None,
                        "resourcesActive": True,
                    },
                )
                if self.delayed
                else chunk
            )
        if command == "file.finish":
            self.fact = completed(
                "pull",
                {
                    "job": "f-original",
                    "size": len(self.body),
                    "sha256": hashlib.sha256(self.body).hexdigest(),
                    "resourcesActive": False,
                },
            )
            return self.fact
        if command == "file.cancel":
            self.cancelled = True
            if self.fact is None:
                self.fact = Result(
                    command="pull",
                    result="failed",
                    reason="Transfer cancelled or deadline reached",
                    data={"job": "f-original", "resourcesActive": False},
                )
            return self.fact
        raise AssertionError(command)


def start(manager, tmp_path, *, remote, **options):
    value = manager.start(
        "device",
        "pair",
        "session",
        {
            "direction": "pull",
            "local": str(tmp_path / "final"),
            "remote": "/file",
            **options,
        },
        lambda: None,
        remote=remote,
    )
    return manager.items[value.data["job"]]
