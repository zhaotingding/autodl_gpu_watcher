"""读取 Windows 本机进程及 TCP/UDP 表；不发请求、不解析域名、不获取命令行。"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
from functools import lru_cache
import os
import socket
import struct
import time


@dataclass(frozen=True, slots=True)
class ProcessInfo:
    pid: int
    parent_pid: int
    name: str
    created_at: float  # Unix 时间；0 表示权限不足或进程已退出，不能据此确认 PID 身份。


@dataclass(frozen=True, slots=True)
class SocketInfo:
    pid: int
    protocol: str
    local_address: str
    local_port: int
    remote_address: str
    remote_port: int
    state: str


@dataclass(frozen=True, slots=True)
class SystemSnapshot:
    processes: tuple[ProcessInfo, ...]
    sockets: tuple[SocketInfo, ...]
    errors: tuple[str, ...]
    captured_at: float  # 本轮读取开始时刻；启动更晚的进程不能认领早先表中的同号 PID。


_DWORD = ctypes.c_uint32  # Win32 DWORD 固定四字节，不随 Python 位数或运行平台改变。
_BOOL = ctypes.c_int32
_HANDLE = ctypes.c_void_p
_AF_INET, _AF_INET6 = 2, 23  # Windows Winsock 的地址族编号。
_INSUFFICIENT_BUFFER = 122
_NO_MORE_FILES = 18
_MAX_TABLE_BYTES = 64 * 1024 * 1024  # 仅约束系统返回缓冲区，不截断成功读取的连接行。
_TCP_STATES = {1: "CLOSED", 2: "LISTEN", 3: "SYN_SENT", 4: "SYN_RCVD", 5: "ESTABLISHED",
               6: "FIN_WAIT1", 7: "FIN_WAIT2", 8: "CLOSE_WAIT", 9: "CLOSING", 10: "LAST_ACK",
               11: "TIME_WAIT", 12: "DELETE_TCB"}


class _Tcp4(ctypes.Structure):
    _fields_ = [(name, _DWORD) for name in ("state", "local_address", "local_port",
                                           "remote_address", "remote_port", "pid")]


class _Tcp6(ctypes.Structure):
    _fields_ = [("local_address", ctypes.c_ubyte * 16), ("local_scope", _DWORD), ("local_port", _DWORD),
                ("remote_address", ctypes.c_ubyte * 16), ("remote_scope", _DWORD), ("remote_port", _DWORD),
                ("state", _DWORD), ("pid", _DWORD)]


class _Udp4(ctypes.Structure):
    _fields_ = [(name, _DWORD) for name in ("local_address", "local_port", "pid")]


class _Udp6(ctypes.Structure):
    _fields_ = [("local_address", ctypes.c_ubyte * 16), ("local_scope", _DWORD),
                ("local_port", _DWORD), ("pid", _DWORD)]


class _FileTime(ctypes.Structure):
    _fields_ = [("low", _DWORD), ("high", _DWORD)]


class _ProcessEntry(ctypes.Structure):
    _fields_ = [("size", _DWORD), ("usage", _DWORD), ("pid", _DWORD), ("heap", ctypes.c_size_t),
                ("module_id", _DWORD), ("threads", _DWORD), ("parent_pid", _DWORD),
                ("priority", ctypes.c_int32), ("flags", _DWORD), ("name", ctypes.c_wchar * 260)]


@lru_cache(maxsize=1)
def _libraries():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)  # 原生只读 API，避免每次刷新启动 PowerShell/WMI。
    ip = ctypes.WinDLL("iphlpapi", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [_DWORD, _DWORD]
    kernel.CreateToolhelp32Snapshot.restype = _HANDLE
    for function in (kernel.Process32FirstW, kernel.Process32NextW):
        function.argtypes, function.restype = [_HANDLE, ctypes.POINTER(_ProcessEntry)], _BOOL
    kernel.OpenProcess.argtypes, kernel.OpenProcess.restype = [_DWORD, _BOOL, _DWORD], _HANDLE
    kernel.CloseHandle.argtypes, kernel.CloseHandle.restype = [_HANDLE], _BOOL
    kernel.GetProcessTimes.argtypes = [_HANDLE, *([ctypes.POINTER(_FileTime)] * 4)]
    kernel.GetProcessTimes.restype = _BOOL
    for function in (ip.GetExtendedTcpTable, ip.GetExtendedUdpTable):
        function.argtypes = [ctypes.c_void_p, ctypes.POINTER(_DWORD), _BOOL, _DWORD, ctypes.c_int32, _DWORD]
        function.restype = _DWORD
    return kernel, ip


def _creation_time(kernel, pid):
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION，不要求提权或读取进程内存。
    if not handle:
        return 0.0  # 系统保护进程和枚举后已退出的进程常见，不扩展为全表失败。
    try:
        created, exited, system, user = (_FileTime() for _ in range(4))
        if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                      ctypes.byref(system), ctypes.byref(user)):
            return 0.0
        return ((created.high << 32) | created.low) / 10_000_000 - 11_644_473_600  # FILETIME 自 1601 年起计时。
    finally:
        kernel.CloseHandle(handle)  # 每次成功打开的进程句柄均有对应释放。


def _processes(kernel):
    handle = kernel.CreateToolhelp32Snapshot(2, 0)  # TH32CS_SNAPPROCESS 只枚举进程元数据。
    if handle == ctypes.c_void_p(-1).value or handle is None:
        raise OSError(ctypes.get_last_error(), "CreateToolhelp32Snapshot 失败")
    try:
        entry = _ProcessEntry()
        entry.size = ctypes.sizeof(entry)
        if not kernel.Process32FirstW(handle, ctypes.byref(entry)):
            raise OSError(ctypes.get_last_error(), "Process32FirstW 失败")
        result = []
        while True:
            result.append(ProcessInfo(entry.pid, entry.parent_pid, entry.name,
                                      _creation_time(kernel, entry.pid)))
            if not kernel.Process32NextW(handle, ctypes.byref(entry)):
                error = ctypes.get_last_error()
                if error != _NO_MORE_FILES:
                    raise OSError(error, "Process32NextW 失败")  # 不将不完整的进程树伪装成完整结果。
                return tuple(result)
    finally:
        kernel.CloseHandle(handle)


def _table_rows(data, row_type):
    class Table(ctypes.Structure):
        _fields_ = [("count", _DWORD), ("rows", row_type * 1)]

    offset = Table.rows.offset  # 使用 ABI 的实际偏移，避免假定表头之后绝无对齐填充。
    if len(data) < offset:
        raise ValueError("系统连接表缺少完整表头")
    count = int.from_bytes(data[:ctypes.sizeof(_DWORD)], "little")
    size = ctypes.sizeof(row_type)
    if count > (len(data) - offset) // size:
        raise ValueError("系统连接表长度与行数不一致")
    return tuple(row_type.from_buffer_copy(data, offset + index * size) for index in range(count))


def _address(value, ipv6, scope=0):
    if not ipv6:
        return socket.inet_ntop(socket.AF_INET, struct.pack("<I", value))
    address = socket.inet_ntop(socket.AF_INET6, bytes(value))
    return f"{address}%{socket.ntohl(scope)}" if scope else address  # IPv6 scope 与端口同样由网络字节序转换。


def _decode_table(data, protocol, family):
    ipv6 = family == _AF_INET6
    row_type = {("TCP", False): _Tcp4, ("TCP", True): _Tcp6,
                ("UDP", False): _Udp4, ("UDP", True): _Udp6}[protocol, ipv6]
    result = []
    for row in _table_rows(data, row_type):
        local = _address(row.local_address, ipv6, row.local_scope if ipv6 else 0)
        remote, port = "", 0  # UDP 表不提供真实对端；TCP 监听行的对端字段也没有连接语义。
        state = "BOUND" if protocol == "UDP" else _TCP_STATES.get(row.state, f"UNKNOWN({row.state})")
        if protocol == "TCP" and state != "LISTEN":
            remote = _address(row.remote_address, ipv6, row.remote_scope if ipv6 else 0)
            port = socket.ntohs(row.remote_port & 0xffff)
        result.append(SocketInfo(row.pid, protocol, local, socket.ntohs(row.local_port & 0xffff),
                                 remote, port, state))
    return tuple(result)


def _socket_table(function, protocol, family):
    size = _DWORD()
    table_class = 5 if protocol == "TCP" else 1  # TCP_TABLE_OWNER_PID_ALL / UDP_TABLE_OWNER_PID。
    code = function(None, ctypes.byref(size), False, family, table_class, 0)
    if code not in (0, _INSUFFICIENT_BUFFER):
        raise OSError(code, "查询连接表大小失败")
    for _ in range(3):
        if not ctypes.sizeof(_DWORD) <= size.value <= _MAX_TABLE_BYTES:
            raise ValueError("系统连接表缓冲区大小无效")
        buffer = ctypes.create_string_buffer(size.value)
        code = function(buffer, ctypes.byref(size), False, family, table_class, 0)
        if code == _INSUFFICIENT_BUFFER:
            continue  # 两次查询之间连接数可能变化，只作有界重读。
        if code:
            raise OSError(code, "读取连接表失败")
        if size.value > len(buffer):
            raise ValueError("系统连接表返回的长度超过缓冲区")
        return _decode_table(buffer.raw[:size.value], protocol, family)
    raise OSError(_INSUFFICIENT_BUFFER, "系统连接表连续变化，本次快照未完成")


def read_system_snapshot() -> SystemSnapshot:
    captured_at = time.time()  # 必须早于表读取，供上层排除采样期间 PID 被新进程复用的歧义。
    if os.name != "nt":
        return SystemSnapshot((), (), ("系统采集：当前仅支持 Windows 本机连接表",), captured_at)
    try:
        kernel, ip = _libraries()
    except (OSError, AttributeError) as exc:
        return SystemSnapshot((), (), (f"系统采集：无法加载 Windows 连接接口：{exc}",), captured_at)
    errors, sockets = [], []
    for protocol, function in (("TCP", ip.GetExtendedTcpTable), ("UDP", ip.GetExtendedUdpTable)):
        for family, version in ((_AF_INET, "IPv4"), (_AF_INET6, "IPv6")):
            try:
                sockets.extend(_socket_table(function, protocol, family))
            except (OSError, ValueError) as exc:
                errors.append(f"{protocol}/{version}：{exc}")  # 某张表不可读时，不拿空列表冒充零连接。
    try:
        processes = _processes(kernel)  # 后读进程身份，减少旧连接 PID 被新进程复用而误归属的窗口。
        current = next((item for item in processes if item.pid == os.getpid()), None)
        if current is None or current.created_at <= 0:
            errors.append("进程身份：当前进程启动时间不可读，无法完整确认本实例归属")
    except OSError as exc:
        processes = ()
        errors.append(f"进程快照：{exc}")
    return SystemSnapshot(processes, tuple(sockets), tuple(errors), captured_at)
