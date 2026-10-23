"""关闭GUI后更新EXE图标，保留PyInstaller附加的程序归档。"""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import shutil
import struct
import sys
from .yaml_settings import read_settings, write_settings


def launcher_target(value):
    if not value:  # 没有明确入口时，源码 GUI 只更新窗口图标。
        return None
    path = Path(value)  # 必须由轻量启动器传入绝对路径，不能解释为当前目录下的旧成品。
    if (not path.is_absolute() or path.suffix.lower() != ".exe" or not path.is_file()
            or path.name.lower() in {"python.exe", "pythonw.exe"}):
        raise OSError("轻量启动器路径无效；请从有效的项目启动器重新打开应用。")  # 防止误写解释器本身。
    return path


def pe_end(data):
    pe = struct.unpack_from("<I", data, 60)[0]
    count, optional = struct.unpack_from("<H", data, pe + 6)[0], struct.unpack_from("<H", data, pe + 20)[0]
    sections = pe + 24 + optional
    return max(sum(struct.unpack_from("<II", data, sections + i * 40 + 16)) for i in range(count))


def replace_icon(executable, icon, *, backup_dir=None):
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
    backup_folder = Path(backup_dir) if backup_dir is not None else executable.parent / ".ui"  # 资料根可与启动器目录分离。
    backup = backup_folder / "icon-executable-backup.exe"
    backup.parent.mkdir(parents=True, exist_ok=True)  # 不向外部项目解释器目录写入资料。
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
    result_code = 0  # 模块入口用退出码区分资源同步成功与失败。
    try:
        executable = launcher_target(sys.argv[2])  # 目标独立于 AUTODL_APP_ROOT，不再拼接根目录旧 EXE。
        if executable is None:
            raise ValueError("缺少轻量启动器路径")
        handle = kernel.OpenProcess(0x100000, False, pid)
        if handle:
            result = kernel.WaitForSingleObject(handle, 120000)
            kernel.CloseHandle(handle)
            if result != 0:
                raise TimeoutError("GUI尚未关闭，EXE图标未更新")
        name = read_settings(root / ".ui/icon.yaml")["file"]
        icon = root / ".ui" / name if name else Path(__file__).with_name("default.ico")
        replace_icon(executable, icon, backup_dir=root / ".ui")  # 备份与状态保存在用户选定资料根。
        message = "EXE图标已同步；请刷新目录后查看。"
    except (OSError, ValueError) as exc:
        message = "EXE图标同步失败：" + str(exc)
        result_code = 1  # 不把资源更新失败包装成模块调用成功。
    write_settings(status, {"message": message})
    return result_code


if __name__ == "__main__":
    raise SystemExit(main())  # 外部项目 Python 直接执行模块，无需冻结 worker。
