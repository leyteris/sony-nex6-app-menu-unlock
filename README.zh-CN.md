# Sony NEX-6「应用程序」入口恢复 Skill

> **平台：仅支持 Windows 10/11 x64。** 需要本机 USB 和管理员权限。

这个包包含一个可复用的通用 Skill、最终成功版的安全实现、可重建的 Python 环境定义、PMCA/fwtool 源码、Windows 工具，以及国行 Sony NEX-6 成功开启「应用程序」入口的完整实战记录。

`SKILL.md` 是给 AI 编程助手识别和加载的技能定义；本 README 是给人看的中文快速说明。

## 这个 Skill 做什么

它用于恢复国行 NEX-6（固件 1.03、Backup 区域 `CX76001_CN2`）被隐藏的「应用程序」入口，只修改一个 Backup 属性：

```text
0x010709ed: 00 -> 01
```

明确不会修改：

- 固件
- 区域字符串
- 机型字符串
- `0x01640001`
- `0x00e40002`
- 其他任何持久 Backup 设置

最终已验证结果：

- 完整断电重启后主菜单出现「应用程序」
- 重新建立独立 updater 会话后，`0x010709ed` 仍为 `01`

## 适用范围

仅在下列条件匹配或已重新验证时使用：

- Sony NEX-6
- 固件 1.03
- Backup 区域 `CX76001_CN2`
- 目标属性 `0x010709ed`，长度 1，attr `0x01`
- USB 应用安装标志已完成解锁，或另有经过验证的独立流程处理

不要把这套参数原样套用到其他机型或区域。

## 系统依赖

本流程**仅支持 Windows**，并且必须能物理接触到相机。macOS、Linux、WSL、容器或远程沙箱都无法执行。

| 项目 | 要求 |
|---|---|
| 操作系统 | Windows 10/11 x64（已在 Windows 10 Build 19045 验证） |
| 权限 | 管理员/UAC 提权；PMCA SCSI 直通、Zadig 绑定和驱动恢复都需要 |
| Python | 64 位 Python 3.13.x（已验证 3.13.14）；`bootstrap.py` 使用当前解释器 |
| 网络 | `bootstrap.py` 安装依赖时需要访问 PyPI；离线机器请预先下载 wheel |
| PowerShell | Windows PowerShell 5.1 或 PowerShell 7，用于 `elevated-preflight.ps1` |
| USB | 本机物理 USB 口、直连、不使用 Hub；相机保持开机唤醒 |
| 相机 | NEX-6、固件 1.03、区域 `CX76001_CN2`、电量至少 80%、已插入 SD/MS 卡 |
| 驱动 | 通过 Zadig 为 `054C:056C` / `054C:0336` / `054C:03E2` 绑定 `libusb-win32`；结束后恢复 Windows 默认驱动 |
| 磁盘 | 至少 500 MB 可用空间，用于工作区、`.venv` 和时间戳备份 |
| 系统时间 | 保持准确；preflight 记录 24 小时后失效 |
| 终端 | 写入必须在可交互终端执行；禁止管道喂输入或后台运行 |

`assets/tools/*.exe` 均为 Windows x64 程序，运行前请按 `references/tools.md` 核对 SHA256。

## 快速开始

在 Skill 目录执行，并传入目标工作区路径：

```powershell
python .\scripts\bootstrap.py <workspace>
cd <workspace>
.\.venv\Scripts\python.exe -m unittest -v test_service.py
.\.venv\Scripts\python.exe .\service.py --help
```

`bootstrap.py` 会：

1. 把 `assets/workspace/` 复制到目标工作区。
2. 把 Windows 工具复制到 `tools/`。
3. 创建 `.venv`。
4. 安装 `requirements-nex6-lock.txt`。
5. 编译 `service.py` 和 `test_service.py`。
6. 运行离线测试。

如果目标目录已有同名文件且需要覆盖，追加 `--force`。

## 推荐硬件流程

1. 相机充满电。
2. 插入可识别的 SD/MS 卡。
3. USB 直连电脑，不使用 Hub。
4. 管理员运行 `tools\pmca-gui-v0.18-win.exe`，确认 NEX-6 / 1.03。
5. 管理员运行 `tools\zadig-2.9.exe`，只给目标 Sony USB ID 绑定 `libusb-win32`：
   - `054C:056C`：Mass Storage
   - `054C:0336`：服务模式，按需
   - `054C:03E2`：updater 模式
6. 执行两次独立只读快照：

```powershell
.\.venv\Scripts\python.exe .\service.py updater-read
.\.venv\Scripts\python.exe .\service.py updater-read
```

7. 把两份备份及 manifest 复制到第二块物理磁盘，并复核 SHA256。
8. 执行唯一一次受保护写入：

```powershell
.\.venv\Scripts\python.exe .\service.py updater-write 0x010709ed 01
```

9. 关机、断开 USB、等待、重新开机，确认「应用程序」入口。
10. 重新连接 USB，再执行一次 `updater-read` 验证持久化。
11. 恢复 `054C:056C` 的 Windows 默认 USB 驱动。

## 重要实现结论

NEX-6 的 service mode 可以读取 `getBackupStatus()` 和单个 Backup 属性，但整份 Backup 的 `PDT_READ` 会超时。不能用模板重建 Backup 来替代字节级备份。最终成功路径是 PMCA updater shell：它能导出完整 `Backup.bin`，并支持 `BackupPatchDataInterface` 处理只读属性。

## 安全规则

- 不要承诺“绝对不变砖”；只能靠门控、备份、验证和异常即停来降低风险。
- 不要执行相机 `设置 → 初始化 / 恢复出厂`，否则会重新加载 CN2 预设并关闭入口。
- patch 或 sync 失败后不要盲目重试，先重新只读确认状态。
- 不覆盖任何备份文件。
- 不使用其他相机或其他区域的 Backup 做恢复。
- 电量低、设备未枚举、updater 半启动卡住时，立即停止。
- 两次原始 SHA256 不同不一定失败；只有属性级 diff 限于文档列出的动态运行字段时才允许继续。

## 回滚

```powershell
.\.venv\Scripts\python.exe .\service.py updater-write 0x010709ed 00
```

回滚后仍需完整断电重启，并执行新的只读验证。

## 包结构

```text
SKILL.md                    技能定义（机器可读）
README.md                   英文快速说明
README.zh-CN.md             简体中文快速说明
scripts/bootstrap.py        部署工作区、安装环境、运行测试
assets/workspace/           最终 service.py、测试、PMCA 源码、fwtool、fixture
assets/tools/               Zadig、PMCA GUI、PMCA Console
references/case-study.md    全量尝试、根因和最终结果记录
references/safety-checklist.md
references/implementation.md
references/tools.md
```

## 不随包附带

- `.venv/`：由 `bootstrap.py` 重建
- `Update_NEX6V103.exe`：约 205 MB；其哈希、签名和官方下载参考记录在 `references/tools.md`

## 操作前继续阅读

- `SKILL.md`
- `references/case-study.md`
- `references/safety-checklist.md`
- `references/implementation.md`
- `references/tools.md`
