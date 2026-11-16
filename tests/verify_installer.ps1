[CmdletBinding()]
param([Parameter(Mandatory)][string]$Package, [Parameter(Mandatory)][string]$Stage, [switch]$ResumeMigration, [switch]$RefreshRegistrationCase, [switch]$RepairRegistrationCase, [switch]$ExplorerLaunch)
$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$allowed = Join-Path $repo 'installer\dist\verification'
$packagePath = (Resolve-Path -LiteralPath $Package).Path
$stagePath = [IO.Path]::GetFullPath($Stage)
if (-not $stagePath.StartsWith($allowed + '\', [StringComparison]::OrdinalIgnoreCase)) { throw '验收路径必须位于installer/dist/verification下' }
if ((Test-Path -LiteralPath $stagePath) -and -not $ResumeMigration) { throw '请使用新的隔离验收目录' }
if ([IO.Path]::GetFileName($packagePath) -notlike 'autodl-update-test-*.exe') { throw '只允许独立测试身份的安装包' }

Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public static class AutoDLWizard {
  delegate bool Callback(IntPtr hwnd, IntPtr parameter);
  [DllImport("user32.dll")] static extern bool EnumWindows(Callback proc, IntPtr parameter);
  [DllImport("user32.dll")] static extern bool EnumChildWindows(IntPtr parent, Callback proc, IntPtr parameter);
  [DllImport("user32.dll")] static extern int GetWindowThreadProcessId(IntPtr hwnd, out int pid);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern IntPtr SendMessage(IntPtr hwnd, int msg, IntPtr w, StringBuilder l);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern IntPtr SendMessage(IntPtr hwnd, int msg, IntPtr w, string l);
  [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr hwnd, int msg, IntPtr w, IntPtr l);
  [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr hwnd, int msg, IntPtr w, IntPtr l);
  [DllImport("user32.dll")] public static extern int GetDlgCtrlID(IntPtr hwnd);
  [DllImport("user32.dll")] public static extern IntPtr GetDlgItem(IntPtr hwnd, int id);
  [DllImport("user32.dll")] public static extern bool IsWindowEnabled(IntPtr hwnd);
  [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr hwnd);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr hwnd, StringBuilder text, int length);
  public static string Class(IntPtr hwnd) { var b=new StringBuilder(256); GetClassName(hwnd,b,b.Capacity); return b.ToString(); }
  public static string Text(IntPtr hwnd) { var b=new StringBuilder(4096); SendMessage(hwnd,13,(IntPtr)b.Capacity,b); return b.ToString(); }
  public static void SetText(IntPtr hwnd,string value) { SendMessage(hwnd,12,IntPtr.Zero,value); }
  public static IntPtr[] Windows(int pid) {
    var list=new List<IntPtr>();
    EnumWindows((h,p)=>{int owner; GetWindowThreadProcessId(h,out owner); if(owner==pid && Class(h)=="#32770") list.Add(h); return true;},IntPtr.Zero);
    return list.ToArray();
  }
  public static IntPtr[] Children(IntPtr hwnd) {
    var list=new List<IntPtr>(); EnumChildWindows(hwnd,(h,p)=>{list.Add(h);return true;},IntPtr.Zero); return list.ToArray();
  }
}
'@

$exeName = 'autodl-update-test.exe'
$product = 'Auto算力与训练调度（更新验收）'
function Installed-Entries {
    @(Get-ChildItem -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall' |
        ForEach-Object { Get-ItemProperty -LiteralPath $_.PSPath } |
        Where-Object { $_.DisplayName -eq $product -or $_.DisplayName -like "$product *" })
}
if ((Get-Process -Name autodl-update-test -ErrorAction SilentlyContinue)) { throw '已有独立测试进程，请先核对归属' }
if (-not $ResumeMigration -and (Installed-Entries).Count) { throw '已有独立测试安装，请先核对归属' }
New-Item -ItemType Directory -Path $stagePath -Force | Out-Null
$from = Join-Path $stagePath '程序 A 中文路径'
$to = Join-Path $stagePath '程序 B 空格路径'
$profile = Join-Path $stagePath '原历史资料'
$local = Join-Path $stagePath 'test-local-app-data'
New-Item -ItemType Directory -Path (Join-Path $profile '.ui'),(Join-Path $local 'autodl-watcher-update-test') -Force | Out-Null
$preferences = Join-Path $profile '.ui/preferences.yaml'
$history = Join-Path $profile '.ui/retained-history.txt'
$pointer = Join-Path $local 'autodl-watcher-update-test/profile-path.yaml'
if ($ResumeMigration) {
    $existing = Installed-Entries
    if ($existing.Count -ne 1 -or $existing[0].DisplayIcon -ne ((Join-Path $from $exeName) + ',0')) { throw '续验的测试安装路径不符' }
} else {
    [IO.File]::WriteAllText($preferences, "ui:`n  theme: dark`nuser: native-wizard-fixture`n", [Text.UTF8Encoding]::new($false))
    [IO.File]::WriteAllText($history, 'installer-retention-fixture', [Text.UTF8Encoding]::new($false))
    [IO.File]::WriteAllText($pointer, (ConvertTo-Json $profile), [Text.UTF8Encoding]::new($false))
}
$before = @(Get-FileHash -LiteralPath $preferences,$history,$pointer | Select-Object -ExpandProperty Hash)

function Wait-Control($task, [scriptblock]$Find, [string]$Label) {
    for ($attempt = 0; $attempt -lt 2400; $attempt++) {
        if ($task.HasExited) { throw "安装器过早退出，等待$Label，退出码$($task.ExitCode)" }
        foreach ($window in [AutoDLWizard]::Windows($task.Id)) {
            $children = [AutoDLWizard]::Children($window)
            $found = @($children | Where-Object { & $Find $_ })
            if ($found.Count) { return [pscustomobject]@{ Root=$window; Control=$found[0]; Children=$children } }
            # 出现未预期的确认或错误弹框时直接报告，避免把目录拒绝误记成等待超时。
            $confirm = @($children | Where-Object { [AutoDLWizard]::Class($_) -eq 'Button' -and
                [AutoDLWizard]::Text($_).Replace('&','') -match '^(确定|OK)$' })
            if ($confirm.Count) {
                $detail = @($children | Where-Object { [AutoDLWizard]::Class($_) -eq 'Static' } |
                    ForEach-Object { [AutoDLWizard]::Text($_) } | Where-Object { $_ }) -join '|'
                throw "安装器弹出未预期对话框，等待$Label，实际内容$detail"
            }
        }
        Start-Sleep -Milliseconds 150
    }
    $texts = foreach ($window in [AutoDLWizard]::Windows($task.Id)) {
        foreach ($control in [AutoDLWizard]::Children($window)) { [AutoDLWizard]::Text($control) }
    }
    throw "未找到$Label，实际控件$($texts -join '|')"
}
function Next-Page($page) {
    $button = [AutoDLWizard]::GetDlgItem($page.Root,1)
    if (-not $button -or -not [AutoDLWizard]::IsWindowEnabled($button)) { throw '安装继续按钮不可用' }
    [void][AutoDLWizard]::PostMessage($page.Root,0x111,[IntPtr]1,$button)
}
function Save-TestRegistration {
    # 只保存独立验收身份的两个确切键和值类型，正式产品登记始终不在操作范围内。
    $guid = '0abb133a-f2ee-5c74-b299-9b011ef42d4f'
    $rows = Installed-Entries
    if ($rows.Count -ne 1 -or $rows[0].PSChildName -ne $guid -or
        $rows[0].DisplayIcon -ne ((Join-Path $from $exeName) + ',0')) { throw '登记刷新回归只允许本轮A目录的独立测试身份' }
    foreach ($name in @("Software\$guid", "Software\Microsoft\Windows\CurrentVersion\Uninstall\$guid")) {
        $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($name)
        if (-not $key) { throw "测试登记缺失 $name" }
        try {
            if ($key.SubKeyCount) { throw '测试登记含未预期子键，停止此回归' }
            if ($name -eq "Software\$guid" -and $key.GetValue('InstallLocation') -ne $from) { throw '测试原程序位置不符' }
            $values = @($key.GetValueNames() | ForEach-Object {
                [pscustomobject]@{ Name=$_; Kind=$key.GetValueKind($_)
                    Value=$key.GetValue($_,$null,[Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames) }
            })
            [pscustomobject]@{ Name=$name; Values=$values }
        } finally {
            $key.Dispose()
        }
    }
}
function Restore-TestRegistration($Backup) {
    foreach ($entry in $Backup) {
        $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($entry.Name)
        try {
            foreach ($value in $entry.Values) { $key.SetValue($value.Name,$value.Value,$value.Kind) }
        } finally {
            $key.Dispose()
        }
    }
}
function Install-Interactively([string]$Chosen, [bool]$Existing, [bool]$Relocate, [bool]$RefreshRegistration = $false, [bool]$MissingRegistration = $false) {
    $task = $null
    $registrationHidden = $false
    $registration = @()
    try {
        if ($RefreshRegistration -or $MissingRegistration) {
            $registration = @(Save-TestRegistration)
            # 刷新场景在目录页恢复，修复场景由安装器重新创建完整登记。
            $registrationHidden = $true
            foreach ($entry in $registration) { [Microsoft.Win32.Registry]::CurrentUser.DeleteSubKeyTree($entry.Name) }
        }
        if ($ExplorerLaunch) {
            # 通过真实Explorer启动，覆盖用户双击入口与PowerShell启动的差异。
            $installerName = [IO.Path]::GetFileNameWithoutExtension($packagePath)
            $previousIds = @(Get-Process -Name $installerName -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
            $shell = New-Object -ComObject Shell.Application
            $browser = $shell.Windows() | Where-Object { $_.FullName -like '*\explorer.exe' } | Select-Object -First 1
            if (-not $browser) { throw 'Explorer入口验收需要一个已打开的资源管理器窗口' }
            $browser.Document.Application.ShellExecute($packagePath,'',(Split-Path $packagePath),'open',1)
            $deadline = [DateTime]::UtcNow.AddSeconds(30)
            while (-not $task -and [DateTime]::UtcNow -lt $deadline) {
                $task = Get-Process -Name $installerName -ErrorAction SilentlyContinue |
                    Where-Object { $_.Id -notin $previousIds -and $_.Path -eq $packagePath } | Select-Object -First 1
                if (-not $task) { Start-Sleep -Milliseconds 100 }
            }
            if (-not $task) { throw 'Explorer未启动本轮测试安装器' }
            # Get-Process取得的实例需提前持有句柄，才能在进程退出后读取真实退出码。
            [void]$task.Handle
            $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($task.Id)"
            if ((Get-Process -Id $parent.ParentProcessId).ProcessName -ne 'explorer') { throw '未通过实际Explorer进程启动' }
        } else {
            $task = Start-Process -FilePath $packagePath -WindowStyle Hidden -PassThru
        }
        $page = Wait-Control $task { param($h) [AutoDLWizard]::Class($h) -eq 'Edit' -and [AutoDLWizard]::GetDlgCtrlID($h) -eq 1019 } '程序目录页'
        if ($registrationHidden -and -not $MissingRegistration) {
            Restore-TestRegistration $registration
            $registrationHidden = $false
        }
        $browse = @($page.Children | Where-Object { [AutoDLWizard]::GetDlgCtrlID($_) -eq 1001 })
        if ($browse.Count -ne 1 -or -not [AutoDLWizard]::IsWindowEnabled($browse[0])) { throw '浏览按钮不可用' }
        if ($Existing -and -not $RefreshRegistration -and -not $MissingRegistration -and [AutoDLWizard]::Text($page.Control) -ne $from) { throw '覆盖安装未预填已登记目录' }
        # 打开真实目录浏览窗口，再取消并在目录输入框选择尚不存在的新路径。
        [void][AutoDLWizard]::PostMessage($page.Root,0x111,[IntPtr]1001,$browse[0])
        $picker = Wait-Control $task { param($h) [AutoDLWizard]::Class($h) -eq 'SysTreeView32' } '浏览目录窗口'
        [void][AutoDLWizard]::PostMessage($picker.Root,0x111,[IntPtr]2,([AutoDLWizard]::GetDlgItem($picker.Root,2)))
        $pickerDeadline = [DateTime]::UtcNow.AddSeconds(10)
        while ([AutoDLWizard]::IsWindow($picker.Root) -and [DateTime]::UtcNow -lt $pickerDeadline) { Start-Sleep -Milliseconds 50 }
        if ([AutoDLWizard]::IsWindow($picker.Root)) { throw '目录浏览窗口未完成关闭' }
        [AutoDLWizard]::SetText($page.Control,$Chosen)
        if ([AutoDLWizard]::Text($page.Control) -ne $Chosen) { throw '目录输入框未接受指定路径' }
        Next-Page $page
        if ($Relocate) {
            $confirmation = Wait-Control $task { param($h) [AutoDLWizard]::GetDlgCtrlID($h) -eq 1 -and [AutoDLWizard]::Text($h) -match '^(确定|OK)$' } '迁移确认'
            if (-not ($confirmation.Children | Where-Object { [AutoDLWizard]::Text($_).Contains($Chosen) })) {
                $details = ($confirmation.Children | ForEach-Object { [AutoDLWizard]::Text($_) }) -join '|'
                throw ('迁移确认缺少目标路径，实际对话框' + $details)
            }
            [void][AutoDLWizard]::PostMessage($confirmation.Root,0x111,[IntPtr]1,$confirmation.Control)
        }
        $finish = Wait-Control $task { param($h) [AutoDLWizard]::Class($h) -eq 'Button' -and [AutoDLWizard]::Text($h).Replace('&','') -eq '打开Auto算力与训练调度并进行基础设置' } '安装完成基础设置选项'
        if ([AutoDLWizard]::SendMessage($finish.Control,0xF0,[IntPtr]::Zero,[IntPtr]::Zero).ToInt32() -ne 1) { throw '完成页未默认勾选基础设置' }
        [void][AutoDLWizard]::SendMessage($finish.Control,0xF1,[IntPtr]::Zero,[IntPtr]::Zero)
        Next-Page $finish
        if (-not $task.WaitForExit(30000) -or $task.ExitCode -ne 0) { throw "安装器未正常完成，退出码$($task.ExitCode)" }
        $rows = Installed-Entries
        if ($rows.Count -ne 1 -or $rows[0].DisplayIcon -ne ((Join-Path $Chosen $exeName) + ',0')) { throw '程序路径或安装登记不唯一' }
        $registrationHidden = $false
        Write-Output "交互安装通过，程序路径$Chosen"
    } finally {
        try {
            if ($task -and -not $task.HasExited) { Stop-Process -Id $task.Id }
        } finally {
            if ($registrationHidden) { Restore-TestRegistration $registration }
        }
    }
}

$previousLocal = $env:LOCALAPPDATA
try {
    $env:LOCALAPPDATA = $local
    if (-not $ResumeMigration) {
        Install-Interactively $from $false $false
        Install-Interactively $from $true $false
    }
    if ($RefreshRegistrationCase) {
        Install-Interactively ($from + '\') $true $false $true
        Write-Output '初始化原目录状态为空后，目录页重新识别登记并原位覆盖通过。'
    }
    if ($RepairRegistrationCase) {
        Install-Interactively ($from + '\') $true $false $false $true
        Write-Output '安装登记全程缺失时，识别已有本应用目录并完成修复安装、重新创建登记通过。'
    }
    Install-Interactively $to $true $true
    if (Test-Path -LiteralPath (Join-Path $from $exeName)) { throw '迁移后原程序仍在' }
    $after = @(Get-FileHash -LiteralPath $preferences,$history,$pointer | Select-Object -ExpandProperty Hash)
    if (($before -join ',') -ne ($after -join ',')) { throw '迁移改写了原资料或资料指针' }
    $shortcut = Join-Path ([Environment]::GetFolderPath([Environment+SpecialFolder]::Programs)) "$product.lnk"
    $shell = New-Object -ComObject WScript.Shell
    if ($shell.CreateShortcut($shortcut).TargetPath -ne (Join-Path $to $exeName)) { throw '开始菜单入口仍指向原位置' }
    & (Join-Path $repo 'installer/uninstall.ps1') -TestBuild -Confirm:$false
    if ((Installed-Entries).Count -or (Test-Path -LiteralPath (Join-Path $to $exeName))) { throw '卸载仍有程序或安装登记' }
    if (-not (Test-Path -LiteralPath $preferences) -or -not (Test-Path -LiteralPath $history)) { throw '卸载删除了个人资料' }
    Write-Output '新装/原位覆盖/浏览/改路径迁移/唯一登记与快捷方式/卸载保留验收通过。'
} finally {
    $env:LOCALAPPDATA = $previousLocal
}
