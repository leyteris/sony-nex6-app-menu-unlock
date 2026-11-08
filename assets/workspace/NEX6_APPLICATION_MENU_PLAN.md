# 索尼 NEX-6 国行「应用程序」入口操作方案

- 适用对象：Sony NEX-6，固件 Ver.1.03，实时区域 `CX76001_CN2`
- 目标：仅将 Backup 属性 `0x010709ed` 从 `00` 改为 `01`，使菜单出现「应用程序」
- 原则：先评估、只读侦察、双份字节级备份、单属性修改、持久化验证、可回滚
- 文档日期：2026-11-07

## 1. 方案评估

技术上可行，但未满足全部安全门控前禁止写入。本方案不修改区域字符串、不刷写整份固件，也不修改其他 Backup 属性。

`0x010709ed` 是 Backup 设置区的属性编号，不是固件文件偏移。写入后校验和和冗余数据也可能变化，因此“单字节”指一个逻辑属性，不保证物理镜像只有一字节差异。

已完成的 USB APK 解锁对应另一属性 `0x01640001`，本次不再修改。`0x00e40002` 用途未确认，本次明确不改。

issue #32 中 Claude 的方案采用 updater shell 并写三个标志；本目录成功记录采用 service mode 且目标相机已完成 USB APK 解锁，因此本实现只采用其中可验证且范围最小的部分：主线 PMCA 的 `BackupPatchDataInterface`（即 `bk patch` 的底层机制）处理只读属性 `0x010709ed`，不混入 updater shell，也不写用途未确认的第三个属性。

### 重要恢复限制

1. `Update_NEX6V103.exe` 只处理固件，不保证恢复 Backup 设置。
2. 索尼官方说明当前已是 1.03 时可能直接提示无需更新，因此不能把“任何失败都先回刷 1.03”作为通用第一动作。
3. 设置异常应优先将目标属性写回 `00`；只有固件或启动异常且官方更新器仍能识别相机时，才进入官方固件恢复。
4. `docs.txt` 所述约 670 KB 备份是 Backup 设置数据，不是完整固件或整颗闪存镜像。

## 2. 写入放行条件

以下条件必须全部满足：

- [ ] 相机菜单和 PMCA 信息均确认 NEX-6、Ver.1.03。
- [ ] `service.py preflight` 已生成 24 小时内有效的机读记录。
- [ ] 只读结果为 `LIVE REGION = 'CX76001_CN2'`。
- [ ] Backup 中的型号为 NEX-6。
- [ ] `0x010709ed` 长度为 1，原值为 `00`；若已为 `01` 则不再写入。
- [ ] 两次独立读取均通过 PMCA `BackupFile` 校验和检查。
- [ ] 两次原始备份逐字节一致，SHA256 相同。
- [ ] 原始备份已在本机和另一块磁盘各保存一份，并复核哈希。
- [ ] `service.py` 使用官方 PMCA 主线 Backup 接口，写前重新备份，且不覆盖旧备份。
- [ ] 电池充满、电脑接电、系统休眠关闭、USB 连接稳定。
- [ ] 原版更新器可离线使用，已知晓同版本可能拒绝更新。

`docs.txt` 中其他相机的 SHA256 不能作为本机备份预期值。本机 `PRECHANGE` 必须是写入前直接读出的原始数据，不能用重建文件替代。

## 3. 准备

1. 备份照片。若按 Mass Storage 执行 PMCA 预检和服务模式，需保留一张可识别存储卡；当前无介质时 Windows 会显示 `No Media`，PMCA 打开逻辑盘会拒绝访问。只有执行索尼官方固件恢复时，才按索尼说明取出存储卡。
2. 使用原装或可靠 USB 线直连电脑，不用 Hub。
3. 关闭 PlayMemories、Imaging Edge、照片导入及云同步软件。
4. 相机设置：`设置 → USB连接 → 海量存储`。
5. 记录设备管理器中的原驱动信息。
6. 更换驱动前执行机器可读预检：

```powershell
.\.venv\Scripts\python.exe .\service.py preflight
```

该命令只读确认 NEX-6 / 1.03，并在 `backup/` 生成 24 小时内有效的 `preflight-*.json`。后续 `service.py write` 没有有效 preflight 会拒绝执行，不再只依赖人工复述。

7. 在项目隔离环境安装依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\requirements-nex6.txt
```

8. 先完成离线测试、CLI 检查和 Backup  fixture 验证，不得跳过测试直接写相机。

## 4. Zadig 驱动配置

1. 在进入服务模式前，通过拔插确认相机为 `VID_054C&PID_056C`。
2. Zadig：`Options → List All Devices`，只选择该相机，选择 `libusb-win32`，点击 `Replace Driver`。
3. 关闭 Zadig，执行 `python service.py read`。
4. 第一次切换后可能出现新设备 `054C:0336` 并超时；只有确认新设备已出现，才把超时视为预期现象。
5. 保持 USB 不动，再给 `054C:0336` 绑定 `libusb-win32`。
6. 若设备 ID 不符、没有切换或伴随其他错误，停止，不反复尝试。

## 5. 只读侦察与备份

分别启动新进程：

```powershell
.\.venv\Scripts\python.exe .\service.py read
.\.venv\Scripts\python.exe .\service.py read
```

每次必须确认：

- 实时区域与 Backup 文件区域一致，均为 `CX76001_CN2`；
- 型号为 NEX-6；
- 目标属性为 `00`、长度为 1；
- `checksum OK`；
- 备份文件非空，生成唯一文件名，SHA256 已记录。

使用以下命令核对：

```powershell
Get-ChildItem .\backup\backup-data-*.bin | Select-Object Name,Length,LastWriteTime
Get-FileHash -Algorithm SHA256 .\backup\backup-data-*.bin
```

也可离线检查指定备份：

```powershell
.\.venv\Scripts\python.exe .\service.py inspect .\backup\<实际文件名>.bin
```

## 6. 唯一一次写入

人工复述并核对：

> NEX-6 / 1.03 / CX76001_CN2 / 0x010709ed / 00→01 / 原始备份已双份校验

执行：

```powershell
.\.venv\Scripts\python.exe .\service.py write 0x010709ed 01
```

脚本必须再次完成双读、原值和区域检查、写入前备份及交互确认。成功输出应包含：

```text
before: 00
writeBackup OK
syncBackup OK
after: 01
```

任何环节异常或结果不确定，都不得直接重试写入，先只读判断状态。

## 7. 持久化与功能验证

1. 新进程执行 `service.py read`，确认区域不变、目标为 `01`、校验和正常。
2. 保存写入后备份和哈希，做属性级比较；除目标属性及合理校验和变化外，不应有其他设置变化。
3. 正常退出、关机、等待后开机。
4. 验证「应用程序」和「应用程序列表」，并测试来源明确的 APK 或已有应用。
5. 验证拍照、回放、USB、Wi-Fi 和其他菜单。
6. 完整断电重启后再次确认目标仍为 `01`。
7. 不执行 `设置 → 初始化/恢复出厂`，以免重新加载 CN2 预设。

## 8. 停止条件

| 情况 | 处置 |
|---|---|
| 机型、固件、区域或 USB ID 不符 | 不写入，退出 |
| 备份校验失败、双读不同或文件保存失败 | 不写入，保留日志 |
| 属性长度不是 1，或原值不是 `00` | 不写入 |
| 写入输出不完整 | 不重试，先只读确认 |
| 值为 `01` 但重启后仍无菜单 | 不改其他地址，保存日志，可回滚 |
| 功能异常但服务模式可用 | 优先单属性回滚 |
| 无法启动或固件级异常 | 停止服务写入，进入官方恢复判断 |

## 9. 回滚

### 9.1 首选单属性回滚

```powershell
.\.venv\Scripts\python.exe .\service.py write 0x010709ed 00
.\.venv\Scripts\python.exe .\service.py read
```

预期 `before: 01`、`after: 00`。随后完整断电重启并检查功能。

只有单属性回滚不可行，且备份确属本机、哈希复核完成时，才考虑通过 `setBackupData()` 恢复整份 Backup 设置数据。绝不使用另一台相机的备份。

### 9.2 官方固件恢复

1. 确保没有进行中的服务写入。
2. 在设备管理器中恢复被 Zadig 替换的正常 USB 驱动。
3. 相机设为「海量存储」，取出存储卡，使用原装 USB 线。
4. 运行经过 Sony 数字签名验证的 `Update_NEX6V103.exe`，严格按提示操作。
5. 更新期间严禁断电、拔线或同时运行 PMCA。

若更新器因当前已是 1.03 而拒绝，不强刷、不使用其他机型固件，也不把 PMCA 固件写入命令当作救援替代；保留日志和备份并寻求专业支持。

## 10. 本地文件指纹

`Update_NEX6V103.exe`：

- 大小：`205007992` 字节
- SHA256：`202E045AD773490FCA9129AA35E19855E49835EE7BAC78D09EC9FCD51223CDC1`
- 数字签名：验证通过，签名者 Sony Corporation

## 11. 真实固件交叉验证

已从本地 `Update_NEX6V103.exe` 的 offset `524858` 处截取内部 Sony DAT 数据并完成官方 `fwtool.py` 只读解包：

- 内部 DAT 验证指纹如下（临时文件已清理）
- SHA256：`0E83A595D12DCAEAC7FBB2F1FDEB232EEE5F32347268895959F416E59C1F715E`
- 固件包内共有 12 个区域预设。
- 只有 `CX76001_CN2` 的 `0x010709ed / 0x01640001 / 0x00e40002` 为 `00`。
- `0x010709ed` 在全部预设中的 attr 均为 `0x01`，必须使用 patch 路径。
- 已固化真实样本：`fixtures/CX76001_CN2.bin`
- 样本 SHA256：`FC158BB7DB42D732890EBB189D0C48CCDA4E0F6343EA0C346D20DABF7A8DF677`
- 该样本的离线写入与回滚均通过，完整属性审计确认只有目标属性变化。
- 最终离线测试：`26/26 OK`。

约 932 MB 的一次性完整解包产物已清理，仅保留 670 KB 的测试样本。

## 12. 最终执行记录（2026-11-07）

结论：**已成功开启国行 NEX-6 的「应用程序」入口。**

关键过程：

1. 服务模式可读取单个属性，但 NEX-6 对整份 `PDT_READ` 无响应，不能提供可靠字节级整份备份。
2. 因此按 issue #32 切换到 updater shell；使用 PMCA 自带 `CXD4132/NEX-6` 临时 payload，不刷 Sony 固件。
3. 执行前完成 NEX-6 / 1.03 机读确认。
4. 两次真实 Backup 只读均通过 `CX76001_CN2`、NEX-6 和 checksum 校验。
5. 两次快照的 16 个差异均为实测动态运行字段，已由显式白名单限制；持久属性不变。
6. 写入前将备份复制到 `<offsite-backup-dir>` 并复核 SHA256。
7. 唯一写入：`0x010709ed`：`00 -> 01`。
8. 写后内存与同步副本均验证为 `01`，全属性审计确认持久属性只有目标项变化。
9. 完整断电重启后，相机主菜单已出现「应用程序」。
10. 再次进入新 updater 只读会话，目标值仍为 `01`，确认持久化。

关键备份：

| 文件 | SHA256 |
|---|---|
| `backup-data-PRECHANGE-20261107T154412.375095Z.bin` | `40EC0B496CE3A2E3B4195940AB473EC246F2A2496B04644F445475ACB7554367` |
| `backup-data-PRECHANGE-VERIFY-20261107T154412.387844Z.bin` | `40EC0B496CE3A2E3B4195940AB473EC246F2A2496B04644F445475ACB7554367` |
| `backup-data-POSTCHANGE-20261107T154415.883633Z.bin` | `413B4D5D203FA6710DF147AA6316C3EDAB9A59271FF02C35F48580CDFEAFE979` |
| `backup-data-READ-UPDATER-20261107T155004.785635Z.bin` | `0048A9A60D261B980A8DB451B7444B59AE5224093A5D43914918C5B308E9A0E2` |

本地副本：`<workspace>\backup\`  
第二副本：`<offsite-backup-dir>\`

回滚命令（如需要）：

```powershell
.\.venv\Scripts\python.exe .\service.py updater-write 0x010709ed 00
```

注意：执行 `设置 → 初始化/恢复出厂` 会重新加载 CN2 预设并关闭入口，不要执行。

### 驱动恢复（仍需人工完成）

当前 `054C:056C` 等记录仍绑定 libusb-win32，普通 USB 连接可能显示“请检查所连接设备”。以管理员打开设备管理器，卸载 `NEX-6` / Sony USB Device 的 libusb-win32 设备，再重新扫描或拔插 USB，让 Windows 恢复默认大容量存储驱动。不要随意删除其他 USB 设备。

## 13. 参考

- 本目录 `docs.txt`
- https://github.com/ma1co/Sony-PMCA-RE
- https://github.com/ma1co/OpenMemories-Tweak/issues/32
- https://www.sony.com/electronics/support/downloads/W0009667
