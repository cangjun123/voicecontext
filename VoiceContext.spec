# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = [
    "keyring.backends.Windows",
    "sounddevice",
    "keyboard",
    "mouse",
]
for optional_package in ("faster_whisper", "funasr", "funasr_onnx", "jieba", "dashscope"):
    try:
        hiddenimports += collect_submodules(optional_package)
    except Exception:
        pass

a = Analysis(
    ["launcher.py"],
    pathex=["."],
    binaries=[],
    datas=[("assets/icon.png", "assets")],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=1,
)

# Qt6Core uses the ICU implementation shipped by Windows. If Anaconda is on
# PATH, PyInstaller may resolve Qt6Core's generic icuuc.dll dependency to
# Anaconda's private, versioned ICU build instead. Putting that DLL in the
# application root makes the bootloader prefer it over the compatible system
# DLL and PySide6.QtCore then fails to import with WinError 127.
def is_foreign_icu(binary):
    name = binary[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    return name == "icuuc.dll" or (name.startswith("icudt") and name.endswith(".dll"))


a.binaries = [binary for binary in a.binaries if not is_foreign_icu(binary)]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VoiceContext",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="VoiceContext",
)
