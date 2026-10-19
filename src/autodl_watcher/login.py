from __future__ import annotations  # AutoDL 登录初始化与会话恢复。

import os
import shutil
import subprocess
import time
from pathlib import Path

from .config import AppConfig, load_config
from .session import emit_session


_AUTOMATION_FLAGS = ("--no-sandbox", "--remote-debugging-pipe", "--headless")
_PROFILE_IGNORE_NAMES = {
    "SingletonCookie",
    "SingletonLock",
    "SingletonSocket",
    "lockfile",
}


def edge_executable_candidates() -> tuple[Path, ...]:  # 返回 Windows 上常见的 Microsoft Edge 可执行文件候选路径。
    candidates: list[Path] = []
    path_hit = shutil.which("msedge")
    if path_hit:
        candidates.append(Path(path_hit))

    for env_name in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        base = os.environ.get(env_name)
        if base:
            candidates.append(Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe")

    unique: list[Path] = []
    seen: set[str] = set()
    for item in candidates:
        key = str(item).lower()  # Windows 路径大小写通常不区分，按小写去掉重复候选。
        if key not in seen:
            unique.append(item)
            seen.add(key)
    return tuple(unique)


def find_edge_executable() -> Path | None:  # 查找系统安装的 Microsoft Edge；找不到时返回 None。
    for candidate in edge_executable_candidates():
        if candidate.is_file():
            return candidate
    return None


def login_profile_dir(browser_profile: Path) -> Path:  # 返回与监控 profile 同级的纯人工登录 profile。
    return browser_profile.parent / "native_login_profile"


def _ps_quote(value: str) -> str:  # PowerShell 单引号字符串转义。
    return value.replace("'", "''")


def _edge_process_query_script(user_data_dir: Path, *, kill: bool, close_windows: bool = False) -> str:  # 构造只匹配指定 user-data-dir 的 Edge 进程查询/清理脚本。
    marker = _ps_quote(str(user_data_dir))
    action = (
        "ForEach-Object { Stop-Process -Id $PSItem.ProcessId -Force "
        "-ErrorAction SilentlyContinue }"
        if kill
        else "Select-Object -ExpandProperty CommandLine"
    )
    if close_windows:  # 只向该专用profile所属进程发送正常关窗请求，保留浏览器写盘机会。
        action = ("ForEach-Object { $edge = Get-Process -Id $PSItem.ProcessId "
                  "-ErrorAction SilentlyContinue; if ($edge) { $null = $edge.CloseMainWindow() } }")
    return (
        "$p='" + marker + "'; "
        "Get-CimInstance Win32_Process -ErrorAction Stop | "
        "Where-Object { $PSItem.Name -eq 'msedge.exe' -and "
        "$PSItem.CommandLine -and $PSItem.CommandLine.Contains($p) } | "
        + action
    )


class LocalStartupError(RuntimeError):  # 本地组件缺失不代表网络故障或登录失效。
    pass


def _powershell_executable() -> str:  # 使用系统绝对路径，GUI继承精简PATH时也可启动辅助进程。
    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if not executable.is_file():
        raise LocalStartupError(f"未找到 Windows PowerShell：{executable}；请修复系统组件后重试")
    return str(executable)


def edge_process_command_lines(user_data_dir: Path) -> list[str]:  # 读取使用指定 profile 的 Edge 进程命令行；非 Windows 返回空列表。
    if os.name != "nt":
        return []
    completed = subprocess.run(
        [
            _powershell_executable(),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            _edge_process_query_script(user_data_dir, kill=False),
        ],
        capture_output=True,  # 辅助进程输出交给 Python 读取，不单独弹终端供用户查看。
        text=True,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW,  # 只隐藏 PowerShell 辅助进程，人工登录的 Edge 窗口仍然可见。
    )
    if completed.returncode != 0:
        raise LocalStartupError("无法查询专用 Edge 进程，已停止登录资料同步；请重试")  # 查询失败不能当成浏览器已经退出。
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def terminate_profile_edge_processes(user_data_dir: Path) -> None:  # 静默关闭仅使用指定专用 profile 的 Edge 进程。
    if os.name != "nt":
        return
    subprocess.run(
        [
            _powershell_executable(),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            _edge_process_query_script(user_data_dir, kill=True),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW,  # 只隐藏 PowerShell 辅助进程，人工登录的 Edge 窗口仍然可见。
    )


def close_profile_edge_windows(user_data_dir: Path) -> None:  # 用户确认登录后，正常关闭本程序专用Edge窗口。
    if os.name != "nt":
        return
    completed = subprocess.run(
        [_powershell_executable(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-Command", _edge_process_query_script(user_data_dir, kill=False, close_windows=True)],
        capture_output=True, text=True, check=False,
        creationflags=subprocess.CREATE_NO_WINDOW,  # 辅助进程隐藏，正常关窗交给Edge自身处理。
    )
    if completed.returncode != 0:
        raise LocalStartupError("无法请求关闭登录 Edge，未同步登录资料；请手动关闭登录窗口后重试")


def automation_flags_present(user_data_dir: Path) -> bool:  # 判断指定 profile 的 Edge 是否混入 Playwright/自动化启动参数。
    lines = edge_process_command_lines(user_data_dir)
    return any(flag in line for line in lines for flag in _AUTOMATION_FLAGS)


def wait_for_profile_edge_exit(
    user_data_dir: Path,
    timeout_seconds: float = 5.0,
) -> bool:  # 等待指定 profile 的 Edge 进程完全退出。
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while time.monotonic() < deadline:
        if not edge_process_command_lines(user_data_dir):
            return True
        time.sleep(0.1)
    return not edge_process_command_lines(user_data_dir)


def remove_stale_profile_locks(user_data_dir: Path) -> None:  # 在相关 Edge 已退出后删除残留的 Chromium profile 锁文件。
    if not user_data_dir.exists():
        return
    for child in user_data_dir.iterdir():
        if (
            child.name in _PROFILE_IGNORE_NAMES
            or child.name.startswith("Singleton")
            or child.name == "DevToolsActivePort"
        ):
            try:
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
            except OSError:
                pass


def prepare_profile_for_exclusive_use(user_data_dir: Path) -> None:  # 彻底释放 watcher 专用 profile，避免残留 Edge/锁文件导致启动失败。
    terminate_profile_edge_processes(user_data_dir)
    if not wait_for_profile_edge_exit(user_data_dir):  # 未释放的 profile 不能删锁或交给另一个进程。
        raise RuntimeError("专用 Edge 尚未退出，已停止操作登录资料；请关闭该窗口后重试")
    remove_stale_profile_locks(user_data_dir)


def launch_native_edge(
    edge_path: Path,
    user_data_dir: Path,
    page_url: str,
    *,
    proxy_bypass_list: str = "",
) -> subprocess.Popen[bytes]:  # 启动不受 Playwright 控制的普通 Edge。
    user_data_dir.mkdir(parents=True, exist_ok=True)
    command = [str(edge_path), f"--user-data-dir={user_data_dir}", "--new-window",
               "--disable-background-mode"]  # 关闭登录窗口后正常退出，避免后台模式阻碍资料交接。
    if proxy_bypass_list.strip():
        command.append(f"--proxy-bypass-list={proxy_bypass_list.strip()}")
    command.append(page_url)
    return subprocess.Popen(command)


def _ignore_profile_copy(_directory: str, names: list[str]) -> set[str]:  # 复制 profile 时忽略锁文件；缓存保留与否不影响登录态。
    ignored = set()
    for name in names:
        if name in _PROFILE_IGNORE_NAMES or name.startswith("Singleton"):
            ignored.add(name)
    return ignored


def sync_login_profile_to_browser(login_profile: Path, browser_profile: Path) -> None:  # 把纯人工登录 profile 同步为监控使用的 browser_profile。
    if not login_profile.exists():
        raise RuntimeError(f"登录 profile 不存在：{login_profile}")

    close_profile_edge_windows(login_profile)  # 确认按钮负责正常关窗，不能仅等待用户已经关闭。
    if not wait_for_profile_edge_exit(login_profile, timeout_seconds=30.0):  # 给人工登录正常写盘留出时间，不强杀源浏览器。
        raise RuntimeError("登录 Edge 尚未退出，未同步或覆盖监控资料；请处理该窗口的关闭确认并关闭后重试")
    remove_stale_profile_locks(login_profile)  # 确认进程已退出后才移除失效锁。
    prepare_profile_for_exclusive_use(browser_profile)

    if browser_profile.exists():
        shutil.rmtree(browser_profile)
    shutil.copytree( login_profile, browser_profile, ignore=_ignore_profile_copy, )


def _notify_login_required() -> None:  # Windows 下播放提示音；失败不影响登录流程。
    if os.name != "nt":
        return
    try:
        import winsound

        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        time.sleep(0.15)
        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
    except Exception:
        return


def interactive_login(config: AppConfig | None = None, *, reason: str = "manual") -> None:  # 用纯人工 Edge 完成登录，并把会话同步给监控 profile。
    config = config or load_config()
    browser_profile = config.platform.user_data_dir
    clean_profile = login_profile_dir(browser_profile)
    browser_profile.parent.mkdir(parents=True, exist_ok=True)

    edge_path = find_edge_executable()
    if edge_path is None:
        raise RuntimeError(
            "未找到系统 Microsoft Edge。v0.5.1 在 Windows 上不回退到 Playwright "
            "做人工验证码登录，请先安装/修复 Edge。"
        )

    prepare_profile_for_exclusive_use(browser_profile)  # v0.5.1：登录前彻底释放两个专用 profile，并清除残留锁文件。 人工验证码只在 native_login_profile 的普通 Edge 中完成。
    prepare_profile_for_exclusive_use(clean_profile)

    if reason == "expired":
        _notify_login_required()
        print("\n检测到 AutoDL 登录会话失效，已自动打开普通 Microsoft Edge。")
    else:
        print("正在打开普通 Microsoft Edge 登录窗口。")

    print("该窗口使用独立 native_login_profile，不带 Playwright 的 --no-sandbox / headless 参数。")
    print("完成 AutoDL 登录并确认能看到“所有主机”后，点击程序的“完成登录并关闭窗口”。")
    print("终端模式按 Enter 继续；程序会正常关闭专用登录窗口并等待保存，验证码仍需手动完成。")

    bypass = (
        config.platform.proxy_bypass_list
        if getattr(config.platform, "autodl_direct", False)
        else ""
    )
    if bypass:
        if bypass:
            launch_native_edge(
                edge_path,
                clean_profile,
                config.platform.page_url,
                proxy_bypass_list=bypass,
            )
        else:
            launch_native_edge(edge_path, clean_profile, config.platform.page_url)
    else:
        launch_native_edge(edge_path, clean_profile, config.platform.page_url)
    time.sleep(1.5)

    if automation_flags_present(clean_profile):
        prepare_profile_for_exclusive_use(clean_profile)  # 理论上不会发生；若系统复用了污染进程，则强制清理并再启动一次。
        launch_native_edge( edge_path, clean_profile, config.platform.page_url, proxy_bypass_list=bypass, )
        time.sleep(1.5)
        if automation_flags_present(clean_profile):
            prepare_profile_for_exclusive_use(clean_profile)
            raise RuntimeError( "普通 Edge 仍检测到自动化启动参数。为避免验证码继续失败，已主动停止登录。" )

    input("\n登录完成后按 Enter，正常关闭专用 Edge 并同步：")
    emit_session("checking", "正在正常关闭登录 Edge，等待保存登录资料")  # 与认证失败区分，避免仍显示上一次过期结果。
    sync_login_profile_to_browser(clean_profile, browser_profile)
    print("登录资料已同步，实际会话状态仍待接口核验。")
    emit_session("checking", "登录资料已同步，等待实际接口核验")


def main() -> None:
    from .collectors import PlatformAuthenticationError, PlatformBrowserCollector, PlatformTransientError

    collector = None
    try:
        from .paths import prepare_application, default_config_path
        prepare_application()
        config = load_config(default_config_path())
        interactive_login(config, reason="manual")
        emit_session("checking", "正在核验同步后的登录会话")
        collector = PlatformBrowserCollector(config.platform)
        collector.collect()  # 复制Cookie不是登录成功，必须由真实主机接口确认。
        emit_session("valid", "登录会话有效，已通过实际接口核验")
    except LocalStartupError as exc:
        emit_session("unknown", f"本地启动失败：{exc}")
        raise SystemExit(5) from None
    except PlatformAuthenticationError as exc:
        emit_session("invalid", f"登录会话失效，请重新登录：{exc}")
        raise SystemExit(3) from None
    except PlatformTransientError as exc:
        emit_session("unknown", f"网络或响应异常，无法确认会话：{exc}")
        raise SystemExit(4) from None
    except Exception as exc:
        emit_session("unknown", f"登录核验失败，无法确认会话：{exc}")
        raise SystemExit(1) from None
    finally:
        if collector is not None:
            collector.close()


if __name__ == "__main__":
    main()
