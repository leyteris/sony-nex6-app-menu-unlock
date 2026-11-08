import datetime
import io
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

import service


service.load_pmca()
from pmca.backup import BackupFile, BackupHeader, PropertyTableEntryV4, SubsystemTableEntry


def make_backup(
    target=0,
    region=service.EXPECTED_REGION,
    model=service.EXPECTED_MODEL,
    usb_installer=1,
    target_attr=1,
    volatile_value=5,
):
    """Build a valid synthetic revision-4 Backup.bin using upstream structs."""
    model_subsystem = 0x003E
    target_subsystem = service.TARGET_ID >> 16
    usb_subsystem = service.USB_APP_INSTALLER_ID >> 16
    volatile_subsystem = 0x0058
    volatile_id = 0x00580036
    num_subsystems = usb_subsystem + 1

    target_count = (service.TARGET_ID & 0xFFFF) + 1
    model_start = 0
    target_start = model_start + 6
    usb_start = target_start + target_count
    volatile_start = usb_start + 2
    volatile_count = (volatile_id & 0xFFFF) + 1
    total_properties = volatile_start + volatile_count

    subsystem_rows = []
    for subsystem in range(num_subsystems):
        count = 0
        ptr = 0
        if subsystem == model_subsystem:
            count, ptr = 6, model_start
        elif subsystem == target_subsystem:
            count, ptr = target_count, target_start
        elif subsystem == usb_subsystem:
            count, ptr = 2, usb_start
        elif subsystem == volatile_subsystem:
            count, ptr = volatile_count, volatile_start
        subsystem_rows.append(SubsystemTableEntry.pack(numProperties=count, ptr=ptr))

    properties = [[0, 0xFFFFFFFF] for _ in range(total_properties)]
    data_offset = (
        BackupHeader.size
        + num_subsystems * SubsystemTableEntry.size
        + total_properties * PropertyTableEntryV4.size
    )

    model_data = model.encode("latin1").ljust(16, b"\0")
    target_data = bytes([target])
    usb_data = bytes([usb_installer])
    volatile_data = bytes([volatile_value])
    payload = model_data + target_data + usb_data + volatile_data

    model_offset = data_offset
    target_offset = model_offset + len(model_data)
    usb_offset = target_offset + len(target_data)
    volatile_offset = usb_offset + len(usb_data)
    properties[model_start + (service.MODEL_ID & 0xFFFF)] = [1, (16 << 24) | model_offset]
    properties[target_start + (service.TARGET_ID & 0xFFFF)] = [
        target_attr,
        (1 << 24) | target_offset,
    ]
    properties[usb_start + (service.USB_APP_INSTALLER_ID & 0xFFFF)] = [
        0,
        (1 << 24) | usb_offset,
    ]
    properties[volatile_start + (volatile_id & 0xFFFF)] = [
        0,
        (1 << 24) | volatile_offset,
    ]

    header = BackupHeader.pack(
        magic=0,
        cookie=0,
        writeComp=0,
        revision=b"BK4\0",
        numSubsystems=num_subsystems,
        numProperties=total_properties,
        dataOffset=data_offset,
        dataSize=len(payload),
        checksum=0,
        id1=0,
        version=b"TEST".ljust(0x20, b"\0"),
        region=region.encode("latin1").ljust(0x20, b"\0"),
    )
    property_rows = b"".join(
        PropertyTableEntryV4.pack(attr=attr, ptr=ptr) for attr, ptr in properties
    )
    raw = header + b"".join(subsystem_rows) + property_rows + payload
    checksum = sum(bytearray(raw[:0x20] + raw[0x24:]))
    raw = raw[:0x20] + struct.pack("<I", checksum) + raw[0x24:]

    parsed = BackupFile(io.BytesIO(raw))
    assert parsed.getProperty(service.TARGET_ID).data == target_data
    assert parsed.getRegion() == region
    return raw


def change_property(raw, prop_id, data=None, attr=None):
    stream = io.BytesIO(raw)
    backup = BackupFile(stream)
    if data is not None:
        backup.setProperty(prop_id, data)
    if attr is not None:
        backup.setPropertyAttr(prop_id, attr)
    backup.updateChecksum()
    return stream.getvalue()


class FakeCamera:
    def __init__(self, backend):
        self.backend = backend

    def getBackupPresetData(self, fromMemory):
        return self.backend.memory if fromMemory else self.backend.disk


class FakeBackend:
    def __init__(self, raw):
        self.memory = raw
        self.disk = raw
        self.dev = FakeCamera(self)
        self.read_queue = []
        self.set_calls = 0
        self.sync_calls = 0
        self.fail_set_at = None
        self.fail_sync = False
        self.mutate_on_sync = False
        self.mutate_on_sync_property = None

    def getBackupData(self):
        if self.read_queue:
            return self.read_queue.pop(0)
        return self.memory

    def getBackupStatus(self):
        region = BackupFile(io.BytesIO(self.memory)).getRegion()
        return bytes(0x14) + region.encode("latin1") + b"\0"

    def readBackup(self, prop_id):
        return BackupFile(io.BytesIO(self.memory)).getProperty(prop_id).data

    def setBackupData(self, data):
        self.set_calls += 1
        if self.fail_set_at == self.set_calls:
            raise RuntimeError("simulated setBackupData failure")

        old_backup = BackupFile(io.BytesIO(self.memory))
        new_backup = BackupFile(io.BytesIO(data))
        old_target = old_backup.getProperty(service.TARGET_ID)
        new_target = new_backup.getProperty(service.TARGET_ID)
        if (
            old_target.data != new_target.data
            and old_target.attr & 1
            and new_target.attr & 1
        ):
            raise RuntimeError("simulated read-only property rejection")
        self.memory = bytes(data)

    def syncBackup(self):
        self.sync_calls += 1
        if self.fail_sync:
            raise RuntimeError("simulated sync failure")
        self.disk = self.memory
        if self.mutate_on_sync:
            prop_id = self.mutate_on_sync_property or service.USB_APP_INSTALLER_ID
            mutated = change_property(self.memory, prop_id, b"\x02")
            self.memory = mutated
            self.disk = mutated


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = service.BackupStore(self.root / "backup")

    def tearDown(self):
        self.temp.cleanup()

    def confirm(self, backend, new_value):
        snapshot = service.parse_backup(backend.memory)
        return service.confirmation_text(snapshot, new_value)

    def run_write(self, backend, new_value):
        expected = self.confirm(backend, new_value)
        return service.write_property(
            backend,
            service.TARGET_ID,
            new_value,
            self.store,
            input_func=lambda _: expected,
        )

    def test_fixture_is_valid(self):
        raw = make_backup()
        snapshot = service.parse_backup(raw, service.EXPECTED_REGION)
        self.assertEqual(snapshot["model"], "NEX-6")
        self.assertEqual(snapshot["target"], 0)
        self.assertEqual(snapshot["target_attr"], 1)
        self.assertEqual(snapshot["usb_app_installer"], "01")

    def test_official_cn2_fixture_roundtrip(self):
        fixture = service.SCRIPT_DIR / "fixtures" / "CX76001_CN2.bin"
        if not fixture.is_file():
            self.skipTest("official CN2 fixture is not available")
        backend = FakeBackend(fixture.read_bytes())
        self.assertEqual(backend.set_calls, 0)
        self.assertTrue(self.run_write(backend, 1))
        prop = BackupFile(io.BytesIO(backend.disk)).getProperty(service.TARGET_ID)
        self.assertEqual(prop.data, b"\x01")
        self.assertEqual(prop.attr, 1)
        self.assertTrue(self.run_write(backend, 0))
        prop = BackupFile(io.BytesIO(backend.disk)).getProperty(service.TARGET_ID)
        self.assertEqual(prop.data, b"\x00")
        self.assertEqual(prop.attr, 1)
        self.assertEqual(backend.sync_calls, 2)

    def test_enable_success_with_two_phase_patch_and_persistence(self):
        backend = FakeBackend(make_backup(target=0))
        changed = self.run_write(backend, 1)
        self.assertTrue(changed)
        self.assertEqual(backend.set_calls, 2)
        self.assertEqual(backend.sync_calls, 1)
        for raw in (backend.memory, backend.disk):
            prop = BackupFile(io.BytesIO(raw)).getProperty(service.TARGET_ID)
            self.assertEqual(prop.data, b"\x01")
            self.assertEqual(prop.attr, 1)
        self.assertEqual(len(list(self.root.glob("backup/*PRECHANGE*.bin"))), 2)
        self.assertEqual(len(list(self.root.glob("backup/*POSTCHANGE*.bin"))), 1)

    def test_rollback_success(self):
        backend = FakeBackend(make_backup(target=1))
        changed = self.run_write(backend, 0)
        self.assertTrue(changed)
        prop = BackupFile(io.BytesIO(backend.disk)).getProperty(service.TARGET_ID)
        self.assertEqual(prop.data, b"\x00")
        self.assertEqual(prop.attr, 1)

    def test_noop_still_archives_but_never_writes(self):
        backend = FakeBackend(make_backup(target=1))
        changed = self.run_write(backend, 1)
        self.assertFalse(changed)
        self.assertEqual(backend.set_calls, 0)
        self.assertEqual(backend.sync_calls, 0)
        self.assertEqual(len(list(self.root.glob("backup/*.bin"))), 2)

    def test_wrong_confirmation_never_writes(self):
        backend = FakeBackend(make_backup(target=0))
        with self.assertRaises(service.SafetyError):
            service.write_property(
                backend,
                service.TARGET_ID,
                1,
                self.store,
                input_func=lambda _: "wrong",
            )
        self.assertEqual(backend.set_calls, 0)
        self.assertEqual(backend.sync_calls, 0)

    def test_double_read_mismatch_never_writes(self):
        original = make_backup(target=0, usb_installer=1)
        different = make_backup(target=0, usb_installer=0)
        backend = FakeBackend(original)
        backend.read_queue = [original, different]
        with self.assertRaisesRegex(service.SafetyError, "Unexpected pre-write"):
            self.run_write(backend, 1)
        self.assertEqual(backend.set_calls, 0)

    def test_volatile_runtime_difference_is_allowed(self):
        original = make_backup(target=0, volatile_value=5)
        runtime_changed = change_property(original, 0x00580036, b"\x02")
        backend = FakeBackend(original)
        backend.read_queue = [original, runtime_changed, runtime_changed]
        self.assertTrue(self.run_write(backend, 1))
        self.assertEqual(backend.set_calls, 2)
        self.assertEqual(backend.sync_calls, 1)

    def test_third_read_mismatch_never_writes(self):
        original = make_backup(target=0, usb_installer=1)
        different = make_backup(target=0, usb_installer=0)
        backend = FakeBackend(original)
        backend.read_queue = [original, original, different]
        with self.assertRaisesRegex(service.SafetyError, "Unexpected pre-write"):
            self.run_write(backend, 1)
        self.assertEqual(backend.set_calls, 0)

    def test_wrong_region_is_refused(self):
        with self.assertRaisesRegex(service.SafetyError, "Region mismatch"):
            service.parse_backup(make_backup(region="CX76001_U2"))

    def test_wrong_model_is_refused(self):
        with self.assertRaisesRegex(service.SafetyError, "Model mismatch"):
            service.parse_backup(make_backup(model="NEX-5R"))

    def test_unexpected_target_attr_is_refused(self):
        with self.assertRaisesRegex(service.SafetyError, "Target attr mismatch"):
            service.parse_backup(make_backup(target_attr=0))

    def test_checksum_corruption_is_refused(self):
        raw = bytearray(make_backup())
        raw[-1] ^= 0xFF
        with self.assertRaisesRegex(service.SafetyError, "checksum"):
            service.parse_backup(bytes(raw))

    def test_wrong_address_is_refused_before_any_backend_read(self):
        backend = FakeBackend(make_backup())
        backend.read_queue = [b"invalid"]
        with self.assertRaisesRegex(service.SafetyError, "Address refused"):
            service.write_property(backend, 0xDEADBEEF, 1, self.store)
        self.assertEqual(backend.set_calls, 0)
        self.assertEqual(backend.read_queue, [b"invalid"])

    def test_patch_failure_is_unknown_and_not_synced(self):
        backend = FakeBackend(make_backup(target=0))
        backend.fail_set_at = 1
        with self.assertRaises(service.WriteStateUnknown):
            self.run_write(backend, 1)
        self.assertEqual(backend.sync_calls, 0)
        self.assertLessEqual(backend.set_calls, 2)

    def test_sync_failure_is_unknown_and_not_retried(self):
        backend = FakeBackend(make_backup(target=0))
        backend.fail_sync = True
        with self.assertRaises(service.WriteStateUnknown):
            self.run_write(backend, 1)
        self.assertEqual(backend.set_calls, 2)
        self.assertEqual(backend.sync_calls, 1)

    def test_unrelated_post_sync_change_is_detected(self):
        backend = FakeBackend(make_backup(target=0, usb_installer=1))
        backend.mutate_on_sync = True
        with self.assertRaises(service.WriteStateUnknown):
            self.run_write(backend, 1)

    def test_volatile_post_sync_change_is_allowed(self):
        backend = FakeBackend(make_backup(target=0, volatile_value=5))
        backend.mutate_on_sync = True
        backend.mutate_on_sync_property = 0x00580036
        self.assertTrue(self.run_write(backend, 1))

    def make_preflight(self, model="NEX-6", firmware="1.03", age_hours=0):
        created = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=age_hours)
        return {
            "created_utc": created.isoformat(),
            "model": model,
            "model_code": "0000000000",
            "serial_sha256_16": "0" * 16,
            "firmware_old": firmware,
            "firmware_new": firmware,
        }

    def test_preflight_record_is_loaded_and_validated(self):
        record = self.make_preflight()
        service.save_preflight(self.store, record)
        self.assertEqual(service.load_preflight(self.store)["model"], "NEX-6")

    def test_missing_preflight_blocks_write(self):
        with self.assertRaisesRegex(service.SafetyError, "Missing preflight"):
            service.load_preflight(self.store)

    def test_wrong_preflight_model_blocks_write(self):
        service.save_preflight(self.store, self.make_preflight(model="NEX-5R"))
        with self.assertRaisesRegex(service.SafetyError, "model mismatch"):
            service.load_preflight(self.store)

    def test_stale_preflight_blocks_write(self):
        service.save_preflight(self.store, self.make_preflight(age_hours=25))
        with self.assertRaisesRegex(service.SafetyError, "older than"):
            service.load_preflight(self.store)

    def test_write_rejects_mismatched_preflight_before_backend_read(self):
        backend = FakeBackend(make_backup())
        backend.read_queue = [b"invalid"]
        with self.assertRaisesRegex(service.SafetyError, "Preflight model mismatch"):
            service.write_property(
                backend,
                service.TARGET_ID,
                1,
                self.store,
                input_func=lambda _: "unused",
                preflight=self.make_preflight(model="NEX-5R"),
            )
        self.assertEqual(backend.read_queue, [b"invalid"])
        self.assertEqual(backend.set_calls, 0)

    def test_persisted_backup_supports_updater_backend(self):
        class UpdaterLikeBackend:
            def __init__(self, raw):
                self.disk = raw

            def getBackupData(self):
                return self.disk

        backend = UpdaterLikeBackend(make_backup(target=1))
        self.assertEqual(service.parse_backup(service.persisted_backup(backend))["target"], 1)

    def test_offline_inspect_cli(self):
        path = self.root / "fixture.bin"
        path.write_bytes(make_backup())
        self.assertEqual(service.main(["inspect", str(path)]), 0)

    def test_invalid_cli_value_fails_without_camera(self):
        with mock.patch.object(service, "SCRIPT_DIR", self.root):
            self.assertEqual(service.main(["write", "0x010709ed", "1"]), 2)
            logs = list((self.root / "logs").glob("service-*.log"))
            self.assertTrue(logs)
            self.assertIn("Value must be exactly two", logs[-1].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
