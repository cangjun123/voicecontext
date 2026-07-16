# VoiceContext

VoiceContext is a Windows tray application that combines spoken intent with clipboard references and produces structured prompts for coding agents.

## Run

```powershell
python -m pip install -e .
python -m voice_context
```

On Windows, the project scripts create and use an isolated Python 3.12 environment:

```powershell
.\install.ps1
.\run.ps1
```

The first launch uses the rule-based composer and an OpenAI-compatible transcription endpoint placeholder. Open **Settings** to choose an ASR provider, add credentials, change global hotkeys, and select an output template.

Configuration and history are stored under `%APPDATA%\VoiceContext`. API keys are stored through the operating-system credential service and are never written to the JSON configuration.

## Aliyun Model Studio realtime ASR

Choose **Aliyun Model Studio realtime (WebSocket)** to stream 16 kHz mono PCM directly from the microphone and display service-side intermediate results as they arrive. This path does not run a local model or perform an offline final pass.

For a Beijing workspace, the API base URL can be entered in any of these forms:

```text
workspace.cn-beijing.maas.aliyuncs.com
https://workspace.cn-beijing.maas.aliyuncs.com/api/v1
https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
```

VoiceContext derives the realtime endpoint as `wss://workspace.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference`. Use `fun-asr-realtime` as the model. The optional vocabulary ID is an Alibaba Cloud vocabulary resource ID, not a comma-separated local hotword list.

## Optional local speech engines

```powershell
python -m pip install -e ".[whisper]"
# or
python -m pip install -e ".[funasr]"
```

With the Windows installer script:

```powershell
.\install.ps1 -WithFunASR
```

For ModelScope ONNX exports, the FunASR model path may point either to the concrete Paraformer directory or to its `iic` parent directory, for example:

```text
C:\Users\you\.cache\modelscope\hub\models\iic
```

VoiceContext automatically selects the matching Chinese/English Paraformer model and discovers sibling FSMN VAD and punctuation models. Build the executable only after installing the selected optional engine; PyInstaller can bundle only packages present in the build environment.

## Tests

```powershell
pytest
```

## Build a Windows executable

```powershell
.\build.ps1
```

The portable application is created at `dist\VoiceContext\VoiceContext.exe`. The base build supports remote ASR and LLM services. Install a local engine before building when it should be bundled.
