"""只读取测试给定PID的可见窗口，用于核对原生标题栏及菜单显示。"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import time

from PIL import ImageGrab


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pid", type=int)
    parser.add_argument("output", type=Path)
    parser.add_argument("--cancel-menu", action="store_true")
    args = parser.parse_args()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetProcessDpiAwarenessContext.argtypes = [wintypes.HANDLE]
    user32.SetProcessDpiAwarenessContext(wintypes.HANDLE(-4))  # 窗口坐标与截图像素使用相同的显示缩放。
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user32.GetDpiForWindow.argtypes = [wintypes.HWND]
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = wintypes.LONG
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    windows = []

    @callback_type
    def collect(hwnd, _parameter):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == args.pid and user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd):
            rectangle = wintypes.RECT()
            if user32.GetWindowRect(hwnd, ctypes.byref(rectangle)):
                area = (rectangle.right - rectangle.left) * (rectangle.bottom - rectangle.top)
                windows.append((area, hwnd, rectangle))
        return True

    user32.EnumWindows(collect, 0)
    if not windows:
        raise RuntimeError(f"测试PID {args.pid} 没有可见窗口")
    _, hwnd, rectangle = max(windows, key=lambda value: value[0])
    was_topmost = bool(user32.GetWindowLongW(hwnd, -20) & 0x00000008)
    flags = 0x0001 | 0x0002 | 0x0010  # 保留尺寸、位置及键盘焦点，只使本测试窗口暂时可见。
    try:
        if not was_topmost and not user32.SetWindowPos(hwnd, wintypes.HWND(-1), 0, 0, 0, 0, flags):
            raise OSError("无法将测试窗口临时置顶以读取实际像素")
        time.sleep(.15)
        # GPU窗口的PrintWindow返回全黑；桌面截图需排除其它窗口的遮挡。
        image = ImageGrab.grab(bbox=(rectangle.left, rectangle.top, rectangle.right, rectangle.bottom), all_screens=True)
    finally:
        if not was_topmost:
            user32.SetWindowPos(hwnd, wintypes.HWND(-2), 0, 0, 0, 0, flags)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    image.save(args.output)
    client_origin = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(client_origin))
    client = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(client))
    scale = user32.GetDpiForWindow(hwnd) / 96
    offset_x, offset_y = client_origin.x - rectangle.left, client_origin.y - rectangle.top
    header_pixel = image.getpixel((offset_x + round(320 * scale), offset_y + round(16 * scale)))
    controls_pixel = image.getpixel((offset_x + client.right - round(110 * scale), offset_y + round(10 * scale)))
    if args.cancel_menu:
        # 原生弹出菜单归测试窗口所有，只向这个已核对的窗口发送取消消息。
        user32.PostMessageW(hwnd, 0x001F, 0, 0)  # WM_CANCELMODE
        user32.PostMessageW(hwnd, 0x0100, 0x1B, 0)  # WM_KEYDOWN / VK_ESCAPE
        user32.PostMessageW(hwnd, 0x0101, 0x1B, 0)
        time.sleep(.1)
    print(json.dumps({"pid": args.pid, "width": image.width, "height": image.height,
                      "scale": scale, "header_pixel": header_pixel, "controls_pixel": controls_pixel}))


if __name__ == "__main__":
    main()
