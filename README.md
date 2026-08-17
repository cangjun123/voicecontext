# VoiceContext

VoiceContext 是一个常驻 Windows 系统托盘的语音上下文输入工具。它将实时语音、复制到剪贴板的内容及两者的发生时间组合起来，生成一句可以直接交给 Codex、Claude Code、Cursor 或 ChatGPT 的自然语言指令。

它不是单纯把声音转换成文字，而是让语音中的“这个”“这里”“刚才那个”真正指向你当时复制的内容。

## 与普通语音识别的最大区别

普通语音识别只有一条输入通道：声音。它能正确识别字面内容，却不知道“这个东西”具体是什么。

假设你一边说：

```text
把这个东西和这个东西合并一下。
```

一边进行两次复制：

1. 说到第一个“这个东西”时，复制 `DataCore-2`。
2. 说到第二个“这个东西”时，复制 `Stroke chunks`。

两种工具得到的结果不同：

| 工具 | 最终结果 |
| --- | --- |
| 普通语音识别 | `把这个东西和这个东西合并一下。` |
| VoiceContext | `把 DataCore-2 和 Stroke chunks 合并一下。` |

VoiceContext 会记录每次复制的毫秒时间、复制时的实时转写快照以及剪贴板原文。LLM 先根据时间线把两个指代分别解析为 `[ref_1]` 和 `[ref_2]`，随后程序在本地将占位符替换为原始内容。因此，模型负责理解指代，程序负责精确保留引用内容。

这也是项目的核心定位：**不是更换一个 ASR，而是在 ASR 之上增加可操作的上下文。**

## 主要能力

- 阿里云百炼 Fun-ASR 实时 WebSocket 识别，边说边出字
- 支持 OpenAI 兼容转写接口和自定义远程 ASR API
- 语音会话期间自动捕获文本、代码、错误、路径和 URL
- 在实时转写中用 `[1]`、`[2]` 标出每次复制发生的位置
- 按复制时间和实时转写快照消解重复指代
- 使用 OpenAI 兼容的流式 LLM 接口整理口语表达
- 生成纯自然语言指令，不强制附加 `Task`、`References` 等结构
- 全局快捷键启动、结束、暂停、撤销引用和重新复制结果
- 快捷键支持点击后直接录制，也支持鼠标侧键
- 完成后在悬浮窗显示结果，4 秒后自动消失；点击可进入编辑器修改
- “保存并复制”会回读校验剪贴板，只有校验成功后才执行自动粘贴
- LLM 空响应或超时会明确提示失败，不静默回退，也不会覆盖剪贴板
- 本地保存会话历史，API 密钥存入 Windows Credential Manager

## 一次完整使用过程

1. 按“切换听取状态”快捷键开始说话。
2. 说到需要引用的“这个”时复制内容，悬浮窗会在当前转写位置显示编号锚点。
3. 再按同一个快捷键结束听取并开始合成。
4. 合成成功后，悬浮窗显示最终结果和复制状态；忽略它即可继续工作。
5. 需要修改时点击悬浮窗，在编辑器中调整结果，再点击“保存并复制”。

完成回执会记录 ASR 状态、引用数量、LLM 模型与耗时、Token 用量以及剪贴板校验结果，便于在结果异常时判断问题发生在哪一步。

## 安装与运行

推荐使用项目脚本创建独立的 Python 3.12 环境：

```powershell
.\install.ps1
.\run.ps1
```

也可以使用已有的 Python 环境：

```powershell
python -m pip install -e .
python -m voice_context
```

首次启动后，在“设置”中配置 ASR、LLM、快捷键和输出方式。配置与历史记录位于 `%APPDATA%\VoiceContext`；API 密钥不会写入 `config.json` 或 Git 仓库。

## 阿里云百炼实时 ASR

在 ASR 设置中选择“阿里云百炼实时识别（WebSocket）”，模型填写 `fun-asr-realtime`。程序会将麦克风的 16 kHz 单声道 PCM 音频直接发送到实时接口。

北京地域的百炼工作空间地址可以使用以下任一形式：

```text
workspace.cn-beijing.maas.aliyuncs.com
https://workspace.cn-beijing.maas.aliyuncs.com/api/v1
https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
```

VoiceContext 会自动转换为实时 WebSocket 地址：

```text
wss://workspace.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference
```

设置中的热词表 ID 是阿里云词表资源 ID，不是本地逗号分隔的热词列表。

## 远程 ASR

VoiceContext 仅支持远程 ASR，不会加载或运行本地语音模型。除阿里云百炼实时 WebSocket 外，还可以选择：

- OpenAI 兼容转写 API：向 `{base_url}/audio/transcriptions` 上传 WAV 录音。
- 自定义 ASR API：支持直接配置以 `/transcribe`、`/transcription` 或 `/audio/transcriptions` 结尾的接口地址。

从旧版本升级时，如果原配置使用本地 FunASR 或 faster-whisper，程序会自动迁移到 OpenAI 兼容远程转写配置。请在“设置 > ASR”中确认 API 地址、模型和密钥。

## 测试

```powershell
pytest
ruff check voice_context tests tools
```

## 构建 Windows 程序

```powershell
.\build.ps1
```

生成的便携版程序位于：

```text
dist\VoiceContext\VoiceContext.exe
```

PyInstaller 配置会明确排除 FunASR、faster-whisper、Torch、ONNX Runtime 等本地模型运行时，避免构建环境中残留的机器学习依赖进入发布包。

需要单文件版本时运行：

```powershell
.\build-onefile.ps1
```

生成文件位于 `dist\VoiceContext.exe`。单文件版启动时会将 Qt 和运行时依赖解压到系统临时目录，因此首次启动通常比目录版稍慢。
