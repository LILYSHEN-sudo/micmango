<h1 id="english"><img src="assets/micmango-logo.png" width="88" alt="MicMango logo"><br>MicMango</h1>

**Local push-to-talk dictation for macOS.**

<p><a href="#english"><img alt="English" src="https://img.shields.io/badge/-English-0969da?style=for-the-badge"></a> <a href="#chinese"><img alt="简体中文" src="https://img.shields.io/badge/-%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-d0d7de?style=for-the-badge"></a></p>

MicMango is a push-to-talk dictation tool that runs entirely on your Mac. Hold the right Option key to speak, then release it to transcribe your speech with MLX Whisper and paste the result into the active text field.

It does not depend on a cloud speech API and has no character quota. Transcription and input history stay on your computer.

## What is MLX Whisper?

[Whisper](https://github.com/openai/whisper) is OpenAI's open-source family of multilingual speech-recognition models. [MLX](https://github.com/ml-explore/mlx) is Apple's machine-learning framework designed for Apple Silicon, with support for the Mac's CPU, GPU, and unified memory. [MLX Whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper) is an implementation that runs Whisper models through MLX, making local Whisper inference well suited to Apple Silicon Macs.

MicMango uses `mlx-community/whisper-large-v3-turbo`, a Whisper checkpoint converted to the MLX format. An internet connection is required to download the model from Hugging Face the first time it runs. After that, speech recognition runs locally, and MicMango does not send recordings to a cloud transcription service. MLX's unified-memory design also allows the CPU and GPU to access the same memory pool without repeatedly copying model data. See [Apple's MLX unified-memory documentation](https://ml-explore.github.io/mlx/build/html/usage/unified_memory.html) for more details.

## Features

- Local transcription with `mlx-community/whisper-large-v3-turbo` on Apple Silicon.
- Hold right Option to record; release it to transcribe and paste.
- Automatic, Chinese-first, and English language modes.
- Hold Shift + right Option to temporarily use English mode.
- Mixed Chinese-English `initial_prompt` support and configurable term replacements.
- Traditional-to-Simplified Chinese conversion with OpenCC.
- Automatic comma insertion after clear pauses, using a default threshold of 1.0 second.
- Fast decoding without timestamps, with model and compiled-state reuse in a persistent process.
- Silence, low-volume, and all-zero audio detection to prevent Whisper hallucinations.
- Restoration of the previous plain-text clipboard contents after pasting.
- A single-instance lock to prevent duplicate listeners and repeated pastes.
- Local SQLite history with daily character totals and one-click copying.
- A local web console for starting, stopping, and reviewing dictation history.

By default, runtime logs show only the detected language, processing time, and character count. They do not print the full transcript. Complete text is stored only in the local history database.

## Requirements

- An Apple Silicon Mac.
- macOS 14 or later.
- Python 3.10.
- [`uv`](https://docs.astral.sh/uv/) for creating the environment and installing dependencies.
- A working microphone.

Closing the lid of an Apple Silicon MacBook disconnects its built-in microphone at the hardware level. When using a closed MacBook with an external display, select an external microphone such as AirPods, DJI MIC MINI, or another audio-input device.

## Installation

Open the project directory:

```bash
cd /path/to/MicMango
```

Create the Python environment and install the dependencies:

```bash
uv venv --python 3.10 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

On the first run, MLX Whisper downloads the model into the local Hugging Face cache. Later launches reuse the downloaded model.

## macOS permissions

Before the first run, open **System Settings → Privacy & Security** and grant the terminal application used to launch MicMango permission for:

- Microphone
- Accessibility
- Input Monitoring

After changing these permissions, quit the terminal application completely, reopen it, and then start MicMango again.

## Selecting a microphone

List the available audio devices:

```bash
.venv/bin/python micmango.py --list-devices
```

You can select a device by name or index. Names are usually more stable than numeric indexes:

```bash
--device "Your Microphone Name"
```

If `--device` is omitted, MicMango uses the current macOS default input device.

## Running MicMango

### Option 1: Local web console

This is the recommended way to run MicMango:

```bash
.venv/bin/python server.py --open
```

The console first checks `audio_device` in `local-only/config.json`, then falls back to the macOS default input device. You can temporarily override it:

```bash
.venv/bin/python server.py --device "Your Microphone Name" --open
```

Your browser opens:

```text
http://127.0.0.1:8766
```

The console provides:

- Start and stop controls.
- Current model and microphone status.
- Today's character count.
- Character totals for the last seven days.
- Dictation history grouped by date.
- One-click copying of previous transcripts.

Keep the terminal window running. Press Control + C or close the window to stop both the console and its managed dictation process.

### Option 2: Launch from Finder

Double-click this file in Finder:

```text
Start MicMango.command
```

The launcher reads `local-only/config.json`. If no microphone is configured, it uses the macOS default input device.

### Option 3: Run only the dictation process

If you do not need the web console:

```bash
.venv/bin/python micmango.py --language auto
```

The core process still saves dictation history. Start `server.py` separately if you want to view that history in the browser.

## Hotkeys

```text
Right Option          Hold to record; release to transcribe and paste
Shift + Right Option  Temporarily use English mode
Control + C           Stop MicMango in the current terminal
```

## Configuration

Create a private configuration from the included template:

```bash
mkdir -p local-only
cp config.example.json local-only/config.json
```

The entire `local-only/` directory is excluded from version control. It is intended for personal configuration, history, recordings, notes, and development files.

Common settings:

| Setting | Default | Purpose |
| --- | --- | --- |
| `default_language` | `auto` | Default mode: `auto`, `zh`, or `en` |
| `audio_device` | `null` | Input device used when `--device` is not provided |
| `minimum_recording_seconds` | `0.3` | Ignore accidental or extremely short recordings |
| `maximum_recording_seconds` | `120.0` | Maximum duration of one recording |
| `minimum_rms_dbfs` | `-55.0` | RMS silence threshold |
| `minimum_peak_dbfs` | `-35.0` | Peak silence threshold |
| `restore_clipboard` | `true` | Restore the previous plain-text clipboard after pasting |
| `simplify_chinese` | `true` | Convert Chinese output to Simplified Chinese |
| `record_history` | `true` | Save local dictation history |
| `pause_punctuation_seconds` | `1.0` | Pause duration used for automatic comma insertion |
| `print_transcript_in_log` | `false` | Print complete transcripts in the runtime log |

Command-line arguments override `local-only/config.json`. For example, `--device "Your Microphone Name"` overrides `audio_device`.

## Local data and privacy

Dictation history is stored at:

```text
local-only/data/history.sqlite3
```

The database contains:

- Final transcript text.
- Input timestamp and local date.
- Detected language.
- Character count.
- Model transcription time.

The entire `local-only/` directory is listed in `.gitignore` and will not be committed. MicMango does not upload recordings or history, but you should still treat this database as private when backing up or sharing the complete project directory.

## Diagnostics and single-file transcription

Run these commands from the MicMango project directory.

Check dependencies, permissions, and microphone access:

```bash
.venv/bin/python micmango.py --check
```

Send an audio file through the complete transcription and text-cleaning pipeline:

```bash
.venv/bin/python micmango.py --transcribe /path/to/your-audio.m4a --language auto
```

## Troubleshooting

### Right Option does nothing

Stop any running MicMango instance, then enable event diagnostics:

```bash
.venv/bin/python micmango.py --debug-events
```

If right Option events do not appear, check Input Monitoring permission for your terminal application.

### Transcription works, but automatic paste does not

Check Accessibility permission for your terminal application. MicMango must send Command + V to the active application.

### The volume is always -240 dBFS

```text
rms=-240.0 dBFS, peak=-240.0 dBFS
```

This means every captured audio sample is zero; it does not mean that your voice is merely too quiet.

- If you are using the built-in MacBook microphone, make sure the lid is open.
- Select an external microphone when using the MacBook with its lid closed.
- Check that the external microphone is powered on, connected, and not muted.

### MicMango is already running

MicMango uses a single-instance lock to prevent multiple processes from listening for the same hotkey. Stop dictation from the existing console or close the original launcher window before starting another instance.

## Project structure

```text
MicMango/
├── micmango.py             # Hotkey, recording, model, text processing, and paste
├── server.py              # Local web server and process management
├── index.html             # Local console and history interface
├── history_store.py       # SQLite history storage
├── config.example.json    # Configuration template
├── requirements.txt       # Python dependencies
└── Start MicMango.command  # Finder launcher
```

Local development also creates `.venv/` and `local-only/`. Both directories are fully excluded from Git and are not uploaded to GitHub.

## Current limitations

- MicMango is not yet a signed macOS `.app`; it must be launched through a terminal.
- macOS permissions belong to the terminal application that launches MicMango, not to a standalone MicMango application.
- Clipboard protection restores plain text only; it does not preserve rich text or image clipboard contents.
- Pause punctuation uses a low-latency estimate based on silent audio regions rather than word-level alignment.
- The history page supports viewing and copying, but not search, editing, export, or deletion.
- Menu-bar language switching and a graphical settings interface are not yet implemented.

---

<h1 id="chinese"><img src="assets/micmango-logo.png" width="88" alt="MicMango logo"><br>MicMango（简体中文）</h1>

**macOS 本地按键语音输入工具。**

<p><a href="#english"><img alt="English" src="https://img.shields.io/badge/-English-d0d7de?style=for-the-badge"></a> <a href="#chinese"><img alt="简体中文" src="https://img.shields.io/badge/-%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-0969da?style=for-the-badge"></a></p>

MicMango 是一个完全在 Mac 本机运行的按键语音输入工具。按住右 Option 说话，松开后通过 MLX Whisper 转写，并把文字粘贴到当前输入框。

它不依赖云端语音 API，没有字符额度限制；录音、转写和输入历史都保存在本机。

## MLX Whisper 是什么

[Whisper](https://github.com/openai/whisper) 是 OpenAI 开源的多语言语音识别模型系列；[MLX](https://github.com/ml-explore/mlx) 是 Apple 为 Apple Silicon 设计的机器学习框架，可以利用 Mac 的 CPU、GPU 和统一内存。[MLX Whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper) 则是在 MLX 上运行 Whisper 的实现，让 Whisper 模型能够针对 Apple Silicon 在本地执行。

MicMango 使用已经转换为 MLX 格式的 `mlx-community/whisper-large-v3-turbo`。第一次运行需要联网从 Hugging Face 下载模型；下载完成后，识别过程在 Mac 本机进行，MicMango 不会把录音发送给云端语音服务。MLX 的统一内存设计也意味着 CPU 和 GPU 可以访问同一份内存，无需来回复制模型数据。更多技术细节可查看 [Apple MLX 统一内存说明](https://ml-explore.github.io/mlx/build/html/usage/unified_memory.html)。

## 功能

- 使用 `mlx-community/whisper-large-v3-turbo` 在 Apple Silicon Mac 上本地转写。
- 按住右 Option 录音，松开后转写并自动粘贴。
- 支持自动识别、中文优先和 English 三种语言模式。
- Shift + 右 Option 临时使用 English 模式。
- 使用中英混排 `initial_prompt` 和可配置术语替换词典。
- 使用 OpenCC 把中文输出统一转换为简体。
- 根据录音中的明显停顿自动补充逗号，默认阈值为 1.0 秒。
- 使用无时间戳快速解码，并在常驻进程中复用模型和编译结果。
- 静音、低音量和全零录音拦截，避免 Whisper 产生静音幻觉。
- 粘贴后恢复原来的纯文本剪贴板内容。
- 单实例保护，避免多个启动入口同时监听并重复粘贴。
- 每段成功转写写入本地 SQLite 历史，按天统计字符数并支持再次复制。
- 独立本地网页控制台，可启动、停止和查看语音输入历史。

运行日志默认只显示语言、耗时和字符数，不重复打印完整转写；完整文本只保存在本地历史数据库中。

## 系统要求

- Apple Silicon Mac。
- macOS 14 或更高版本。
- Python 3.10。
- `uv`，用于创建环境和安装依赖。
- 一个可用的麦克风。

Apple Silicon Mac 合盖时会在硬件层断开内置麦克风。合盖连接外接显示器使用时，需要选择 DJI MIC MINI、AirPods 或其他外接麦克风。

## 安装

进入项目目录：

```bash
cd /path/to/MicMango
```

创建 Python 环境并安装依赖：

```bash
uv venv --python 3.10 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

首次运行时，MLX Whisper 会自动把模型下载到 Hugging Face 本地缓存。后续运行直接使用本地模型。

## macOS 权限

首次运行需要在「系统设置 → 隐私与安全性」中为启动 MicMango 的 Terminal 开启：

- 麦克风
- 辅助功能
- 输入监控

修改权限后，应完全退出 Terminal 并重新打开，再启动 MicMango。

## 选择麦克风

先查看可用设备：

```bash
.venv/bin/python micmango.py --list-devices
```

设备可以使用名称或编号指定。名称通常比编号更稳定：

```bash
--device "Your Microphone Name"
```

如果不指定 `--device`，MicMango 使用 macOS 当前的默认输入设备。

## 运行

### 方式一：独立网页控制台

推荐使用独立控制台：

```bash
.venv/bin/python server.py --open
```

控制台依次使用 `local-only/config.json` 中的 `audio_device` 和 macOS 默认输入设备。也可以临时覆盖：

```bash
.venv/bin/python server.py --device "Your Microphone Name" --open
```

浏览器会打开：

```text
http://127.0.0.1:8766
```

控制台提供：

- 启动和停止语音输入。
- 当前模型与麦克风状态。
- 今日输入字符数。
- 最近七天字符量。
- 按日期查看历史。
- 一键复制历史文本。

运行该命令的 Terminal 窗口需要保持打开。按 Control + C 或关闭窗口会停止控制台和它管理的语音输入进程。

### 方式二：Finder 双击启动

也可以在 Finder 中双击：

```text
Start MicMango.command
```

启动脚本会读取 `local-only/config.json`；未配置麦克风时使用 macOS 默认输入设备。

### 方式三：只运行核心语音输入

不需要网页控制台时，可以直接运行：

```bash
.venv/bin/python micmango.py --language auto
```

直接运行核心进程仍会保存输入历史，但需要另外启动 `server.py` 才能在网页中查看。

## 快捷键

```text
右 Option          按住录音，松开转写并粘贴
Shift + 右 Option  临时使用 English 模式
Control + C        退出当前 Terminal 中的 MicMango
```

## 配置

如需自定义设置，复制配置模板：

```bash
mkdir -p local-only
cp config.example.json local-only/config.json
```

整个 `local-only/` 文件夹已排除在版本控制之外，可以安全存放个人配置、历史记录、录音和开发资料。

常用配置：

| 配置 | 默认值 | 作用 |
| --- | --- | --- |
| `default_language` | `auto` | 默认语言模式：`auto`、`zh` 或 `en` |
| `audio_device` | `null` | 核心进程未收到 `--device` 时使用的设备 |
| `minimum_recording_seconds` | `0.3` | 忽略过短录音和误触 |
| `maximum_recording_seconds` | `120.0` | 单次最长录音秒数 |
| `minimum_rms_dbfs` | `-55.0` | RMS 静音阈值 |
| `minimum_peak_dbfs` | `-35.0` | Peak 静音阈值 |
| `restore_clipboard` | `true` | 粘贴后恢复原纯文本剪贴板 |
| `simplify_chinese` | `true` | 把中文统一转换为简体 |
| `record_history` | `true` | 保存本地输入历史 |
| `pause_punctuation_seconds` | `1.0` | 自动补逗号的停顿阈值 |
| `print_transcript_in_log` | `false` | 是否在运行日志打印完整转写 |

命令行参数优先于 `local-only/config.json`。例如，`--device "Your Microphone Name"` 会覆盖配置中的 `audio_device`。

## 本地数据与隐私

输入历史保存在：

```text
local-only/data/history.sqlite3
```

数据库包含：

- 最终转写文本。
- 输入时间和本地日期。
- 检测语言。
- 字符数。
- 模型转写耗时。

整个 `local-only/` 目录已加入 `.gitignore`，不会随代码提交。MicMango 不会主动上传录音或历史数据，但在备份或分享整个项目目录时，仍应把数据库视为私人数据。

## 检查与单文件转写

以下命令均应在 MicMango 项目目录运行。

检查依赖、权限和麦克风：

```bash
.venv/bin/python micmango.py --check
```

让单个音频走完整的模型与文本清理链路：

```bash
.venv/bin/python micmango.py --transcribe /path/to/your-audio.m4a --language auto
```

## 故障排查

### 右 Option 没有反应

先停止当前正在运行的 MicMango 实例，再使用事件调试模式：

```bash
.venv/bin/python micmango.py --debug-events
```

如果没有看到右 Option 事件，请检查 Terminal 的输入监控权限。

### 可以转写但不能自动粘贴

检查 Terminal 的辅助功能权限。MicMango 需要发送 Command + V 到当前应用。

### 音量始终是 -240 dBFS

```text
rms=-240.0 dBFS, peak=-240.0 dBFS
```

这表示收到的音频采样全部为零，而不是说话声音太小。

- 如果使用 MacBook 内置麦克风，请确认电脑没有合盖。
- 合盖使用时，请选择外接麦克风。
- 检查外接麦克风是否开机、配对并解除静音。

### 提示 MicMango 已经在运行

MicMango 使用单实例锁，防止两个进程同时监听快捷键。请先在现有控制台中停止语音输入，或关闭原来的启动窗口。

## 项目结构

```text
MicMango/
├── micmango.py             # 快捷键、录音、模型、文本处理和粘贴
├── server.py               # 本地 Web 服务和进程管理
├── index.html              # 独立控制台和历史页面
├── history_store.py        # SQLite 历史记录
├── config.example.json     # 配置模板
├── requirements.txt        # Python 依赖
└── Start MicMango.command  # Finder 双击启动入口
```

本机开发目录还会包含 `.venv/` 和 `local-only/`；两者都被完整忽略，不会上传到 GitHub。

## 当前限制

- 还不是签名的 macOS `.app`，需要通过 Terminal 启动。
- 权限目前归属于启动 MicMango 的 Terminal，而不是独立应用。
- 剪贴板保护只保存和恢复纯文本，不保留富文本或图片剪贴板。
- 停顿标点是基于音频静音区间的低延迟估算，不是逐词时间对齐。
- 历史页面目前支持查看和复制，不支持搜索、编辑、导出或删除。
- 菜单栏语言切换和图形化设置界面尚未实现。
