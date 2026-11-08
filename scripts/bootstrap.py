#!/usr/bin/env python3
"""Deploy the bundled NEX-6 workspace, create a venv, and run offline checks."""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_BUNDLE = ROOT / "assets" / "workspace"
TOOLS_BUNDLE = ROOT / "assets" / "tools"
REQUIRED_TOOLS = (
    "zadig-2.9.exe",
    "pmca-gui-v0.18-win.exe",
    "pmca-console-v0.18-win.exe",
)
MIN_PYTHON = (3, 9)


def check_system():
    """Fail fast on hosts that cannot run the Windows hardware workflow."""
    if os.name != "nt":
        raise SystemExit(
            "This skill requires Windows 10/11 x64 with administrator access, "
            "physical USB, and an interactive terminal; the current OS is not Windows."
        )
    if sys.version_info < MIN_PYTHON:
        raise SystemExit(
            f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required; "
            f"64-bit Python 3.13.x is recommended. Current: {sys.version.split()[0]}"
        )
    missing = [name for name in REQUIRED_TOOLS if not (TOOLS_BUNDLE / name).is_file()]
    if missing:
        raise SystemExit(
            "Bundled Windows tools are missing: "
            + ", ".join(missing)
            + f". Expected under {TOOLS_BUNDLE}."
        )


def copy_bundle(source: Path, destination: Path, force: bool):
    copied = 0
    skipped = 0
    destination.mkdir(parents=True, exist_ok=True)
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        target = destination / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if target.exists() and not force:
            skipped += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied += 1
    return copied, skipped


def run(command, cwd=None):
    print("RUN:", " ".join(str(part) for part in command), flush=True)
    subprocess.check_call([str(part) for part in command], cwd=str(cwd) if cwd else None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", nargs="?", default=".", help="Workspace directory to create or update")
    parser.add_argument("--force", action="store_true", help="Overwrite files already present in the target")
    parser.add_argument("--skip-tests", action="store_true", help="Skip compile and unittest checks")
    args = parser.parse_args()

    check_system()
    target = Path(args.target).resolve()
    target.mkdir(parents=True, exist_ok=True)

    copied, skipped = copy_bundle(WORKSPACE_BUNDLE, target, args.force)
    tools_copied, tools_skipped = copy_bundle(TOOLS_BUNDLE, target / "tools", args.force)
    print(f"WORKSPACE_FILES copied={copied} skipped={skipped}")
    print(f"TOOLS_FILES copied={tools_copied} skipped={tools_skipped}")

    venv_dir = target / ".venv"
    if not venv_dir.exists():
        run([sys.executable, "-m", "venv", venv_dir])

    python = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists():
        raise SystemExit(f"Python executable not found in venv: {python}")

    requirements = target / "requirements-nex6-lock.txt"
    run([python, "-m", "pip", "install", "-r", requirements])

    if not args.skip_tests:
        run([python, "-m", "py_compile", target / "service.py", target / "test_service.py"])
        run([python, "-m", "unittest", "-q", "test_service.py"], cwd=target)

    print("BOOTSTRAP_OK")
    print(f"WORKSPACE={target}")
    print(f"PYTHON={python}")
    print("TOOLS=" + str(target / "tools"))
    print("NEXT=Open tools\\pmca-gui-v0.18-win.exe as administrator for preflight, then run service.py updater-read.")


if __name__ == "__main__":
    main()
