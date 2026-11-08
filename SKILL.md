---
name: sony-nex6-app-menu-unlock
description: This skill should be used when working on a mainland-China Sony NEX-6 (firmware 1.03, Backup region CX76001_CN2) to restore the hidden Application menu, diagnose Sony-PMCA-RE service/updater mode issues, safely patch Backup property 0x010709ed, prepare libusb-win32 drivers, validate backups, or roll back the change.
agent_created: true
---

# Sony NEX-6 国行应用程序入口恢复

## Overview

Restore only the hidden Application menu flag on a mainland-China NEX-6 by changing Backup property `0x010709ed` from `00` to `01`. Preserve every other persistent setting, avoid firmware flashing, and require byte-level Backup backups plus post-restart verification.

Use the complete field notes in `references/case-study.md` before repeating any hardware operation. Use `references/safety-checklist.md` as the mandatory gate list. Use `references/implementation.md` for code architecture, PMCA interfaces, and environment setup.

## When To Use

Apply this skill when any of the following appears:

- NEX-6 / NEX-6 CN / `CX76001_CN2` / hidden `Application` menu / `0x010709ed`
- `Sony-PMCA-RE` service mode, updater shell, `bk patch`, `BackupPatchDataInterface`
- Zadig + `libusb-win32` driver binding for `054C:056C`, `054C:0336`, `054C:03E2`
- `Senser product info error 128`, blank `Cannot download Backup data`, or service-mode `PDT_READ` timeout
- Post-change persistence verification or rollback of the Application menu flag

Do not use this workflow for other camera models or regions without revalidating all property IDs and region presets.

## System Requirements

This workflow is **Windows-only** and requires physical access to the camera. Do not attempt it on macOS, Linux, WSL, containers, or remote sandboxes. If the host does not match this table, stop and hand off to a Windows machine rather than adapting the procedure.

| Item | Requirement |
|---|---|
| OS | Windows 10/11 x64 (validated on Windows 10 Build 19045) |
| Privileges | Administrator/UAC elevation for PMCA SCSI pass-through, Zadig, and driver restore |
| Python | 64-bit Python 3.13.x (3.13.14 validated); `bootstrap.py` reuses the current interpreter |
| Network | PyPI access during `bootstrap.py`; pre-download wheels for offline machines |
| PowerShell | Windows PowerShell 5.1 or PowerShell 7 for `elevated-preflight.ps1` |
| USB | Local physical USB port, direct connection without a hub; camera powered and awake |
| Camera | NEX-6, firmware 1.03, region `CX76001_CN2`, battery at least 80%, SD/MS card inserted |
| Drivers | `libusb-win32` via Zadig for `054C:056C` / `054C:0336` / `054C:03E2`; restore the Windows default driver afterwards |
| Disk | At least 500 MB free for the workspace, `.venv`, and timestamped backups |
| Clock | Accurate system clock; preflight records expire after 24 hours |
| Terminal | Interactive TTY for `write` / `updater-write`; no piped input, no background execution |

The bundled `assets/tools/*.exe` are Windows x64 binaries. Verify them against `references/tools.md` before execution.

## Hard Constraints

1. Modify only `0x010709ed`. Do not modify `0x01640001`, `0x00e40002`, region strings, model strings, or firmware.
2. Never flash Sony firmware. PMCA updater shell loads a temporary debug payload only.
3. Never run `Setup → Initialize / Factory Reset`; it reloads the CN2 preset and disables the menu again.
4. Never guarantee “cannot brick”. Reduce risk with gates, backups, and abort conditions; state that absolute guarantees are impossible.
5. Require full battery, stable USB, direct connection, and inserted removable media before updater operations.
6. Require a recent machine-read or trusted PMCA-GUI model/firmware preflight record.
7. Never overwrite backup files; use timestamped exclusive writes and SHA256 manifests.
8. Never retry a failed patch blindly. Treat a partial patch/sync as unknown state and re-read first.
9. Guard non-interactive writes; require explicit final confirmation.
10. Use PMCA master source, not v0.18 executable, for `BackupPatchDataInterface` and `bk patch` behavior.

## Workflow

### 1. Classify and Validate

Confirm all of the following:

- NEX-6
- Firmware `1.03`
- Live Backup region `CX76001_CN2`
- Target property `0x010709ed`, length 1, attr `0x01`
- Current value `00` for enable, or `01` for rollback
- USB APK installer `0x01640001` already `01` if only the menu is being restored

Read `references/case-study.md` for the 12-preset firmware verification showing that only CN2 disables the three application-related flags.

### 2. Prepare Environment

Deploy the bundled workspace before touching hardware:

```powershell
python <skill-dir>\scripts\bootstrap.py <workspace>
```

`scripts/bootstrap.py` copies `assets/workspace/` and `assets/tools/` into the target workspace, creates `.venv`, installs `requirements-nex6-lock.txt`, compiles the code, and runs offline tests. The bundle contains final `service.py`, `test_service.py`, PMCA master source, `fwtool.py`, CN2 fixture, Zadig, PMCA GUI/Console, dependency files, and the plan document.

Inspect `references/tools.md` before running bundled executables. Keep all operations in the target workspace unless the user explicitly authorizes another destination.

### 3. Prefer Updater Shell for NEX-6

Do not rely on NEX-6 service mode for whole-Backup export. On this model, service mode can read `getBackupStatus()` and `readBackup(id)` but `getBackupPresetData(True/False)` times out. Attempting to reconstruct Backup from a template weakens the byte-level backup guarantee.

Use updater shell:

```powershell
.\.venv\Scripts\python.exe .\service.py updater-read
```

Then perform another independent `updater-read`. Parse both with `pmca.backup.BackupFile`, compare all properties, and allow only the explicit volatile runtime IDs listed in `references/safety-checklist.md`. Save both backups and manifests.

### 4. Driver and Mode Handling

Recognize the three USB IDs:

| ID | Meaning |
|---|---|
| `054C:056C` | normal Mass Storage |
| `054C:0336` | PMCA service mode |
| `054C:03E2` | updater mode used by NEX-6 |

Use Zadig as administrator to bind `libusb-win32` only to the intended Sony device. Do not alter unrelated USB devices. Use `hold-service` only when `0336` disappears too quickly for driver binding.

Treat a camera that remains at `03E2` after an interrupted updater run as a temporary half-started state. Exit with the updater `complete` command or power-cycle; never start another write from that state.

### 5. External Backup and Final Confirmation

Before the first write, copy PRECHANGE and VERIFY backups to a second physical location, verify SHA256, and record a manifest. Ask for explicit final confirmation showing:

- model/firmware
- region
- exact address
- `00 → 01`
- backup locations and hashes

### 6. Perform Guarded Write

Use the updater backend implementation pattern:

```powershell
.\.venv\Scripts\python.exe .\service.py updater-write 0x010709ed 01
```

or call `write_property()` directly only with an explicit confirmation function.

The write must:

1. Take two fresh pre-write snapshots.
2. Save both with exclusive filenames and manifests.
3. Verify persistent properties and all attr/reset data.
4. Require target value `00`.
5. Take a third snapshot via `BackupPatchDataInterface`.
6. Require exact confirmation.
7. Clear the read-only attr, set the target byte, restore the attr, and update the checksum via upstream two-phase `apply()`.
8. Call `syncBackup()`.
9. Re-read memory and persisted Backup.
10. Audit all properties, allowing only volatile runtime IDs.
11. Save POSTCHANGE backup.
12. Print `before`, `writeBackup OK`, `syncBackup OK`, `after`.

### 7. Verify and Close

1. Power off, disconnect USB, wait, power on.
2. Confirm the Application menu appears and Application List opens.
3. Reconnect USB and run a new `updater-read`.
4. Require `0x010709ed = 01` and valid checksum.
5. Save the final verification backup and copy it to the second backup location.
6. Guide restoration of the normal Windows USB driver for `054C:056C`.
7. Preserve the rollback command and warn against initialization.

## Failure Decision Tree

| Symptom | Root cause | Action |
|---|---|---|
| `CreateFile WinError 5` | non-admin SCSI access or no media | insert media; use elevated PMCA GUI/preflight |
| `No devices found` after service hold | mode timed out and reverted | query present PnP IDs; power-cycle if needed |
| `Senser product info error 128` | unsupported `readHasp` probe on NEX-6 | ignore only this specific error |
| blank `Cannot download Backup data` | service-mode whole-Backup `PDT_READ` timeout | switch to updater-shell path |
| `054C:0336` disappears | upstream waits only 5 seconds | use `hold-service --seconds 180` |
| `054C:03E2` has no driver | updater PID lacks libusb binding | bind `libusb-win32` to `03E2` |
| updater callback has no `start()` | callback receives raw device | wrap with `UsbPlatformBackend(device)` |
| low battery / no response | updater refuses around 50% | stop, charge, power-cycle |
| two Backup SHA256 differ | runtime counters/time fields | compare property maps with volatile whitelist |
| write times out waiting for camera | confirmation not pressed in time | power-cycle to clean state and retry once |
| patch or sync error | partial unknown state | do not retry; re-read and diagnose |

## Resources

- `README.md`: English human-facing quick start
- `README.zh-CN.md`: Simplified Chinese human-facing quick start
- `scripts/bootstrap.py`: deploy the bundled workspace, create the venv, install dependencies, and run offline checks
- `assets/workspace/`: final code, tests, requirement files, PMCA master source, fwtool, CN2 fixture, and plan document
- `assets/tools/`: Zadig, PMCA GUI, PMCA Console
- `references/tools.md`: bundled tool hashes, workspace layout, and files intentionally not bundled
- `references/case-study.md`: full chronological record of every attempt, root cause, evidence, and final success
- `references/safety-checklist.md`: mandatory preflight, stop conditions, volatile IDs, backup, rollback, and driver-restore rules
- `references/implementation.md`: environment, upstream interfaces, `service.py` architecture, test strategy, and exact commands
