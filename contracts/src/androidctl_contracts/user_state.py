from .base import DaemonWireModel


class DaemonInstanceIdentity(DaemonWireModel):
    pid: int
    started_at: str


class ActiveDaemonRecord(DaemonWireModel):
    pid: int
    host: str
    port: int
    token: str
    started_at: str
    workspace_root: str
    owner_id: str

    @property
    def identity(self) -> DaemonInstanceIdentity:
        return DaemonInstanceIdentity(
            pid=self.pid,
            started_at=self.started_at,
        )
