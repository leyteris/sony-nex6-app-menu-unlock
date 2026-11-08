# NEX-6 Application 菜单恢复：实现与环境

## 1. 文件布局

项目根：

```text
service.py
test_service.py
requirements-nex6.txt
requirements-nex6-lock.txt
NEX6_APPLICATION_MENU_PLAN.md
Sony-PMCA-RE/
fwtool.py/
fixtures/CX76001_CN2.bin
backup/
logs/
.venv/
```

最终本轮哈希：

| 文件 | SHA256 |
|---|---|
| `service.py` | `FF7DE9AE563B14A2921F2C1DC5DB546E940C0F5ED9265001C5847E73AE4E178C` |
| `test_service.py` | `1610535A63ADF76C5966A45B5B6445A9534B20BFA2E041E81E588A525CE9FB86` |
| `NEX6_APPLICATION_MENU_PLAN.md` | `52C92ADEDCDAFC400E4F20DBC65B7F5C8F507237F21A9A858E50AF43705C3FF0` |
| `backup/offsite-copy-final.json` | `26F0F875F2EBCB7E453532CF6D9386298E6FCE4B278E885D1FEB733E9776F1CE` |

哈希会随后续修改变化；这里仅标识成功版本。

## 2. 环境搭建

本流程仅支持 Windows 10/11 x64，并且需要管理员权限、本机物理 USB 和可交互终端。系统级依赖：

- Windows 10/11 x64（Windows 10 Build 19045 已验证）
- 64 位 Python 3.13.x（3.13.14 已验证）
- 可访问 PyPI，或预先准备 `requirements-nex6-lock.txt` 的离线 wheel
- Windows PowerShell 5.1 / PowerShell 7（`elevated-preflight.ps1`）
- `libusb-win32`（Zadig 绑定 `054C:056C` / `054C:0336` / `054C:03E2`）
- 至少 500 MB 可用磁盘空间
- 准确的系统时间（preflight 记录 24 小时有效）

```powershell
python -m venv .\.venv
.\.venv\Scripts\python.exe -m pip install -r .\requirements-nex6.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m unittest -v test_service.py
```

关键依赖：

- `pyusb`
- `libusb-package`
- `pywin32`
- `comtypes`
- `pycryptodomex`
- `pyyaml`
- `tlslite-ng`
- `pycparser==2.17`

`bundled_libusb_backend()` 强制 PyUSB 使用 `libusb-package` 自带 DLL，避免 Windows 找不到 libusb-1.0。

## 3. 上游 PMCA 接口

必须使用 master 源码，不使用 v0.18 可执行程序做 Backup 操作。

关键接口：

- `pmca.backup.BackupFile`：解析 Backup.bin 并校验 checksum
- `pmca.platform.backup.BackupPatchDataInterface`：处理 attr `0x01` 只读属性的两阶段 patch
- `pmca.platform.backend.senser.SenserPlatformBackend`：服务模式；NEX-6 仅适合单属性读取，不适合整份 Backup 导出
- `pmca.platform.backend.usb.UsbPlatformBackend`：updater shell；支持 `getBackupData`、`setBackupData`、`syncBackup`
- `pmca.commands.usb.updaterShellCommand`：进入临时 updater 环境
- `pmca.commands.usb.senserShellCommand`：进入 service mode
- `SonySenserAuthDevice`：服务模式认证
- `SonyUpdaterCamera.complete()`：结束 updater payload 流程

成功执行时，updater `complete` 回调的参数是 raw `SonyMscUpdaterDevice`，不是 backend；必须包装：

```python
backend = UsbPlatformBackend(device)
backend.start()
try:
    operation(backend)
finally:
    backend.stop()
```

异常时调用 `SonyUpdaterCamera(device).complete()`，避免相机停在半启动 updater 状态。

## 4. `service.py` 命令

```text
preflight       机读 NEX-6 / 1.03，生成记录
read            服务模式单份只读 Backup；保留诊断用途
hold-service    保持 054C:0336 出现以便绑定驱动
updater-read    updater shell 整份真实 Backup 只读归档
inspect         离线解析 Backup.bin
write           服务模式保护性写入；本机型不作为最终路径
updater-write   updater shell 保护性写入；最终路径
```

示例：

```powershell
.\.venv\Scripts\python.exe .\service.py preflight
.\.venv\Scripts\python.exe .\service.py hold-service --seconds 180
.\.venv\Scripts\python.exe .\service.py updater-read
.\.venv\Scripts\python.exe .\service.py inspect .\backup\<file>.bin
.\.venv\Scripts\python.exe .\service.py updater-write 0x010709ed 01
```

## 5. 安全实现要点

### 备份存储

`BackupStore.save()`：

- 时间戳唯一文件名
- `xb` 排他创建
- `fsync`
- 回读字节比较
- SHA256 manifest JSON
- 不覆盖任何旧备份

### 机读预检

- `run_preflight()` 使用 PMCA `getCameraInfo` 与 `getFirmwareVersion`
- 只保存序列号/产品码指纹，不把原始序列号写入日志
- `load_preflight()` 要求 24 小时内记录，且 NEX-6 / 1.03 匹配
- 允许管理员 PMCA GUI 的只读结果作为可信人工转录记录，但需在 `source` 中说明

### Backup 解析

`parse_backup()`：

- 使用 `BackupFile` 校验 checksum
- 要求 `CX76001_CN2`
- 要求 `NEX-6`
- 要求目标属性长度 1、attr `0x01`、值仅 `00/01`
- 记录 USB 安装标志但不修改

### 写入

`write_property()`：

- 仅允许 `0x010709ed` 和 `00/01`
- 两次 pre-read + 一次 `BackupPatchDataInterface` 快照
- 允许 `VOLATILE_PROP_IDS` 动态字段变化
- 精确确认串包含 `NEX-6 1.03 CX76001_CN2 010709ed 00>01 <hash12>`
- 调用上游 `patch.apply()` 两阶段处理只读属性
- `syncBackup()`
- 内存与持久化副本双重验证
- `assert_only_target_changed()` 全属性审计
- 失败分类：
  - `SafetyError`：写前门控失败，可安全重试
  - `WriteStateUnknown`：patch/sync 后失败，禁止盲重试

### 动态属性白名单

两次 updater 读取可能 SHA256 不同。`assert_pre_read_compatible()` 与 `assert_only_target_changed()` 比较：

- 属性 ID 集合
- attr
- data
- resetData

只允许 `VOLATILE_PROP_IDS` 中 16 个动态运行字段 data 变化，其他变化一律拒绝。

### Updater 执行器

`run_updater()`：

- 使用 `updaterShellCommand(model='NEX-6', driverName='libusb', complete=...)`
- 相机屏幕可能需要按 OK
- 成功时由上游执行 `dev.complete()`
- 操作异常时主动执行 `SonyUpdaterCamera(device).complete()` 清场
- 上游 `firmwareUpdateCommandInternal` 的等待循环在本项目改为 `range(240)`，把等待窗口从 30 秒延长到 120 秒

### 服务模式保持

`hold_service_mode(seconds)`：

- 绑定 `056C` 后切换到 service mode
- 保持认证会话，产生 `054C:0336`
- 给 Zadig 180 秒绑定窗口
- 结束调用 `auth.stop()`

## 6. 测试策略

`test_service.py` 合成合法 Backup v4 结构并验证：

- 区域/型号/目标/attr 校验
- checksum 错误
- 只读属性两阶段 patch
- 双读/三读不一致
- 错误确认
- patch 失败 / sync 失败为未知状态
- 非白名单属性变化
- 动态白名单属性变化
- 真实 CN2 fixture 写入/回滚
- updater backend 的持久化读取
- CLI 离线检查

最终：`26/26 OK`。

## 7. 固件验证

`fwtool.py` 无法直接识别本更新器外层包装时：

1. 搜索 DAT magic `\x89UFU\r\n\x1a\n`
2. 从 magic offset 截取 `FirmwareData_*.dat`
3. 再交给 `fwtool.py unpack`
4. 在 `firmware.tar_unpacked/0110_backup/SYSAVIP-DSLR/EYC_S/` 对比 12 个区域预设

不要修改源 EXE。

## 8. 驱动恢复注意

成功写入后，`054C:056C` 可能仍是 libusb-win32。普通 USB 连接可能显示「请检查所连接设备」。使用管理员设备管理器只卸载该 NEX-6 实例，重新扫描/拔插 USB，让 Windows 恢复默认驱动。不要删除其他设备的 libusb 驱动。

## 9. Skill 自包含部署

使用 `scripts/bootstrap.py` 从 Skill 目录复制完整工作区与工具：

```powershell
python <skill-dir>\scripts\bootstrap.py <workspace>
```

脚本会：

1. 复制 `assets/workspace/` 到目标工作区。
2. 复制 `assets/tools/` 到目标工作区的 `tools/`。
3. 创建 `.venv`。
4. 安装 `requirements-nex6-lock.txt`。
5. 编译 `service.py` 与 `test_service.py`。
6. 运行 `unittest -q test_service.py`。

随包代码副本把 `SCRIPT_DIR` 设为当前工作目录，并把 `PMCA_DIR` 设为脚本旁边的 `Sony-PMCA-RE/`，因此可以在新工作区运行。不要把 `.venv/` 或 205 MB 的官方固件更新器打包进 Skill；前者由 bootstrap 重建，后者只记录哈希与官方下载参考。
