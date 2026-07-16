from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class ForegroundInfo:
    process: str = ""
    title: str = ""
    pid: int = 0
    hwnd: int = 0


def get_foreground_info() -> ForegroundInfo:
    if os.name != "nt":
        return ForegroundInfo()
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ForegroundInfo()
    length = user32.GetWindowTextLengthW(hwnd)
    title_buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, title_buffer, length + 1)
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    process_name = ""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if handle:
        try:
            size = ctypes.c_ulong(32768)
            path_buffer = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, path_buffer, ctypes.byref(size)):
                process_name = Path(path_buffer.value).name
        finally:
            kernel32.CloseHandle(handle)
    return ForegroundInfo(process=process_name, title=title_buffer.value, pid=int(pid.value), hwnd=int(hwnd))


def activate_window(hwnd: int) -> bool:
    if os.name != "nt" or not hwnd:
        return False
    try:
        return bool(ctypes.windll.user32.SetForegroundWindow(hwnd))
    except (AttributeError, OSError):
        return False
