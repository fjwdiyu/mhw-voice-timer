# MHW 竞速语音计时器（MHW Voice Timer）

给《怪物猎人：世界 / Iceborne》（PC Steam 版）竞速练习用的语音计时器。自动读取游戏内存识别任务开始/结束，用甜美少女音播报进行时长与完成时间；带图形界面，可直接改配置、一键触发 IN-Q 重置、设置定时自动重置。

> 只读取游戏内存识别状态（与 HunterPie、LiveSplit 自动分段同类），不修改游戏文件、不注入、不上传任何数据。

## 功能

- **图形界面**：所有设置可视化调整，实时显示连接/任务状态与当前用时
- **一键重置**：点按钮即触发 IN-Q 的重置（无需自己按 F11）
- **定时重置**：任务进行到设定秒数自动触发重置（例如 180 秒没打完就重开）
- 任务开始 / 重置时自动播报「任务开始」
- 进行中每隔 60 秒播报一次「已进行 X 分 0.0 秒」
- 击杀目标瞬间播报「任务完成」，随后播报「用时 X 分 X 秒」
- 兼容 IN-Q 的 F11 快速重置 / F10 重置
- 语音默认微软晓晓 `zh-CN-XiaoxiaoNeural`（甜美少女音，需联网），离线自动退回系统内置中文语音
- 也可用无界面托盘模式运行（命令行 `--tray`）

## 系统要求

- Windows 10 / 11 64 位
- Steam 版《怪物猎人：世界》+ 冰原 DLC，版本 v15.20 或更新
- 免安装、免 Python（使用打包好的 exe）

## 下载与使用

在 [Releases](../../releases) 下载最新的 `MHW-Voice-Timer_v1.1.zip`，解压后：

1. 双击 `MHW竞速语音计时器.exe`，弹出图形界面即启动成功（先开计时器或先开游戏都可以）。
2. 进任务 / 按 IN-Q 的 F11 重置后自动播报。
3. 退出：点界面右下角「退出」。

> 若 Windows 弹出「已保护你的电脑」：点「更多信息」→「仍要运行」即可（未签名小工具的通病）。

### 界面里的「IN-Q 重置」

| 控件 | 说明 |
| --- | --- |
| 重置按键 | 选 `F11`/`F10`，须与你在 IN-Q 里设置的「重新开始/重置」快捷键一致 |
| 手动重置任务 | 点一下即发送该按键，触发 IN-Q 重置 |
| 定时重置(秒) | 填 >0 的数字后，任务进行到该秒数会自动重置（`0` = 关闭） |

> 使用前请确保 **IN-Q 正在运行**，且触发时游戏窗口处于前台。

## 配置（config.json）

| 键 | 默认值 | 说明 |
| --- | --- | --- |
| `interval_seconds` | `60` | 播报间隔秒数 |
| `precision` | `1` | 时间小数位 |
| `announce_start` | `true` | 任务开始时播报 |
| `announce_abandon` | `false` | 放弃/失败时播报 |
| `reset_on_load` | `true` | 任务中「读条结束」视为新一局（IN-Q 重置依赖它；多区任务换区会误报，可改 `false`） |
| `completion_offset_seconds` | `0.40` | 完成时间补偿，用于对齐结算画面（语音偏大调小、偏小调大，0.02 一档） |
| `reset_hotkey` | `"F11"` | 触发 IN-Q 重置的按键 |
| `auto_reset_seconds` | `0` | 定时重置秒数（`0`=关闭） |
| `tts.engine` | `"edge"` | `edge`=晓晓（需联网）；`sapi`=系统内置 |
| `tts.edge_voice` | `"zh-CN-XiaoxiaoNeural"` | 可换 `zh-CN-XiaoyiNeural` |
| `tts.edge_pitch` | `"+0Hz"` | 音调，如 `"+20Hz"` 更尖细 |
| `texts.*` | 见文件 | 播报文案模板 |

## 从源码构建

```bat
build.bat
```

或手动：

```powershell
pip install pyinstaller edge-tts pystray pillow
python make_icon.py
pyinstaller --onefile --windowed --name "MHW竞速语音计时器" --icon app.ico --add-data "app.ico;." --collect-all edge_tts --collect-all pystray --noconfirm --clean mhw_voice_timer.py
```

## 原理

复用社区 Iceborne 自动分段脚本的特征码，定位 4 个全局指针：

- `sQuest` → `+0x4C` 任务 ID、`+0x9B` 主目标状态（5=完成）
- `sMhGUI` → 载入状态（0=非读条，1/2/3=读条中）
- `sEventDemo` → 过场状态
- `sMhArea` → 区域

「新的一局」= 任务中一次读条的结束（IN-Q 快速重置即触发一次读条）。完成判定 = 主目标状态 1→5 的边沿。

「触发 IN-Q 重置」= 用 `keybd_event` 注入你在 IN-Q 里设置的快捷键（IN-Q 用低级键盘钩子接收，因此合成按键有效）。

## 适用范围

- ✅ 狩猎/讨伐/捕获、调查、活动、历战、竞技场/斗技大会/挑战、采集交付等所有正常任务
- ❌ 探索（自由探索）、聚魔之地（游戏本身不计时）

## 致谢

特征码与字段偏移来自社区 Iceborne 自动分段脚本（[MoonBunnie / JalBagel / GreenSpeed](https://github.com/MoonBunnie/Monster-Hunter-World-Iceborne-AutoSplitter)）。

## 许可与免责声明

本项目以 [MIT](LICENSE) 协议开源，仅供学习交流，与 CAPCOM 无关，使用风险自负。
