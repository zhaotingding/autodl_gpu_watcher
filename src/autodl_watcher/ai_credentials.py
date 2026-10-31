"""Windows Generic Credentials；以完整 API 基础地址绑定密钥，无明文文件回退。"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os



class CredentialError(RuntimeError):
    pass


class _Credential(ctypes.Structure):
    _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]


def _api():
    if os.name != "nt":
        raise CredentialError("安全密钥存储需要 Windows 凭据管理器")
    dll = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    dll.CredWriteW.argtypes = [ctypes.POINTER(_Credential), wintypes.DWORD]
    dll.CredWriteW.restype = wintypes.BOOL
    dll.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(_Credential))]
    dll.CredReadW.restype = wintypes.BOOL
    dll.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    dll.CredDeleteW.restype = wintypes.BOOL
    dll.CredFree.argtypes = [ctypes.c_void_p]
    return dll


def _target(url: str) -> str:
    return _secret_target("llm", url)  # 保留已保存 AI 密钥的原目标名称。


def _secret_target(namespace: str, identifier: str) -> str:
    if not namespace or "/" in namespace or not identifier or "\x00" in namespace + identifier:
        raise CredentialError("凭据命名空间或标识无效")
    return f"autodl-watcher/{namespace}/{identifier}"  # 各用途隔离，避免站点密码覆盖 AI 密钥。


def save(url: str, key: str) -> None:
    key = key.strip()
    if not key or any(c.isspace() for c in key) or len(key.encode("utf-8")) > 2500:
        raise CredentialError("密钥为空、包含空白或长度不合法")
    save_secret("llm", url, key)


def save_secret(namespace: str, identifier: str, value: str) -> None:
    """保存独立用途的秘密；密码空白字符保留，不按 API token 规则裁剪。"""
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 2500:
        raise CredentialError("凭据为空或长度不合法")
    dll = _api()
    blob = (ctypes.c_ubyte * len(value.encode("utf-8"))).from_buffer_copy(value.encode("utf-8"))
    credential = _Credential(Type=1, TargetName=_secret_target(namespace, identifier), CredentialBlobSize=len(blob),
                             CredentialBlob=blob, Persist=2, UserName="autodl-watcher")
    try:
        if not dll.CredWriteW(ctypes.byref(credential), 0):
            raise CredentialError("Windows 无法保存密钥；未写入任何明文配置")
    finally:
        ctypes.memset(blob, 0, len(blob))


def read(url: str) -> str:
    return read_secret("llm", url)


def read_secret(namespace: str, identifier: str) -> str:
    dll = _api()
    pointer = ctypes.POINTER(_Credential)()
    if not dll.CredReadW(_secret_target(namespace, identifier), 1, 0, ctypes.byref(pointer)):
        if ctypes.get_last_error() == 1168:
            return ""
        raise CredentialError("Windows 无法读取密钥，请检查当前登录用户")
    try:
        value = pointer.contents
        return ctypes.string_at(value.CredentialBlob, value.CredentialBlobSize).decode("utf-8")
    finally:
        ctypes.memset(pointer.contents.CredentialBlob, 0, pointer.contents.CredentialBlobSize)
        dll.CredFree(pointer)


def delete(url: str) -> None:
    delete_secret("llm", url)


def delete_secret(namespace: str, identifier: str) -> None:
    if not _api().CredDeleteW(_secret_target(namespace, identifier), 1, 0) and ctypes.get_last_error() != 1168:
        raise CredentialError("Windows 无法删除该接口的密钥")
