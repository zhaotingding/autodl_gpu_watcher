"""关闭GUI后更新EXE图标，保留PyInstaller附加的程序归档。"""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import shutil
import struct
import sys
from .yaml_settings import read_settings, write_settings


def pe_end(data):
    pe = struct.unpack_from("<I", data, 60)[0]
    count, optional = struct.unpack_from("<H", data, pe + 6)[0], struct.unpack_from("<H", data, pe + 20)[0]
    sections = pe + 24 + optional
    return max(sum(struct.unpack_from("<II", data, sections + i * 40 + 16)) for i in range(count))


def replace_icon(executable, icon):
    original = executable.read_bytes()
    overlay = original[pe_end(original):]
    candidate = executable.with_name(executable.stem + "-icon.tmp")
    shutil.copyfile(executable, candidate)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.BeginUpdateResourceW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL]
    kernel.BeginUpdateResourceW.restype = wintypes.HANDLE
    kernel.UpdateResourceW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, wintypes.WORD, ctypes.c_void_p, wintypes.DWORD]
    kernel.EndUpdateResourceW.argtypes = [wintypes.HANDLE, wintypes.BOOL]
    handle = kernel.BeginUpdateResourceW(str(candidate), False)
    if not handle:
        candidate.unlink()
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        data = icon.read_bytes()
        reserved, kind, count = struct.unpack_from("<HHH", data)
        if reserved or kind != 1 or not count:
            raise ValueError("无效ICO")
        group = bytearray(data[:6])
        for index in range(count):
            entry = data[6 + index * 16:22 + index * 16]
            size, offset = struct.unpack_from("<II", entry, 8)
            image = data[offset:offset + size]
            group.extend(entry[:12] + struct.pack("<H", index + 1))
            if not kernel.UpdateResourceW(handle, 3, index + 1, 0, image, len(image)):
                raise ctypes.WinError(ctypes.get_last_error())
        raw = bytes(group)
        if not kernel.UpdateResourceW(handle, 14, 1, 0, raw, len(raw)):
            raise ctypes.WinError(ctypes.get_last_error())
    except Exception:
        kernel.EndUpdateResourceW(handle, True)
        candidate.unlink()
        raise
    if not kernel.EndUpdateResourceW(handle, False):
        candidate.unlink()
        raise ctypes.WinError(ctypes.get_last_error())
    # Windows重写资源可能移动/移除overlay；按新PE末尾重新附加原归档。
    changed = candidate.read_bytes()
    candidate.write_bytes(changed[:pe_end(changed)] + overlay)
    backup = executable.parent / ".ui" / "icon-executable-backup.exe"
    backup.parent.mkdir(exist_ok=True)
    shutil.copyfile(executable, backup)
    try:
        candidate.replace(executable)
    finally:
        candidate.unlink(missing_ok=True)
    shell = ctypes.windll.shell32
    shell.SHChangeNotify(0x08000000, 0, None, None)
    shell.SHChangeNotify(0x2000, 0x0005, str(executable), None)
    shell.SHChangeNotify(0x1000, 0x0005, str(executable.parent), None)


def main():
    pid = int(sys.argv[1])
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    root = Path(os.environ["AUTODL_APP_ROOT"])
    status = root / ".ui" / "icon-executable-status.yaml"
    try:
        handle = kernel.OpenProcess(0x100000, False, pid)
        if handle:
            result = kernel.WaitForSingleObject(handle, 120000)
            kernel.CloseHandle(handle)
            if result != 0:
                raise TimeoutError("GUI尚未关闭，EXE图标未更新")
        name = read_settings(root / ".ui/icon.yaml")["file"]
        icon = root / ".ui" / name if name else Path(__file__).with_name("default.ico")
        replace_icon(root / "AutoDLWatcher.exe", icon)
        message = "EXE图标已同步；请刷新目录后查看。"
    except (OSError, ValueError) as exc:
        message = "EXE图标同步失败：" + str(exc)
    write_settings(status, {"message": message})
