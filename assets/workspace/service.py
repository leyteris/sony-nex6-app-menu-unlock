#!/usr/bin/env python3
"""Purpose-built, guarded NEX-6 Backup property reader/writer.

The only supported mutation is 0x010709ed: 00 <-> 01 on a NEX-6
running region CX76001_CN2. No firmware image is read or written here.
"""

import argparse
import contextlib
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import time
import traceback


SCRIPT_DIR = Path.cwd()
PMCA_DIR = Path(__file__).resolve().parent / "Sony-PMCA-RE"
EXPECTED_MODEL = "NEX-6"
EXPECTED_FIRMWARE = "1.03"
PREFLIGHT_MAX_AGE_HOURS = 24
EXPECTED_REGION = "CX76001_CN2"
TARGET_ID = 0x010709ED
MODEL_ID = 0x003E0005
USB_APP_INSTALLER_ID = 0x01640001
# Observed on NEX-6 updater reads: counters/time/run-state properties may change
# between otherwise valid snapshots. These are not user settings and are allowed
# to differ before/after the guarded target-property patch.
VOLATILE_PROP_IDS = frozenset({
    0x0003012C,
    0x000D0027,
    0x00150002,
    0x00580032,
    0x00580034,
    0x00580036,
    0x00580039,
    0x0058003A,
    0x0058003C,
    0x0058003E,
    0x0058003F,
    0x00590013,
    0x00930003,
    0x00A10003,
    0x01250005,
    0x013B0001,
})
SONY_VID = 0x054C
NEX6_SERVICE_PID = 0x0336
MAX_BACKUP_BYTES = 16 * 1024 * 1024
USB_TIMEOUT_MS = 60000


class SafetyError(RuntimeError):
    """A precondition failed before any intended write."""


class WriteStateUnknown(RuntimeError):
    """A write started but verification failed; never blindly retry."""


def utc_stamp():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest().upper()


def load_pmca():
    if not PMCA_DIR.is_dir():
        raise SafetyError(f"Missing upstream PMCA source: {PMCA_DIR}")
    if str(PMCA_DIR) not in sys.path:
        sys.path.insert(0, str(PMCA_DIR))
    from pmca.backup import BackupFile

    return BackupFile


@contextlib.contextmanager
def bundled_libusb_backend():
    """Force PyUSB to use the DLL bundled in libusb-package."""
    try:
        import libusb_package
        import usb.core
    except ImportError as exc:
        raise SafetyError(f"Missing USB dependency: {exc.name}") from exc

    backend = libusb_package.get_libusb1_backend()
    if backend is None:
        raise SafetyError("libusb-package could not load its bundled libusb-1.0 DLL")

    original_find = usb.core.find

    def find_with_bundled_backend(*args, **kwargs):
        kwargs["backend"] = backend
        return original_find(*args, **kwargs)

    usb.core.find = find_with_bundled_backend
    try:
        yield
    finally:
        usb.core.find = original_find


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for stream in self.streams:
            stream.write(data)
            stream.flush()
        return len(data)

    def flush(self):
        for stream in self.streams:
            stream.flush()


@contextlib.contextmanager
def capture_console(log_dir):
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"service-{utc_stamp()}.log"
    with path.open("x", encoding="utf-8", newline="") as stream:
        print(f"LOG = {path}")
        with contextlib.redirect_stdout(Tee(sys.stdout, stream)), contextlib.redirect_stderr(Tee(sys.stderr, stream)):
            yield path


class BackupStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, data, label, metadata=None):
        path = self.root / f"backup-data-{label}-{utc_stamp()}.bin"
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())

        with path.open("rb") as stream:
            saved = stream.read()
        if saved != data:
            raise SafetyError(f"Backup read-back mismatch: {path}")

        digest = sha256_bytes(data)
        record = {
            "file": path.name,
            "size": len(data),
            "sha256": digest,
            "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        if metadata:
            record.update(metadata)
        manifest = path.with_suffix(".json")
        with manifest.open("x", encoding="utf-8") as stream:
            json.dump(record, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        return path, digest


def save_preflight(store, record):
    path = store.root / f"preflight-{utc_stamp()}.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    return path


def load_preflight(store):
    """Load a recent machine-read NEX-6/1.03 record before any service write."""
    records = []
    for path in sorted(store.root.glob("preflight-*.json"), reverse=True):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            created = dt.datetime.fromisoformat(record["created_utc"])
            if created.tzinfo is None:
                created = created.replace(tzinfo=dt.timezone.utc)
            records.append((created, path, record))
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            continue

    if not records:
        raise SafetyError(
            "Missing preflight.json; run `python service.py preflight` before Zadig/service mode"
        )

    created, path, record = records[0]
    age = dt.datetime.now(dt.timezone.utc) - created
    if age.total_seconds() > PREFLIGHT_MAX_AGE_HOURS * 3600:
        raise SafetyError(
            f"Preflight record is older than {PREFLIGHT_MAX_AGE_HOURS}h: {path}"
        )
    if record.get("model") != EXPECTED_MODEL:
        raise SafetyError(f"Preflight model mismatch: {record.get('model')!r}")
    if record.get("firmware_old") != EXPECTED_FIRMWARE:
        raise SafetyError(
            f"Preflight firmware mismatch: expected {EXPECTED_FIRMWARE}, got {record.get('firmware_old')!r}"
        )
    if record.get("firmware_new") != EXPECTED_FIRMWARE:
        raise SafetyError(
            f"Preflight updater target mismatch: expected {EXPECTED_FIRMWARE}, got {record.get('firmware_new')!r}"
        )
    return record


def run_preflight(store):
    """Read model and firmware through PMCA before the camera enters service mode."""
    if str(PMCA_DIR) not in sys.path:
        sys.path.insert(0, str(PMCA_DIR))
    try:
        from pmca.commands.usb import getDevice, importDriver
        from pmca.usb.sony import SonyExtCmdCamera, SonyExtCmdDevice, SonyUpdaterCamera
    except ImportError as exc:
        raise SafetyError(f"Cannot import PMCA preflight support: {exc}") from exc

    with importDriver(None) as driver:
        try:
            device = getDevice(driver)
        except Exception as exc:
            if getattr(exc, "winerror", None) == 5:
                raise SafetyError(
                    "Windows denied access to the Sony mass-storage drive. "
                    "A removable drive with no media often causes this; insert a memory "
                    "card or switch the camera to MTP, then rerun preflight."
                ) from exc
            raise
        if not device or not isinstance(device, SonyExtCmdDevice):
            raise SafetyError(
                "Preflight requires one NEX-6 in MTP or mass storage mode before Zadig"
            )
        info = SonyExtCmdCamera(device).getCameraInfo()
        updater = SonyUpdaterCamera(device)
        updater.init()
        firmware_old, firmware_new = updater.getFirmwareVersion()

    serial_fingerprint = sha256_bytes(info.serial.encode("latin1"))[:16]
    record = {
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "model": info.modelName,
        "model_code": info.modelCode,
        "serial_sha256_16": serial_fingerprint,
        "firmware_old": firmware_old,
        "firmware_new": firmware_new,
        "operation": "read-only camera identification; no firmware write",
    }
    if record["model"] != EXPECTED_MODEL:
        raise SafetyError(f"Preflight model mismatch: {record['model']!r}")
    if record["firmware_old"] != EXPECTED_FIRMWARE or record["firmware_new"] != EXPECTED_FIRMWARE:
        raise SafetyError(
            "Firmware mismatch: expected "
            f"{EXPECTED_FIRMWARE}/{EXPECTED_FIRMWARE}, got {firmware_old}/{firmware_new}"
        )
    path = save_preflight(store, record)
    print(f"MODEL = {record['model']!r}")
    print(f"FIRMWARE = {record['firmware_old']} (updater target {record['firmware_new']})")
    print(f"PREFLIGHT = {path}")
    return record


def decode_c_string(data):
    return data.split(b"\0", 1)[0].decode("latin1").strip()


def parse_backup(raw, live_region=None):
    """Parse and validate all invariants without performing any camera write."""
    if not isinstance(raw, bytes) or len(raw) < 0x100:
        raise SafetyError("Backup is missing or too small")
    if len(raw) > MAX_BACKUP_BYTES:
        raise SafetyError(f"Backup exceeds safety limit: {len(raw)} bytes")
    if raw == bytes(len(raw)) or raw == b"\xff" * len(raw):
        raise SafetyError("Backup is uniformly empty")

    BackupFile = load_pmca()
    try:
        backup = BackupFile(io.BytesIO(raw))
    except Exception as exc:
        raise SafetyError(f"Backup structure/checksum validation failed: {exc}") from exc

    if backup.size > len(raw):
        raise SafetyError("Backup header size exceeds downloaded data")

    region = backup.getRegion()
    if region != EXPECTED_REGION:
        raise SafetyError(f"Region mismatch: expected {EXPECTED_REGION}, got {region!r}")
    if live_region is not None and live_region != region:
        raise SafetyError(f"Live/file region mismatch: {live_region!r} vs {region!r}")

    try:
        model = decode_c_string(backup.getProperty(MODEL_ID).data)
    except Exception as exc:
        raise SafetyError(f"Cannot read model property: {exc}") from exc
    if model != EXPECTED_MODEL:
        raise SafetyError(f"Model mismatch: expected {EXPECTED_MODEL}, got {model!r}")

    try:
        target = backup.getProperty(TARGET_ID)
    except Exception as exc:
        raise SafetyError(f"Cannot read target property 0x{TARGET_ID:08x}: {exc}") from exc
    if len(target.data) != 1:
        raise SafetyError(f"Target size mismatch: expected 1, got {len(target.data)}")
    if target.attr != 0x01:
        raise SafetyError(f"Target attr mismatch: expected 0x01, got 0x{target.attr:02x}")
    if target.data[0] not in (0, 1):
        raise SafetyError(f"Unexpected target value: 0x{target.data[0]:02x}")

    try:
        usb_data = backup.getProperty(USB_APP_INSTALLER_ID).data
        usb_installer = usb_data.hex() if len(usb_data) == 1 else "unexpected-size"
    except Exception:
        usb_installer = "unavailable"

    return {
        "raw": raw,
        "backup": backup,
        "backup_size": backup.size,
        "region": region,
        "live_region": live_region,
        "model": model,
        "target": target.data[0],
        "target_attr": target.attr,
        "usb_app_installer": usb_installer,
        "size": len(raw),
        "sha256": sha256_bytes(raw),
    }


def metadata_for(snapshot):
    return {
        "model": snapshot["model"],
        "region": snapshot["region"],
        "live_region": snapshot["live_region"],
        "backup_size": snapshot["backup_size"],
        "target_id": f"0x{TARGET_ID:08x}",
        "target_value": f"{snapshot['target']:02x}",
        "target_attr": f"0x{snapshot['target_attr']:02x}",
        "usb_app_installer": snapshot["usb_app_installer"],
        "checksum": "OK (parsed by pmca.backup.BackupFile)",
    }


def print_snapshot(snapshot, prefix=""):
    print(f"{prefix}MODEL = {snapshot['model']!r}")
    print(f"{prefix}LIVE REGION = {snapshot['live_region']!r}")
    print(f"{prefix}FILE REGION = {snapshot['region']!r}")
    print(f"{prefix}TARGET 0x{TARGET_ID:08x} = {snapshot['target']:02x} (attr 0x{snapshot['target_attr']:02x})")
    print(f"{prefix}USB APP INSTALLER 0x{USB_APP_INSTALLER_ID:08x} = {snapshot['usb_app_installer']}")
    print(f"{prefix}BACKUP SIZE = {snapshot['size']} bytes (data size {snapshot['backup_size']})")
    print(f"{prefix}BACKUP SHA256 = {snapshot['sha256']}")
    print(f"{prefix}checksum OK")


def live_region(backend):
    try:
        status = backend.getBackupStatus()
        if len(status) < 0x14:
            raise SafetyError("Backup status response is too short")
        return decode_c_string(status[0x14:])
    except SafetyError:
        raise
    except Exception as exc:
        raise SafetyError(f"Cannot read live Backup region: {exc}") from exc


def read_snapshot(backend):
    try:
        raw = backend.getBackupData()
    except Exception as exc:
        raise SafetyError(f"Cannot download Backup data: {exc}") from exc
    snapshot = parse_backup(raw, live_region(backend))
    try:
        live_target = backend.readBackup(TARGET_ID)
    except Exception as exc:
        raise SafetyError(f"Cannot read live target property: {exc}") from exc
    if live_target != bytes([snapshot["target"]]):
        raise SafetyError(
            f"Live/file target mismatch: {live_target.hex()!r} vs {snapshot['target']:02x}"
        )
    return snapshot


def property_map(raw):
    BackupFile = load_pmca()
    backup = BackupFile(io.BytesIO(raw))
    return {
        prop_id: (prop.attr, prop.data, prop.resetData)
        for prop_id, prop in backup.listProperties()
    }


def assert_pre_read_compatible(reference_raw, candidate_raw):
    """Require persistent properties to match while allowing observed volatile IDs."""
    before = property_map(reference_raw)
    after = property_map(candidate_raw)
    if before.keys() != after.keys():
        raise SafetyError("Backup property IDs differ between pre-write reads")

    unexpected = []
    volatile_changed = set()
    for prop_id in before:
        before_attr, before_data, before_reset = before[prop_id]
        after_attr, after_data, after_reset = after[prop_id]
        if before_attr != after_attr or before_reset != after_reset:
            unexpected.append(f"0x{prop_id:08x} attr/reset")
        if before_data == after_data:
            continue
        if prop_id in VOLATILE_PROP_IDS:
            volatile_changed.add(prop_id)
        else:
            unexpected.append(
                f"0x{prop_id:08x} data {before_data.hex()}->{after_data.hex()}"
            )
    if unexpected:
        raise SafetyError("Unexpected pre-write Backup differences: " + "; ".join(unexpected[:12]))
    return volatile_changed


def assert_only_target_changed(before_raw, after_raw, expected_new):
    """Audit all persistent Backup properties; only volatile runtime fields may differ."""
    before = property_map(before_raw)
    after = property_map(after_raw)
    if before.keys() != after.keys():
        raise WriteStateUnknown("Backup property IDs changed after write")

    unexpected = []
    volatile_changed = set()
    target_seen = False
    for prop_id in before:
        before_attr, before_data, before_reset = before[prop_id]
        after_attr, after_data, after_reset = after[prop_id]
        if before_attr != after_attr or before_reset != after_reset:
            unexpected.append(f"0x{prop_id:08x} attr/reset")
        if before_data == after_data:
            continue
        if prop_id == TARGET_ID and after_data == bytes([expected_new]):
            target_seen = True
        elif prop_id in VOLATILE_PROP_IDS:
            volatile_changed.add(prop_id)
        else:
            unexpected.append(
                f"0x{prop_id:08x} data {before_data.hex()}->{after_data.hex()}"
            )
    if not target_seen:
        unexpected.append(f"0x{TARGET_ID:08x} did not change to {expected_new:02x}")
    if unexpected:
        raise WriteStateUnknown("Unexpected Backup differences: " + "; ".join(unexpected[:12]))
    print(
        f"PROPERTY AUDIT OK: persistent properties unchanged except "
        f"0x{TARGET_ID:08x} -> {expected_new:02x}"
    )
    if volatile_changed:
        print(
            "VOLATILE RUNTIME FIELDS CHANGED (allowed): "
            + ", ".join(f"0x{x:08x}" for x in sorted(volatile_changed))
        )


def confirmation_text(snapshot, new_value):
    return (
        f"{EXPECTED_MODEL} {EXPECTED_FIRMWARE} {snapshot['region']} "
        f"{TARGET_ID:08x} {snapshot['target']:02x}>{new_value:02x} "
        f"{snapshot['sha256'][:12]}"
    )


def persisted_backup(backend):
    """Read the persisted Backup copy after sync, for senser or updater backends."""
    try:
        camera = getattr(backend, "dev", None)
        preset_getter = getattr(camera, "getBackupPresetData", None)
        if callable(preset_getter):
            return preset_getter(False)
        if hasattr(backend, "getBackupData"):
            return backend.getBackupData()
    except Exception as exc:
        raise WriteStateUnknown(f"Cannot read persisted Backup data after sync: {exc}") from exc
    raise WriteStateUnknown("This backend does not expose a persisted Backup read")


def write_property(backend, address, new_value, store, input_func=input, preflight=None):
    if address != TARGET_ID:
        raise SafetyError(f"Address refused: only 0x{TARGET_ID:08x} is supported")
    if new_value not in (0, 1):
        raise SafetyError("Value refused: only 00 or 01 is supported")
    if preflight is not None:
        if preflight.get("model") != EXPECTED_MODEL:
            raise SafetyError(f"Preflight model mismatch: {preflight.get('model')!r}")
        if preflight.get("firmware_old") != EXPECTED_FIRMWARE:
            raise SafetyError("Preflight firmware does not match this guarded write")

    first = read_snapshot(backend)
    second = read_snapshot(backend)
    first_path, _ = store.save(first["raw"], "PRECHANGE", metadata_for(first))
    second_path, _ = store.save(second["raw"], "PRECHANGE-VERIFY", metadata_for(second))
    print(f"PRECHANGE = {first_path}")
    print(f"PRECHANGE VERIFY = {second_path}")
    volatile_seen = assert_pre_read_compatible(first["raw"], second["raw"])
    if volatile_seen:
        print(
            "VOLATILE RUNTIME FIELDS DIFFER (allowed): "
            + ", ".join(f"0x{x:08x}" for x in sorted(volatile_seen))
        )

    expected_old = 1 - new_value
    print_snapshot(first)
    if first["target"] == new_value:
        print(f"already: {new_value:02x}; no write needed")
        return False
    if first["target"] != expected_old:
        raise SafetyError(
            f"Before-value mismatch: expected {expected_old:02x}, got {first['target']:02x}"
        )

    try:
        from pmca.platform.backup import BackupPatchDataInterface

        patch = BackupPatchDataInterface(backend)
    except Exception as exc:
        raise SafetyError(f"Cannot initialize third pre-write snapshot: {exc}") from exc
    third_volatile_seen = assert_pre_read_compatible(first["raw"], patch.file.getvalue())
    volatile_seen.update(third_volatile_seen)

    expected_confirmation = confirmation_text(first, new_value)
    print("Firmware 1.03 must have been confirmed separately with pmca-console info.")
    print(f"Type exactly: {expected_confirmation}")
    if input_func("confirmation> ") != expected_confirmation:
        raise SafetyError("Confirmation mismatch; no data was written")

    print(f"before: {first['target']:02x}")
    try:
        patch.writeProp(TARGET_ID, bytes([new_value]))
        patch.apply()
    except Exception as exc:
        raise WriteStateUnknown(
            f"Backup patch failed; camera state is UNKNOWN. Do not retry blindly: {exc}"
        ) from exc
    print("writeBackup OK")

    try:
        backend.syncBackup()
    except Exception as exc:
        raise WriteStateUnknown(
            f"Backup sync failed; camera state is UNKNOWN. Do not retry blindly: {exc}"
        ) from exc
    print("syncBackup OK")

    after_memory = read_snapshot(backend)
    if after_memory["target"] != new_value:
        raise WriteStateUnknown(
            f"Post-write memory value is {after_memory['target']:02x}, expected {new_value:02x}"
        )

    persisted_raw = persisted_backup(backend)
    try:
        after_disk = parse_backup(persisted_raw, live_region=after_memory["region"])
    except SafetyError as exc:
        raise WriteStateUnknown(f"Persisted Backup validation failed: {exc}") from exc
    if after_disk["target"] != new_value:
        raise WriteStateUnknown(
            f"Persisted value is {after_disk['target']:02x}, expected {new_value:02x}"
        )

    assert_only_target_changed(first["raw"], after_memory["raw"], new_value)
    assert_only_target_changed(first["raw"], persisted_raw, new_value)
    post_path, _ = store.save(persisted_raw, "POSTCHANGE", metadata_for(after_disk))
    print(f"POSTCHANGE = {post_path}")
    print(f"after: {new_value:02x}")
    print("PERSISTENCE CHECK OK (service-session memory and synced copy)")
    print("A full camera power cycle and a later read are still required.")
    return True


def require_active_service_device():
    """A write must never be the command that initiates service-mode switching."""
    try:
        from pmca.commands.usb import importDriver, listDevices
        from pmca.usb.sony import SonySenserDevice
    except ImportError as exc:
        raise SafetyError(f"Cannot import PMCA service support: {exc}") from exc

    with importDriver("libusb") as driver:
        devices = list(listDevices(driver, True))
        if len(devices) != 1 or not isinstance(devices[0], SonySenserDevice):
            raise SafetyError(
                "write requires one camera already in service mode (054C:0336); run read first"
            )
        vid, pid = devices[0].driver.getId()
        if (vid, pid) != (SONY_VID, NEX6_SERVICE_PID):
            raise SafetyError(f"Unexpected service USB ID: {vid:04x}:{pid:04x}")


def hold_service_mode(seconds=180):
    """Switch to service mode and keep it alive while Zadig binds 054C:0336."""
    if not 10 <= seconds <= 600:
        raise SafetyError("hold-service seconds must be between 10 and 600")
    if str(PMCA_DIR) not in sys.path:
        sys.path.insert(0, str(PMCA_DIR))

    with bundled_libusb_backend():
        try:
            from pmca.commands.usb import getDevice, importDriver
            from pmca.usb.driver.generic import GenericUsbDriver
            from pmca.usb.sony import SonyMscExtCmdDevice, SonySenserAuthDevice
        except ImportError as exc:
            raise SafetyError(f"Cannot import PMCA service support: {exc}") from exc

        with importDriver("libusb") as driver:
            device = getDevice(driver)
            if not device or not isinstance(device, SonyMscExtCmdDevice):
                raise SafetyError("hold-service requires one camera in mass storage mode")
            if not isinstance(device.driver, GenericUsbDriver):
                raise SafetyError("054C:056C must use libusb-win32 before hold-service")
            print("Switching to service mode and holding it open...")
            auth = SonySenserAuthDevice(device.driver)
            auth.start()
            try:
                auth.authenticate()
                print("SERVICE MODE IS ACTIVE: bind 054C:0336 in Zadig now.")
                print("Do not unplug USB and do not close the camera.")
                deadline = time.monotonic() + seconds
                last_notice = -15
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    whole = int(remaining)
                    if whole % 15 == 0 and whole != last_notice:
                        print(f"service-mode hold: {whole}s remaining")
                        last_notice = whole
                    time.sleep(min(1, remaining))
            finally:
                print("Releasing the service-mode hold...")
                auth.stop()
        print("hold-service finished; the camera may return to mass storage mode.")


def configure_service_backend(backend):
    """Raise short libusb timeouts for slow service-mode Backup transfers."""
    try:
        generic_driver = backend.dev.dev.driver
        generic_driver.backend.dev.default_timeout = USB_TIMEOUT_MS
    except AttributeError as exc:
        raise SafetyError(f"Cannot configure USB timeout on service backend: {exc}") from exc


def run_service(operation, for_write=False):
    if str(PMCA_DIR) not in sys.path:
        sys.path.insert(0, str(PMCA_DIR))
    with bundled_libusb_backend():
        if for_write:
            require_active_service_device()
        try:
            from pmca.commands.usb import senserShellCommand
            from pmca.platform.backend.senser import SenserPlatformBackend
        except ImportError as exc:
            raise SafetyError(f"Cannot import PMCA service mode: {exc}") from exc

        called = False

        def complete(camera):
            nonlocal called
            called = True
            vid, pid = camera.dev.driver.getId()
            if (vid, pid) != (SONY_VID, NEX6_SERVICE_PID):
                raise SafetyError(f"Unexpected service USB ID: {vid:04x}:{pid:04x}")
            backend = SenserPlatformBackend(camera)
            configure_service_backend(backend)
            try:
                backend.start()
            except Exception as exc:
                if "Senser product info error 128" not in str(exc):
                    raise
                print("Ignoring unsupported Senser product-info probe (error 128).")
            try:
                operation(backend)
            finally:
                backend.stop()

        senserShellCommand(driverName="libusb", complete=complete)
        if not called:
            raise SafetyError(
                "Service session was not established. If 054C:0336 appeared, bind "
                "libusb-win32 to it before retrying read; never blindly retry write."
            )


def run_updater(operation):
    """Enter a clean PMCA updater shell and run one guarded operation."""
    if str(PMCA_DIR) not in sys.path:
        sys.path.insert(0, str(PMCA_DIR))
    with bundled_libusb_backend():
        try:
            from pmca.commands.usb import updaterShellCommand
            from pmca.platform.backend.usb import UsbPlatformBackend
            from pmca.usb.sony import SonyUpdaterCamera
        except ImportError as exc:
            raise SafetyError(f"Cannot import PMCA updater support: {exc}") from exc

        called = False

        def complete(device):
            nonlocal called
            called = True
            backend = UsbPlatformBackend(device)
            operation_error = None
            stop_error = None
            try:
                print("Updater shell payload accepted; starting guarded operation...")
                backend.start()
                operation(backend)
            except BaseException as exc:
                operation_error = exc
            finally:
                try:
                    backend.stop()
                except BaseException as exc:
                    stop_error = exc

            if operation_error is not None:
                try:
                    SonyUpdaterCamera(device).complete()
                except BaseException:
                    pass
                raise operation_error
            if stop_error is not None:
                try:
                    SonyUpdaterCamera(device).complete()
                except BaseException:
                    pass
                raise stop_error

        print("The camera may ask for confirmation; follow the camera screen.")
        updaterShellCommand(model=EXPECTED_MODEL, driverName="libusb", complete=complete)
        if not called:
            raise SafetyError(
                "Updater session was not established; no Backup operation was performed"
            )


def parse_address(text):
    try:
        return int(text.removeprefix("0x").removeprefix("0X"), 16)
    except ValueError as exc:
        raise SafetyError(f"Invalid hexadecimal address: {text!r}") from exc


def parse_byte(text):
    if not re.fullmatch(r"[0-9a-fA-F]{2}", text):
        raise SafetyError("Value must be exactly two hexadecimal digits: 00 or 01")
    return int(text, 16)


def build_parser():
    parser = argparse.ArgumentParser(
        description="Guarded NEX-6 CN2 application-menu Backup tool; no firmware flashing"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("preflight", help="Machine-read NEX-6/1.03 check before Zadig/service mode")
    sub.add_parser("read", help="Read and archive one Backup snapshot; may switch to service mode")
    hold_parser = sub.add_parser("hold-service", help="Hold service mode open while Zadig binds 054C:0336")
    hold_parser.add_argument("--seconds", type=int, default=180)
    sub.add_parser("updater-read", help="Clean updater-shell read and archive one Backup snapshot")
    inspect_parser = sub.add_parser("inspect", help="Offline-only Backup file inspection")
    inspect_parser.add_argument("file", type=Path)
    write_parser = sub.add_parser("write", help="Guarded 00<->01 target change; service mode required")
    write_parser.add_argument("address")
    write_parser.add_argument("value")
    updater_write_parser = sub.add_parser("updater-write", help="Guarded 00<->01 target change via updater shell")
    updater_write_parser.add_argument("address")
    updater_write_parser.add_argument("value")
    return parser


def run_online(args):
    if args.command == "preflight":
        store = BackupStore(SCRIPT_DIR / "backup")
        run_preflight(store)
        return 0

    if args.command == "hold-service":
        hold_service_mode(args.seconds)
        return 0

    if args.command == "updater-read":
        store = BackupStore(SCRIPT_DIR / "backup")

        def updater_read_operation(backend):
            snapshot = read_snapshot(backend)
            path, _ = store.save(snapshot["raw"], "READ-UPDATER", metadata_for(snapshot))
            print_snapshot(snapshot)
            print(f"BACKUP FILE = {path}")

        run_updater(updater_read_operation)
        return 0

    if args.command == "read":
        store = BackupStore(SCRIPT_DIR / "backup")

        def operation(backend):
            snapshot = read_snapshot(backend)
            path, _ = store.save(snapshot["raw"], "READ", metadata_for(snapshot))
            print_snapshot(snapshot)
            print(f"BACKUP FILE = {path}")

        run_service(operation)
        return 0

    if args.command == "write":
        address = parse_address(args.address)
        value = parse_byte(args.value)
        if address != TARGET_ID or value not in (0, 1):
            raise SafetyError(
                f"Refused: only write 0x{TARGET_ID:08x} 00 or 01 is supported"
            )
        if not sys.stdin.isatty():
            raise SafetyError("write requires an interactive terminal; no non-interactive bypass")
        store = BackupStore(SCRIPT_DIR / "backup")
        preflight = load_preflight(store)
        print(
            f"PREFLIGHT OK: {preflight['model']} {preflight['firmware_old']} "
            f"(record {preflight['created_utc']})"
        )
        run_service(
            lambda backend: write_property(
                backend, address, value, store, preflight=preflight
            ),
            for_write=True,
        )
        return 0

    if args.command == "updater-write":
        address = parse_address(args.address)
        value = parse_byte(args.value)
        if address != TARGET_ID or value not in (0, 1):
            raise SafetyError(
                f"Refused: only updater-write 0x{TARGET_ID:08x} 00 or 01 is supported"
            )
        if not sys.stdin.isatty():
            raise SafetyError("updater-write requires an interactive terminal; no non-interactive bypass")
        store = BackupStore(SCRIPT_DIR / "backup")
        preflight = load_preflight(store)
        print(
            f"PREFLIGHT OK: {preflight['model']} {preflight['firmware_old']} "
            f"(record {preflight['created_utc']})"
        )
        run_updater(
            lambda backend: write_property(
                backend, address, value, store, preflight=preflight
            )
        )
        return 0

    raise SafetyError("Unsupported command")


def report_exception(exc):
    if isinstance(exc, (SafetyError, WriteStateUnknown)):
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3 if isinstance(exc, WriteStateUnknown) else 2
    print(f"UNEXPECTED ERROR: {exc}", file=sys.stderr)
    traceback.print_exc()
    return 1


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "inspect":
            with args.file.open("rb") as stream:
                raw = stream.read()
            snapshot = parse_backup(raw)
            print_snapshot(snapshot)
            return 0

        with capture_console(SCRIPT_DIR / "logs"):
            try:
                return run_online(args)
            except Exception as exc:
                return report_exception(exc)
    except Exception as exc:
        return report_exception(exc)


if __name__ == "__main__":
    sys.exit(main())
