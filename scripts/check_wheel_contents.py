from __future__ import annotations

import argparse
import configparser
import csv
from pathlib import Path
from zipfile import ZipFile


def _single(paths: list[Path], label: str) -> Path:
    if len(paths) != 1:
        raise SystemExit(f"expected exactly one {label}, found {len(paths)}")
    return paths[0]


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the fable-lite wheel RECORD.")
    parser.add_argument("--wheel-dir", type=Path, default=Path("dist"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()

    wheel = _single(sorted(args.wheel_dir.glob("*.whl")), "wheel")
    root = args.root.resolve()
    expected_smtw = {f"smtw/{path.name}" for path in (root / "smtw").glob("*.py")}

    with ZipFile(wheel) as archive:
        names = archive.namelist()
        record_name = _single(
            [Path(name) for name in names if name.endswith(".dist-info/RECORD")],
            "RECORD",
        ).as_posix()
        entry_points_name = _single(
            [Path(name) for name in names if name.endswith(".dist-info/entry_points.txt")],
            "entry_points.txt",
        ).as_posix()
        record_rows = csv.reader(archive.read(record_name).decode("utf-8").splitlines())
        record_paths = {row[0] for row in record_rows if row}
        entry_points_text = archive.read(entry_points_name).decode("utf-8")

    wheel_smtw = {
        path for path in record_paths if path.startswith("smtw/") and path.endswith(".py")
    }
    wheel_shim = {path for path in record_paths if path.startswith("fable_lite/")}
    if wheel_smtw != expected_smtw:
        missing = sorted(expected_smtw - wheel_smtw)
        unexpected = sorted(wheel_smtw - expected_smtw)
        raise SystemExit(f"canonical package mismatch: missing={missing}, unexpected={unexpected}")
    if wheel_shim != {"fable_lite/__init__.py"}:
        raise SystemExit(f"legacy shim contains unexpected files: {sorted(wheel_shim)}")

    entry_points = configparser.ConfigParser()
    entry_points.read_string(entry_points_text)
    scripts = entry_points["console_scripts"]
    expected_target = "smtw.cli:main"
    if scripts.get("smtw") != expected_target or scripts.get("fable-lite") != expected_target:
        raise SystemExit(f"unexpected console scripts: {dict(scripts)}")

    print(
        f"wheel-record-ok wheel={wheel.name} "
        f"smtw_modules={len(wheel_smtw)} shim_files={len(wheel_shim)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
