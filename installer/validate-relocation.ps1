param(
    [string]$OldDirectory,
    [string]$NewDirectory,
    [string]$ExecutableName,
    [string]$UninstallCommand,
    [switch]$FromEnvironment,
    [switch]$Repair
)

$ErrorActionPreference = 'Stop'
if ($FromEnvironment) {
    $OldDirectory = $env:AUTODL_RELOCATION_OLD_DIRECTORY
    $NewDirectory = $env:AUTODL_RELOCATION_NEW_DIRECTORY
    $ExecutableName = $env:AUTODL_RELOCATION_EXECUTABLE_NAME
    $UninstallCommand = $env:AUTODL_RELOCATION_UNINSTALL_COMMAND
}

function Get-CheckedLocation([string]$Value, [string]$Label) {
    # 拒绝设备路径、盘相对路径和 Win32 会忽略的尾随字符。
    if ($Value -notmatch '^[a-zA-Z]:[\\/]' -or $Value.Substring(2) -match '[:*?"<>|\x00-\x1F]' -or
        ($Value -split '[\\/]' | Where-Object { $_ -match '[. ]$' -and $_ -notin @('.', '..') })) {
        throw "$Label 需要有效的本地绝对路径。"
    }
    $full = [IO.Path]::GetFullPath($Value.Replace('/', '\'))
    $ancestor = [IO.DirectoryInfo]::new($full)
    $existing = $null
    while ($ancestor) {
        try { $item = Get-Item -LiteralPath $ancestor.FullName -Force }
        catch [System.Management.Automation.ItemNotFoundException] {
            $ancestor = $ancestor.Parent
            continue
        }
        if (-not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "$Label 不能包含文件、目录联接或符号链接。"
        }
        if (-not $existing) { $existing = $ancestor.FullName }
        $ancestor = $ancestor.Parent
    }
    if (-not $existing) { throw "无法访问 $Label 所在磁盘。" }

    # 比较实际卷上的路径，避免盘符别名或 8.3 短名称绕过重叠检查。
    $verified = [AutodlRelocationLocation]::ForDirectory($existing).TrimEnd('\')
    $suffix = $full.Substring($existing.TrimEnd('\').Length).Trim('\')
    $key = if ($suffix) { "$verified\$suffix" } else { $verified }
    [pscustomobject]@{ Full = $full.TrimEnd('\'); Key = $key; Existing = $existing }
}

function Test-Overlap([string]$Left, [string]$Right) {
    $comparison = [StringComparison]::OrdinalIgnoreCase
    return $Left.Equals($Right, $comparison) -or $Left.StartsWith($Right + '\', $comparison) -or
        $Right.StartsWith($Left + '\', $comparison)
}

function Get-InstalledPackage([string]$Archive) {
    # 只读asar中的package.json，沿用Electron asar的Pickle头和文件偏移格式。
    $stream = [IO.File]::OpenRead($Archive)
    $reader = [IO.BinaryReader]::new($stream)
    try {
        if ($reader.ReadUInt32() -ne 4) { throw '现有程序的asar头无效。' }
        $headerSize = $reader.ReadUInt32()
        $payloadSize = $reader.ReadUInt32()
        $jsonSize = $reader.ReadUInt32()
        if ($headerSize -lt 8 -or $headerSize -gt 16777216 -or $headerSize + 8 -gt $stream.Length -or
            $payloadSize -ne $headerSize - 4 -or $jsonSize + 8 -gt $headerSize) {
            throw '现有程序的asar头不完整。'
        }
        $header = ConvertFrom-Json ([Text.Encoding]::UTF8.GetString($reader.ReadBytes($jsonSize)))
        $entry = $header.files.'package.json'
        if (-not $entry -or $entry.unpacked -or $entry.link -or $entry.offset -notmatch '^\d+$' -or
            $entry.size -le 0 -or $entry.size -gt 1048576) { throw '现有程序缺少可识别的安装元数据。' }
        $position = 8 + [long]$headerSize + [long]$entry.offset
        if ($position + $entry.size -gt $stream.Length) { throw '现有程序的安装元数据不完整。' }
        $stream.Position = $position
        ConvertFrom-Json ([Text.Encoding]::UTF8.GetString($reader.ReadBytes($entry.size)))
    } finally {
        $reader.Dispose()
    }
}

try {
    $profileName = switch ($ExecutableName) {
        'autodl-desktop.exe' { 'autodl-watcher' }
        'autodl-update-test.exe' { 'autodl-watcher-update-test' }
        default { throw '无法识别现有安装的产品身份。' }
    }
    if (-not $Repair) {
        if ($UninstallCommand -notmatch '^"([^"\r\n]+)"(?:\s+/currentuser)?\s*$') {
            throw '原卸载登记无效，无法安全迁移。'
        }
        $registeredUninstaller = $Matches[1]
    }
    Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;

public static class AutodlRelocationLocation {
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    private static extern SafeFileHandle CreateFile(string path, uint access, uint share,
        IntPtr security, uint mode, uint flags, IntPtr template);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    private static extern uint GetFinalPathNameByHandle(SafeFileHandle handle, StringBuilder path,
        uint length, uint flags);

    public static string ForDirectory(string path) {
        using (var handle = CreateFile(path, 0, 7, IntPtr.Zero, 3, 0x02000000, IntPtr.Zero)) {
            var result = new StringBuilder(32768);
            uint length = handle.IsInvalid ? 0 :
                GetFinalPathNameByHandle(handle, result, (uint)result.Capacity, 1);
            if (length == 0 || length >= result.Capacity) {
                throw new IOException("Cannot verify the physical directory path.");
            }
            return result.ToString();
        }
    }
}
'@

    $old = Get-CheckedLocation $OldDirectory '原程序目录'
    $new = Get-CheckedLocation $NewDirectory '新程序目录'
    if ($Repair) {
        if (-not $old.Key.Equals($new.Key, [StringComparison]::OrdinalIgnoreCase)) { throw '修复安装必须使用同一个原程序目录。' }
    } elseif (Test-Overlap $old.Key $new.Key) { throw '新程序目录必须与原程序目录分开。' }
    if (-not (Test-Path -LiteralPath $old.Full -PathType Container)) { throw '原程序目录不存在。' }
    if ($old.Full -eq [IO.Path]::GetPathRoot($OldDirectory).TrimEnd('\') -or
        $new.Full -eq [IO.Path]::GetPathRoot($NewDirectory).TrimEnd('\')) {
        throw '不能使用磁盘根目录。'
    }

    $expectedUninstaller = Join-Path $old.Full "Uninstall $ExecutableName"
    if (-not $Repair -and -not $registeredUninstaller.Equals($expectedUninstaller, [StringComparison]::OrdinalIgnoreCase)) {
        throw '原卸载登记与原程序目录不一致。'
    }
    $program = Join-Path $old.Full $ExecutableName
    $requiredFiles = if ($Repair) { @($program, (Join-Path $old.Full 'resources/app.asar')) }
        else { @($expectedUninstaller, $program) }
    foreach ($file in $requiredFiles) {
        $item = Get-Item -LiteralPath $file -Force -ErrorAction SilentlyContinue
        if (-not $item -or $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw '原程序或卸载器不完整，无法安全迁移。'
        }
    }
    if ($Repair) {
        # 登记缺失时通过本产品的PE身份和随包元数据识别原程序，允许原位修复。
        $testBuild = $ExecutableName -eq 'autodl-update-test.exe'
        $product = if ($testBuild) { 'Auto算力与训练调度（更新验收）' } else { 'Auto算力与训练调度' }
        $name = if ($testBuild) { 'autodl-update-test' } else { 'autodl-watcher-desktop' }
        $identity = [Diagnostics.FileVersionInfo]::GetVersionInfo($program)
        if ($identity.ProductName -ne $product -or $identity.FileDescription -ne $product) {
            throw '所选目录未识别为本应用的已有程序目录，请选择本应用的安装位置。'
        }
        $package = Get-InstalledPackage (Join-Path $old.Full 'resources/app.asar')
        if ($package.name -ne $name -or $package.autodlInstalled -ne $true -or
            $package.autodlLocalUpdateTest -ne $testBuild) {
            throw '所选目录未识别为本应用的已有程序目录，请选择本应用的安装位置。'
        }
    }
    foreach ($name in @('.ui', 'runtime', 'config.local.yaml', 'config.yaml')) {
        if (Test-Path -LiteralPath (Join-Path $old.Full $name)) {
            throw '原程序目录内还有应用资料，无法安全迁移。'
        }
    }
    # 旧版卸载器递归清理程序目录。不要让程序里的目录联接指向原资料或其他位置。
    $pending = [System.Collections.Generic.Stack[string]]::new()
    $pending.Push($old.Full)
    while ($pending.Count) {
        foreach ($item in Get-ChildItem -LiteralPath ($pending.Pop()) -Force) {
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw '原程序目录内有目录联接或符号链接，无法安全迁移。'
            }
            if ($item.PSIsContainer) { $pending.Push($item.FullName) }
        }
    }

    # 程序卸载器会移除整个原程序目录。默认根和已记住的资料根都要保护。
    if (-not $env:LOCALAPPDATA) { throw '无法确认当前用户的资料位置。' }
    $protected = foreach ($name in @('autodl-watcher', 'autodl-watcher-update-test')) {
        @{ Path = (Join-Path $env:LOCALAPPDATA $name); AllowProjectInstall = $false }
    }
    $selectionFile = Join-Path (Join-Path $env:LOCALAPPDATA $profileName) 'profile-path.yaml'
    if (Test-Path -LiteralPath $selectionFile) {
        try { $selected = ConvertFrom-Json -InputObject ([IO.File]::ReadAllText($selectionFile)) }
        catch { throw '无法读取已保存的资料位置，已停止迁移。' }
        if ($selected -isnot [string]) { throw '已保存的资料位置无效，已停止迁移。' }
        $protected += @{ Path = $selected; AllowProjectInstall = $true }
    }
    foreach ($folder in $protected) {
        $profile = Get-CheckedLocation $folder.Path '个人资料目录'
        $allowed = $null
        # 原资料根就是源码项目时，可把程序放在其专用子目录。卸载只处理 installed/。
        if ($folder.AllowProjectInstall -and
            (Test-Path -LiteralPath (Join-Path $profile.Full 'src/autodl_watcher/__init__.py') -PathType Leaf) -and
            (Test-Path -LiteralPath (Join-Path $profile.Full 'installer') -PathType Container)) {
            $allowed = $profile.Key + '\installed'
        }
        foreach ($program in $old, $new) {
            if (-not $program.Key.Equals($allowed, [StringComparison]::OrdinalIgnoreCase) -and
                (Test-Overlap $program.Key $profile.Key)) {
                throw '原目录和新目录都必须与个人资料目录分开。'
            }
        }
    }

    if (-not $Repair -and (Test-Path -LiteralPath $new.Full)) {
        if (Get-ChildItem -LiteralPath $new.Full -Force | Select-Object -First 1) {
            throw '请选择一个新的或空的程序文件夹。'
        }
    }

    # 在最近的现有目标目录试写独占探针，关闭句柄即删除，不创建所选目录。
    $probe = Join-Path $new.Existing ('.autodl-relocation-' + [guid]::NewGuid())
    try {
        $stream = [IO.FileStream]::new($probe, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write,
            [IO.FileShare]::None, 1, [IO.FileOptions]::DeleteOnClose)
        try { $stream.WriteByte(1) } finally { $stream.Dispose() }
    } catch {
        throw '新程序目录或其父目录不可写，请选择其他位置。'
    }
} catch {
    $message = $_.Exception.Message -split '\r?\n' | Select-Object -First 1
    if ($FromEnvironment) {
        [IO.File]::WriteAllText((Join-Path $PSScriptRoot 'relocation-error.txt'), "$message`r`n",
            [Text.UnicodeEncoding]::new($false, $false))
    } else {
        [Console]::Error.WriteLine($message)
    }
    exit 1
}
exit 0
