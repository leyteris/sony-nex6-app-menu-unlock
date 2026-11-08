# NEX-6 国行 Application 菜单恢复：完整实战记录

记录日期：2026-11-07  
项目目录：`<workspace>`  
结果：成功；完整重启后菜单出现，目标属性保持 `01`。

## 1. 目标与证据基线

目标：仅将 Backup 属性 `0x010709ed` 从 `00` 改为 `01`，恢复国行 NEX-6 的「应用程序」入口。

已验证条件：

- 机型：NEX-6
- 固件：1.03
- 区域：`CX76001_CN2`
- 目标属性：`0x010709ed`，长度 1，attr `0x01`
- USB APK 安装标志 `0x01640001` 在真机备份中为 `01`
- 不修改 `0x00e40002`
- 不修改区域字符串、不刷固件

### 官方固件预设验证

本地 `Update_NEX6V103.exe`：

- 大小：`205007992` bytes
- SHA256：`202E045AD773490FCA9129AA35E19855E49835EE7BAC78D09EC9FCD51223CDC1`
- 数字签名：Sony Corporation，验证通过
- 内部 DAT magic offset：`524858`
- 内部 DAT SHA256：`0E83A595D12DCAEAC7FBB2F1FDEB232EEE5F32347268895959F416E59C1F715E`

`fwtool.py` 直接解包 EXE 时返回 `Unknown exe file`。根因：更新器外层 WinZip/PE 包装不属于 fwtool 覆盖布局。绕过方式是定位 Sony DAT magic `\x89UFU\r\n\x1a\n` 后截取剩余数据，再由 fwtool 正常解密和解包。

解包出的 `0110_backup/SYSAVIP-DSLR/EYC_S` 共 12 个区域预设：

- 仅 `CX76001_CN2` 的 `0x010709ed / 0x01640001 / 0x00e40002` 均为 `00`
- 其他 11 个区域这三个属性均为 `01`
- `0x010709ed` 在所有区域均为 attr `0x01`
- 真实 CN2 预设：`fixtures/CX76001_CN2.bin`
- SHA256：`FC158BB7DB42D732890EBB189D0C48CCDA4E0F6343EA0C346D20DABF7A8DF677`
- 大小：`669995` bytes

一次性 932 MB 完整解包产物已清理，仅保留测试样本。

### issue #32 中 Claude 信息的取舍

可采纳：

- 必须使用 Sony-PMCA-RE master，v0.18 缺少 `bk patch` 后的 Backup 文件接口
- `0x010709ed` 是只读属性，必须走 `BackupPatchDataInterface` / `bk patch`
- 初始化会重载 CN2 预设，应禁止
- 电量约 50% 时 updater 可能拒绝运行

未采纳：

- Claude 的三次写入 `01640001 / 00e40002 / 010709ed`
- `0x00e40002` 用途未确认，且本机 USB 安装已解锁，因此不扩大范围

## 2. 环境与源码

- Python：`3.13.14`，隔离 venv：`.venv/`
- PMCA 主线：`Sony-PMCA-RE`，commit `a82f5baaa8e9c3d9f28f94699e860fb2e48cc8e0`
- fwtool：`fwtool.py`，commit `cdba742b73eed5981480c326aeb30033aabf0223`
- `requirements-nex6.txt` + `requirements-nex6-lock.txt`
- 关键依赖：`pyusb`, `libusb-package`, `pywin32`, `comtypes`, `pycryptodomex`, `pyyaml`, `tlslite-ng`, `pycparser==2.17`
- `pip check`：无损坏依赖
- bundled libusb backend：可用

离线实现：

- `service.py`
- `test_service.py`
- 合成 Backup v4 fixture 生成器
- 真实 CN2 fixture 写入/回滚测试
- 最终测试：`26/26 OK`

## 3. 尝试与根因时间线

| 阶段 | 表现 | 根因 | 处理 |
|---|---|---|---|
| 初始 PMCA info | `No devices found` | 相机当时未在线 / 仅残留 PnP 记录 | 重新检查 PnP |
| 插入卡前 preflight | `CreateFile WinError 5`，`Sony DSC No Media` | Windows 逻辑盘不可访问；当时非管理员 | 插入 SD/MS 卡 |
| 插入卡后仍 WinError 5 | 当前终端非管理员 | PMCA Windows SCSI pass-through 需要管理员 | 用管理员 PMCA GUI 完成机读预检 |
| 手工提权 | `Start-Process -Verb RunAs` 被环境安全策略拦截 | 当前会话不能代弹 UAC | 用户手动运行管理员 PMCA GUI |
| PMCA GUI deviceinfo | NEX-6 / 1.03 / product code / serial / lens | 成功 | 生成 `preflight-20261107T141954Z-pmca-gui.json` |
| `service.py preflight` 恢复后重试 | `firmware_old=1.03`, `firmware_new=0.00` | 相机刚退出临时 updater，版本号查询异常 | 保留可信 GUI preflight，不覆盖 |
| Zadig 绑定 `056C` | `libusb-win32 1.4.0.0` | 成功 | 继续 |
| 首次 `service.py read` | 切换到 service mode 后超时 | `0336` 新设备尚未绑定驱动 | 检查 PnP |
| Zadig 列表无 `0336` | 上游只等待 5 秒，命令退出后设备撤回 | 等待窗口过短 | 新增 `hold-service --seconds 180` |
| `hold-service` | `0336` 持续出现 | 成功 | 用户在保持窗口内绑定 `0336` |
| 服务模式 read | `Authenticating` 后 `Cannot download Backup data:` | 漏掉 `SenserPlatformBackend.start()` | 增加 `backend.start()` / `stop()` |
| 加入 start 后 | `Senser product info error 128` | NEX-6 不支持 `readHasp` 探针 | 仅忽略 error 128 |
| 忽略 128 后 | 整份 Backup 仍空异常 | `getBackupData` / `PDT_READ` 超时 | 底层栈定位 |
| 底层诊断 | `getBackupStatus`, `readBackup` 正常；`getBackupPresetData(True/False)` 20 秒超时 | NEX-6 service mode 不支持整份 Backup 导出 | 切换 updater shell |
| 提高 USB timeout | 仍超时 | 非慢，而是命令不支持 | 不再强行在服务模式重建 Backup |
| updater 首次尝试 | 等待相机确认超时，随后 `03E2` 出现 | `03E2` 未绑定 libusb | Zadig 绑定 `03E2` |
| updater callback 初版 | `AttributeError: SonyMscUpdaterDevice no start` | 回调参数是 raw device，不是 backend | 包装 `UsbPlatformBackend(device)` |
| payload 写到 100% 后异常 | 未执行 `dev.complete()`，相机停在半启动状态 | 回调异常打断上游收尾 | 实现异常自动 `complete` |
| 重复写 payload | 进程被 SIGTERM，会话卡住 | 复用半启动状态 | 停止、取电池、充电、重启 |
| 电池低 / 无反应 | updater 环境未完成，电池电量不足 | 低电量 | 完整充电后再操作 |
| 恢复后 preflight | `054C:056C` 正常 | 成功 | 继续 |
| updater-read 两次 | 均成功，SHA256 不同 | 16 个动态运行字段（时间/计数）变化 | 引入 `VOLATILE_PROP_IDS` 白名单 |
| 首次写入 | 等待相机确认 30 秒超时 | 用户未在窗口内确认 | 上游等待改为 120 秒 |
| 第二次写入 | 启动时 `No devices found`，随后相机变 `03E2` | Mass Storage / updater 切换时序错位 | 电源循环后重试 |
| `03E2` 仍存在 | 电源键重启后仍回 updater | 临时 updater 未正常退出 | 发送 `SonyUpdaterCamera.complete()` |
| 最终写入 | 成功 | 干净 `056C` 起点 + 用户按 OK | 进入审计 |
| 写后菜单检查 | 出现「应用程序」 | 成功 | 断电验证 |
| 写后新会话 read | `0x010709ed = 01` | 持久化成功 | 结束 |

## 4. 最终写入结果

写入前：

- `MODEL = 'NEX-6'`
- `LIVE REGION = 'CX76001_CN2'`
- `FILE REGION = 'CX76001_CN2'`
- `TARGET 0x010709ed = 00 (attr 0x01)`
- `USB APP INSTALLER 0x01640001 = 01`
- `BACKUP SIZE = 669995`
- `checksum OK`

写入输出：

```text
before: 00
writeBackup OK
syncBackup OK
PROPERTY AUDIT OK: persistent properties unchanged except 0x010709ed -> 01
PROPERTY AUDIT OK: persistent properties unchanged except 0x010709ed -> 01
after: 01
PERSISTENCE CHECK OK
WRITE_RESULT_CHANGED= True
```

完整断电重启后：

- 主菜单出现「应用程序」
- 重新进入 updater 只读：
  - `TARGET 0x010709ed = 01`
  - `USB APP INSTALLER = 01`
  - `checksum OK`

## 5. 备份与哈希

本地：`<workspace>\backup\`  
第二副本：`<offsite-backup-dir>\`

| 文件 | SHA256 |
|---|---|
| `backup-data-PRECHANGE-20261107T154412.375095Z.bin` | `40EC0B496CE3A2E3B4195940AB473EC246F2A2496B04644F445475ACB7554367` |
| `backup-data-PRECHANGE-VERIFY-20261107T154412.387844Z.bin` | `40EC0B496CE3A2E3B4195940AB473EC246F2A2496B04644F445475ACB7554367` |
| `backup-data-POSTCHANGE-20261107T154415.883633Z.bin` | `413B4D5D203FA6710DF147AA6316C3EDAB9A59271FF02C35F48580CDFEAFE979` |
| `backup-data-READ-UPDATER-20261107T155004.785635Z.bin` | `0048A9A60D261B980A8DB451B7444B59AE5224093A5D43914918C5B308E9A0E2` |

早期两次独立真实读取的 SHA256 不同，但所有持久属性一致；差异仅来自 16 个动态运行字段。

## 6. 收尾

- 当前 `054C:056C` 仍可能是 libusb-win32 驱动，普通 USB 连接可能显示「请检查所连接设备」。
- 需要在管理员设备管理器中只卸载 NEX-6 的 libusb 设备并重新扫描/拔插，让 Windows 恢复默认驱动。
- 不要卸载其他 libusb 设备，尤其不要碰 Intel Bluetooth。
- 不要执行相机初始化。
- 回滚命令：

```powershell
.\.venv\Scripts\python.exe .\service.py updater-write 0x010709ed 00
```
