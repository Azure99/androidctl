import io
import json
from pathlib import Path


def load_wire_contract():
    path = Path(__file__).resolve().parents[2] / "contract/wire-contract.json"
    return json.loads(path.read_text(encoding="utf-8"))


class BrokenStream(io.StringIO):
    def write(self, value):
        raise OSError("broken output")
