from __future__ import annotations

from pathlib import Path

from tools.release.version_lockstep import format_report, run_checks


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    report = run_checks(repo_root)
    print(format_report(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
