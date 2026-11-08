# Sony NEX-6 Application Menu Unlock Skill

**English** | [简体中文](README.zh-CN.md)

> **Platform: Windows 10/11 x64 only.** Physical USB access and administrator rights are required.

This package contains a reusable AI-agent skill, the final guarded implementation, a reproducible Python environment definition, bundled PMCA/fwtool source, Windows tools, and the complete field notes from a successful mainland-China Sony NEX-6 unlock.

`SKILL.md` is the machine-facing skill definition. This README is a human-facing quick start.

## What It Does

This workflow restores the hidden "Application" menu on a mainland-China NEX-6 with firmware 1.03 and Backup region `CX76001_CN2` by changing exactly one Backup property:

```text
0x010709ed: 00 -> 01
```

It does not modify:

- firmware
- region strings
- model strings
- `0x01640001`
- `0x00e40002`
- any other persistent Backup setting

The final verified result was:

- "Application" appeared after a full power cycle
- a later independent updater read confirmed `0x010709ed = 01`

## When To Use It

Use this package only when all target facts match or have been revalidated:

- Sony NEX-6
- Firmware 1.03
- Backup region `CX76001_CN2`
- Target property `0x010709ed`, length 1, attr `0x01`
- The USB application installer flag is already unlocked, or a separate validated workflow has handled it

Do not apply this workflow unchanged to another model or region.

## System Requirements

This workflow is **Windows-only** and requires physical access to the camera. It cannot run on macOS, Linux, WSL, containers, or remote sandboxes.

| Item | Requirement |
|---|---|
| OS | Windows 10/11 x64 (validated on Windows 10 Build 19045) |
| Privileges | Administrator/UAC for PMCA SCSI pass-through, Zadig, and driver restore |
| Python | 64-bit Python 3.13.x (3.13.14 validated) |
| Network | PyPI access during `bootstrap.py`; pre-download wheels for offline machines |
| PowerShell | Windows PowerShell 5.1 or PowerShell 7 for `elevated-preflight.ps1` |
| USB | Local direct USB port, no hub; camera powered and awake |
| Camera | NEX-6, firmware 1.03, region `CX76001_CN2`, battery at least 80%, SD/MS card inserted |
| Drivers | `libusb-win32` via Zadig for `054C:056C` / `054C:0336` / `054C:03E2` |
| Disk | At least 500 MB free for the workspace, `.venv`, and backups |
| Clock | Accurate system clock; preflight records expire after 24 hours |
| Terminal | Interactive terminal for writes; no piped input or background execution |

`assets/tools/*.exe` are Windows x64 binaries; verify their SHA256 values against `references/tools.md` before running them.

## Quick Start

From the skill directory, pass the target workspace path:

```powershell
python .\scripts\bootstrap.py <workspace>
cd <workspace>
.\.venv\Scripts\python.exe -m unittest -v test_service.py
.\.venv\Scripts\python.exe .\service.py --help
```

`bootstrap.py` will:

1. Copy `assets/workspace/` into the target workspace.
2. Copy Windows tools into `tools/`.
3. Create `.venv`.
4. Install `requirements-nex6-lock.txt`.
5. Compile `service.py` and `test_service.py`.
6. Run the offline test suite.

Use `--force` to overwrite bundled files that already exist in the target workspace.

## Recommended Hardware Workflow

1. Charge the camera fully.
2. Insert a readable SD/MS card.
3. Use a direct USB connection; avoid hubs.
4. Run `tools\pmca-gui-v0.18-win.exe` as administrator and confirm NEX-6 / 1.03.
5. Use `tools\zadig-2.9.exe` as administrator to bind `libusb-win32` only to the intended Sony USB IDs:
   - `054C:056C` for Mass Storage
   - `054C:0336` for service mode, if needed
   - `054C:03E2` for updater mode
6. Run two independent read-only snapshots:

```powershell
.\.venv\Scripts\python.exe .\service.py updater-read
.\.venv\Scripts\python.exe .\service.py updater-read
```

7. Copy both backups and manifests to a second physical location and verify SHA256.
8. Perform the single guarded write:

```powershell
.\.venv\Scripts\python.exe .\service.py updater-write 0x010709ed 01
```

9. Power off, disconnect USB, wait, power on, and confirm the "Application" menu.
10. Reconnect USB and run one more `updater-read` to verify persistence.
11. Restore the normal Windows USB driver for `054C:056C`.

## Important Implementation Note

NEX-6 service mode can read `getBackupStatus()` and individual Backup properties, but its whole-Backup `PDT_READ` command times out. Do not reconstruct a Backup from a template when byte-level backup is required. The successful path is the PMCA updater shell, which can export the entire `Backup.bin` and supports `BackupPatchDataInterface`.

## Safety Rules

- Never guarantee "cannot brick". Use gates, backups, verification, and abort conditions instead.
- Never run camera `Setup -> Initialize / Factory Reset`; it reloads the CN2 preset.
- Never retry blindly after patch or sync failure. Re-read first.
- Never overwrite backup files.
- Never use another camera's Backup or another region preset for recovery.
- Stop if the battery is low, a USB device is not present, or the camera is stuck in a half-started updater state.
- Treat two different raw SHA256 values as normal only when the property-level diff is limited to the documented volatile runtime IDs.

## Rollback

```powershell
.\.venv\Scripts\python.exe .\service.py updater-write 0x010709ed 00
```

After rollback, perform a full power cycle and a new read-only verification.

## Package Layout

```text
SKILL.md                    Skill definition (machine-facing)
README.md                   English quick start
README.zh-CN.md             Simplified Chinese quick start
scripts/bootstrap.py        Deploy workspace, install environment, run tests
assets/workspace/           Final service.py, tests, PMCA source, fwtool, fixture
assets/tools/               Zadig, PMCA GUI, PMCA Console
references/case-study.md    Full attempt/root-cause/final-result record
references/safety-checklist.md
references/implementation.md
references/tools.md
```

## Not Bundled

- `.venv/`: created by `bootstrap.py`
- `Update_NEX6V103.exe`: about 205 MB; its hash, signature, and official download reference are recorded in `references/tools.md`

## More Detail

Read these before operating on a camera:

- `SKILL.md`
- `references/case-study.md`
- `references/safety-checklist.md`
- `references/implementation.md`
- `references/tools.md`
