# 随包工具与可运行环境

## 1. 一键部署

Skill 的 `scripts/bootstrap.py` 会：

1. 把 `assets/workspace/` 中的代码、依赖文件、PMCA 主线源码、fwtool、fixture 和方案文档复制到目标工作区。
2. 把 `assets/tools/` 中的 Zadig 与 PMCA GUI/Console 复制到目标工作区的 `tools/`。
3. 创建 `.venv`。
4. 安装 `requirements-nex6-lock.txt`。
5. 执行 `py_compile` 和 `unittest -q test_service.py`。

示例（在 Skill 目录执行，并传入目标工作区路径）：

```powershell
python .\scripts\bootstrap.py <workspace>
```

从目标工作区运行：

```powershell
.\.venv\Scripts\python.exe .\service.py --help
.\.venv\Scripts\python.exe -m unittest -v test_service.py
```

如果目标目录已有同名文件，默认跳过；如需覆盖：

```powershell
python .\scripts\bootstrap.py <workspace> --force
```

## 2. 随包内容

### 可运行工作区

`assets/workspace/`：

- `service.py`：最终成功版；`SCRIPT_DIR = Path.cwd()`，`PMCA_DIR` 相对脚本位置解析
- `test_service.py`：`26` 个离线测试
- `requirements-nex6.txt`：顶层依赖
- `requirements-nex6-lock.txt`：已验证精确版本
- `Sony-PMCA-RE/`：PMCA 主线源码，含 NEX-6 updater 等待窗口本地修正
- `fwtool.py/`：官方固件解包工具源码
- `fixtures/CX76001_CN2.bin`：真实 CN2 预设样本
- `NEX6_APPLICATION_MENU_PLAN.md`：方案与最终执行记录
- `elevated-preflight.ps1`：管理员预检辅助脚本

### 工具

`assets/tools/`：

| 文件 | 大小 | SHA256 |
|---|---:|---|
| `zadig-2.9.exe` | 5,334,088 | `4ECAA95DF3DA3621486A043AEF8B3050B8BAFE7C901402871E816229EF82039B` |
| `pmca-console-v0.18-win.exe` | 7,651,552 | `051311E8992B8E834129B15BCF0A486990F2783868D15E421D03C093699FE2F5` |
| `pmca-gui-v0.18-win.exe` | 10,195,086 | `C10198B706C9416C42619C56AD3DC780706144FB5098377C5E45E35DCBFBF4F2` |

真实 CN2 fixture：

| 文件 | SHA256 |
|---|---|
| `assets/workspace/fixtures/CX76001_CN2.bin` | `FC158BB7DB42D732890EBB189D0C48CCDA4E0F6343EA0C346D20DABF7A8DF677` |

工具用途：

- `pmca-gui-v0.18-win.exe`：管理员运行，读取 NEX-6 / 1.03 deviceinfo
- `pmca-console-v0.18-win.exe`：辅助设备信息读取和 PMCA 基础诊断
- `zadig-2.9.exe`：给 `054C:056C`、`054C:0336`、`054C:03E2` 绑定 `libusb-win32`

## 3. 不随包附带的内容

### Python 虚拟环境

不打包 `.venv/`，因为它绑定本机路径和 Python 版本。由 `bootstrap.py` 重建。

### `Update_NEX6V103.exe`

约 205 MB，仅记录指纹，不塞入 Skill 包：

- 大小：`205007992` bytes
- SHA256：`202E045AD773490FCA9129AA35E19855E49835EE7BAC78D09EC9FCD51223CDC1`
- 签名：Sony Corporation
- 官方下载参考：`https://www.sony.com/electronics/support/downloads/W0009667`

固件只用于离线验证区域预设和应急恢复判断；正常菜单解锁不需要刷写它。

## 4. 验证后的环境基线

- Python `3.13.14` / Windows 10 Build 19045
- `pip check`：`No broken requirements found`
- bundled libusb backend：可用
- 离线测试：`26/26 OK`
- PMCA 主线 commit：`a82f5baaa8e9c3d9f28f94699e860fb2e48cc8e0`
- fwtool commit：`cdba742b73eed5981480c326aeb30033aabf0223`

在新机器上使用其他 Python 版本时，先运行 `bootstrap.py` 和离线测试；不要直接操作相机。
