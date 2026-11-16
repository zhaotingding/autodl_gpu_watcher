$ErrorActionPreference = 'Stop'
$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$scratch = Join-Path $tempRoot ('autodl-relocation-test-' + [guid]::NewGuid())
$testDirectory = New-Item -ItemType Directory -Path $scratch
$localAppData = $env:LOCALAPPDATA
$helper = Join-Path $scratch 'validate-relocation.ps1'
$exeName = 'autodl-update-test.exe'
$link = Join-Path $scratch '程序联接'
$project = Join-Path $scratch 'Auto算力与训练调度项目'
$installed = Join-Path $project 'installed'
$oldProgramLink = Join-Path $installed '资料联接'
$repair = Join-Path $scratch '修复程序'
$repairLink = Join-Path $repair '资料联接'
$envNames = @('AUTODL_RELOCATION_OLD_DIRECTORY', 'AUTODL_RELOCATION_NEW_DIRECTORY',
    'AUTODL_RELOCATION_EXECUTABLE_NAME', 'AUTODL_RELOCATION_UNINSTALL_COMMAND')
$previousEnv = @{}
foreach ($name in $envNames) { $previousEnv[$name] = [Environment]::GetEnvironmentVariable($name) }

function Assert-Validation([string]$Old, [string]$New, [string]$Command, [string]$Expected, [bool]$Repair = $false) {
    # 使用真实 Windows PowerShell 解析参数和退出码，避免依赖测试调用者的版本。
    $env:AUTODL_RELOCATION_OLD_DIRECTORY = $Old
    $env:AUTODL_RELOCATION_NEW_DIRECTORY = $New
    $env:AUTODL_RELOCATION_EXECUTABLE_NAME = $exeName
    $env:AUTODL_RELOCATION_UNINSTALL_COMMAND = $Command
    $errorFile = Join-Path $scratch 'relocation-error.txt'
    if (Test-Path -LiteralPath $errorFile) { Remove-Item -LiteralPath $errorFile }
    $arguments = @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', $helper, '-FromEnvironment')
    if ($Repair) { $arguments += '-Repair' }
    & powershell.exe @arguments
    $exitCode = $LASTEXITCODE
    if (-not $Expected) {
        if ($exitCode -ne 0 -or (Test-Path -LiteralPath $errorFile)) {
            $detail = if (Test-Path -LiteralPath $errorFile) { [IO.File]::ReadAllText($errorFile, [Text.Encoding]::Unicode) }
            throw "合法迁移未通过预检：$detail"
        }
    } else {
        if ($exitCode -ne 1 -or -not (Test-Path -LiteralPath $errorFile)) { throw "应拒绝：$Expected" }
        $detail = [IO.File]::ReadAllText($errorFile, [Text.Encoding]::Unicode)
        if (-not $detail.Contains($Expected)) { throw "应拒绝：$Expected，实际：$detail" }
    }
}

function Write-RepairArchive([string]$Name = 'autodl-update-test', [bool]$TestBuild = $true) {
    # 使用真实asar头格式构造极小元数据样本，避免复制Electron运行时。
    $package = [Text.Encoding]::UTF8.GetBytes((ConvertTo-Json -Compress @{
        name=$Name; autodlInstalled=$true; autodlLocalUpdateTest=$TestBuild
    }))
    $header = [Text.Encoding]::UTF8.GetBytes((ConvertTo-Json -Depth 4 -Compress @{
        files=@{'package.json'=@{size=$package.Length; offset='0'}}
    }))
    $payloadSize = [int]([Math]::Ceiling(($header.Length + 4) / 4.0) * 4)
    $stream = [IO.File]::Create((Join-Path $repair 'resources/app.asar'))
    $writer = [IO.BinaryWriter]::new($stream)
    try {
        $writer.Write([uint32]4)
        $writer.Write([uint32]($payloadSize + 4))
        $writer.Write([uint32]$payloadSize)
        $writer.Write([uint32]$header.Length)
        $writer.Write($header)
        while ($stream.Position -lt $payloadSize + 12) { $writer.Write([byte]0) }
        $writer.Write($package)
    } finally {
        $writer.Dispose()
    }
}

try {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot '../installer/validate-relocation.ps1') -Destination $helper
    $env:LOCALAPPDATA = Join-Path $scratch '本机资料'
    $old = Join-Path $scratch '原程序'
    $new = Join-Path $scratch '新程序'
    $uninstaller = Join-Path $old "Uninstall $exeName"
    New-Item -ItemType Directory -Path $old, $env:LOCALAPPDATA | Out-Null
    New-Item -ItemType File -Path $uninstaller, (Join-Path $old $exeName) | Out-Null
    $uninstallCommand = '"' + $uninstaller + '" /currentuser'

    Assert-Validation $old $new $uninstallCommand ''
    if (Test-Path -LiteralPath $new) { throw '预检不应创建新程序目录' }
    if (Get-ChildItem -LiteralPath $scratch -Force -Filter '.autodl-relocation-*') { throw '预检探针未删除' }
    Assert-Validation $old (Join-Path $old.ToUpperInvariant() '另一个位置') $uninstallCommand '与原程序目录分开'
    Assert-Validation $old $new ('"' + (Join-Path $scratch "Uninstall $exeName") + '" /currentuser') '与原程序目录不一致'

    New-Item -ItemType Directory -Path $new | Out-Null
    New-Item -ItemType File -Path (Join-Path $new '现有文件.txt') | Out-Null
    Assert-Validation $old $new $uninstallCommand '新的或空的程序文件夹'
    $nonempty = Join-Path $new '现有文件.txt'
    Assert-Validation $old (Join-Path $nonempty '无法创建') $uninstallCommand '不能包含文件'

    New-Item -ItemType Directory -Path (Join-Path $old '.ui') | Out-Null
    Assert-Validation $old (Join-Path $scratch '备选程序') $uninstallCommand '原程序目录内还有应用资料'
    Remove-Item -LiteralPath (Join-Path $old '.ui')

    $testProfileFolder = Join-Path $env:LOCALAPPDATA 'autodl-watcher-update-test'
    New-Item -ItemType Directory -Path $testProfileFolder | Out-Null
    $profile = Join-Path $scratch '保留资料'
    $preference = Join-Path $testProfileFolder 'profile-path.yaml'
    [IO.File]::WriteAllText($preference, (ConvertTo-Json $profile), [Text.UTF8Encoding]::new($false))
    Assert-Validation $old (Join-Path $profile '子目录') $uninstallCommand '个人资料目录分开'
    Assert-Validation $old (Join-Path $profile 'installed') $uninstallCommand '个人资料目录分开'
    Assert-Validation $old (Join-Path $env:LOCALAPPDATA 'autodl-watcher/profile') $uninstallCommand '个人资料目录分开'

    # 只有保存的源码项目根的直属 installed/ 能与资料同处一个项目。
    New-Item -ItemType Directory -Path (Join-Path $project 'src/autodl_watcher'), (Join-Path $project 'installer') | Out-Null
    New-Item -ItemType File -Path (Join-Path $project 'src/autodl_watcher/__init__.py') | Out-Null
    [IO.File]::WriteAllText($preference, (ConvertTo-Json $project), [Text.UTF8Encoding]::new($false))
    Assert-Validation $old $installed $uninstallCommand ''
    Assert-Validation $old $project $uninstallCommand '个人资料目录分开'
    Assert-Validation $old (Join-Path $project '.ui/installed') $uninstallCommand '个人资料目录分开'
    Assert-Validation $old (Join-Path $project 'data/installed') $uninstallCommand '个人资料目录分开'

    New-Item -ItemType Junction -Path $installed -Target $new | Out-Null
    Assert-Validation $old $installed $uninstallCommand '目录联接或符号链接'
    [IO.Directory]::Delete($installed, $false)

    New-Item -ItemType Directory -Path $installed | Out-Null
    $projectUninstaller = Join-Path $installed "Uninstall $exeName"
    New-Item -ItemType File -Path $projectUninstaller, (Join-Path $installed $exeName) | Out-Null
    $projectUninstallCommand = '"' + $projectUninstaller + '" /currentuser'
    Assert-Validation $installed (Join-Path $scratch '迁出程序') $projectUninstallCommand ''
    New-Item -ItemType Junction -Path $oldProgramLink -Target $project | Out-Null
    Assert-Validation $installed (Join-Path $scratch '迁出程序') $projectUninstallCommand '原程序目录内有目录联接'
    [IO.Directory]::Delete($oldProgramLink, $false)
    New-Item -ItemType Directory -Path (Join-Path $installed 'runtime') | Out-Null
    Assert-Validation $installed (Join-Path $scratch '迁出程序') $projectUninstallCommand '原程序目录内还有应用资料'

    New-Item -ItemType Junction -Path $link -Target $old | Out-Null
    Assert-Validation $old (Join-Path $link '新位置') $uninstallCommand '目录联接或符号链接'
    if (-not (Test-Path -LiteralPath $uninstaller)) { throw '预检修改了既有安装' }

    # 编译几KB的PE身份样本，仅读取其版本信息，不执行该程序。
    New-Item -ItemType Directory -Path (Join-Path $repair 'resources') | Out-Null
    $fixtureSource = Join-Path $scratch 'RepairFixture.cs'
    $fixtureExe = Join-Path $repair $exeName
    [IO.File]::WriteAllText($fixtureSource, @'
using System.Reflection;
[assembly: AssemblyProduct("Auto算力与训练调度（更新验收）")]
[assembly: AssemblyTitle("Auto算力与训练调度（更新验收）")]
public static class RepairFixture { public static void Main() {} }
'@, [Text.UTF8Encoding]::new($true))
    $compiler = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    & $compiler '/nologo' '/target:winexe' ('/out:' + $fixtureExe) $fixtureSource
    if ($LASTEXITCODE -ne 0) { throw '修复目录PE样本编译失败' }
    Write-RepairArchive
    Assert-Validation $repair $repair '' '' $true
    if (Test-Path -LiteralPath (Join-Path $repair "Uninstall $exeName")) { throw '修复样本不应依赖原卸载器' }
    Assert-Validation $repair (Join-Path $scratch '其他位置') '' '同一个原程序目录' $true
    Assert-Validation $old $old '' '原程序或卸载器不完整' $true

    Write-RepairArchive 'unrelated-app'
    Assert-Validation $repair $repair '' '未识别为本应用' $true
    Write-RepairArchive 'autodl-update-test' $false
    Assert-Validation $repair $repair '' '未识别为本应用' $true
    Write-RepairArchive
    $fixtureBytes = [IO.File]::ReadAllBytes($fixtureExe)
    Copy-Item -LiteralPath (Join-Path $env:WINDIR 'System32/where.exe') -Destination $fixtureExe -Force
    Assert-Validation $repair $repair '' '未识别为本应用' $true
    [IO.File]::WriteAllBytes($fixtureExe, $fixtureBytes)

    New-Item -ItemType Directory -Path (Join-Path $repair '.ui') | Out-Null
    Assert-Validation $repair $repair '' '原程序目录内还有应用资料' $true
    Remove-Item -LiteralPath (Join-Path $repair '.ui')
    New-Item -ItemType Directory -Path $profile -Force | Out-Null
    New-Item -ItemType Junction -Path $repairLink -Target $profile | Out-Null
    Assert-Validation $repair $repair '' '原程序目录内有目录联接' $true
    [IO.Directory]::Delete($repairLink, $false)
    [IO.File]::WriteAllText($preference, (ConvertTo-Json $repair), [Text.UTF8Encoding]::new($false))
    Assert-Validation $repair $repair '' '个人资料目录分开' $true
    '迁移预检通过：新目录、重叠拒绝、卸载登记、非空目录、资料保护、目录联接、迁入或迁出项目专用子目录。'
    '原位修复预检通过：无登记或卸载器、PE与asar身份、非本产品拒绝、资料和目录联接保护。'
} finally {
    $env:LOCALAPPDATA = $localAppData
    foreach ($name in $envNames) { [Environment]::SetEnvironmentVariable($name, $previousEnv[$name]) }
    if ($testDirectory.FullName -ne $scratch -or
        -not $scratch.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) { throw '测试目录边界异常' }
    if (Test-Path -LiteralPath $link) { [IO.Directory]::Delete($link, $false) }
    if (Test-Path -LiteralPath $oldProgramLink) { [IO.Directory]::Delete($oldProgramLink, $false) }
    if (Test-Path -LiteralPath $repairLink) { [IO.Directory]::Delete($repairLink, $false) }
    $installedItem = Get-Item -LiteralPath $installed -Force -ErrorAction SilentlyContinue
    if ($installedItem.Attributes -band [IO.FileAttributes]::ReparsePoint) { [IO.Directory]::Delete($installed, $false) }
    Remove-Item -LiteralPath $scratch -Recurse -Force
}
