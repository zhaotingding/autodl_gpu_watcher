from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def main(stage_only=False, icon=None):  # 保留旧参数兼容；所有构建现在都仅暂存，不自动安装。
    sys.path.insert(0, str(ROOT / "src"))
    from autodl_watcher.launcher import check_environment, child_environment
    check_environment()  # 构建同样限定项目Conda环境；不借用用户级依赖。
    folder = ROOT / ".tools" / "build"
    folder.mkdir(parents=True, exist_ok=True)
    icon_path = None
    if icon:
        sys.path.insert(0, str(ROOT / "src"))
        from autodl_watcher.app_icon import load_icon
        data, _, _ = load_icon(icon)
        icon_path = folder / "application.ico"
        icon_path.write_bytes(data)
    sys.path.insert(0, str(ROOT / "src"))
    from autodl_watcher import __version__
    version_path = folder / "version.txt"
    version_tuple = tuple(map(int, __version__.split("."))) + (0,) * (4 - len(__version__.split(".")))
    version_path.write_text(f"""VSVersionInfo(
        ffi=FixedFileInfo(filevers={version_tuple!r}, prodvers={version_tuple!r}, mask=0x3f,
                         flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
        kids=[StringFileInfo([StringTable('080404b0', [
            StringStruct('FileDescription', 'Auto算力与训练调度'),
            StringStruct('ProductName', 'Auto算力与训练调度'),
            StringStruct('FileVersion', '{__version__}'),
            StringStruct('ProductVersion', '{__version__}')])]),
            VarFileInfo([VarStruct('Translation', [2052, 1200])])])
""", encoding="utf-8")
    staged = folder / "launcher"
    command = [sys.executable, "-s", "-m", "PyInstaller", "--noconfirm", "--clean",
               "--onefile", "--noconsole", "--noupx", "--name", "AutoDLWatcher",
               "--distpath", str(staged), "--workpath", str(folder / "launcher-work"),
               "--specpath", str(folder), "--version-file", str(version_path),
               "--icon", str(icon_path or ROOT / "src/autodl_watcher/default.ico"),
               str(ROOT / "tools/launcher_entry.py")]
    subprocess.run(command, cwd=ROOT, env=child_environment(ROOT), check=True)
    print(f"轻量启动器暂存：{staged / 'AutoDLWatcher.exe'}；验收后再复制到仓库根目录。")
    return staged  # 不覆盖正在运行的旧程序、旧_internal或任何个人数据。


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage-only", action="store_true", help="兼容旧调用；现在始终只生成暂存启动器")
    parser.add_argument("--icon", help="将所选ICO/PNG嵌入启动器EXE资源；ICO原样保留")
    args = parser.parse_args()
    main(stage_only=args.stage_only, icon=args.icon)
