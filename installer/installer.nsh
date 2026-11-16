!ifndef BUILD_UNINSTALLER
  ; 自定义 include 在模板载入前编译，下面的函数需先引入条件与路径宏。
  !include LogicLib.nsh
  !include FileFunc.nsh

  !define AUTODL_RELOCATION_SCRIPT "${__FILEDIR__}\validate-relocation.ps1"
  Var AutodlRegisteredInstallDir
  Var AutodlRelocating

  !macro customInit
    ; 检查实际安装记录，默认沿用原目录。手动安装可在目录页确认迁移。
    StrCpy $AutodlRelocating "0"
    ReadRegStr $0 HKLM "${INSTALL_REGISTRY_KEY}" InstallLocation
    ReadRegStr $1 HKLM "${UNINSTALL_REGISTRY_KEY}" UninstallString
    ${If} $0 != ""
    ${OrIf} $1 != ""
      MessageBox MB_OK|MB_ICONSTOP "已找到此应用的全用户安装。本安装包仅支持当前用户，已停止安装。" /SD IDOK
      SetErrorLevel 1
      Quit
    ${EndIf}
    ${If} ${isForAllUsers}
      MessageBox MB_OK|MB_ICONSTOP "此安装包仅支持为当前用户安装。" /SD IDOK
      SetErrorLevel 1
      Quit
    ${EndIf}
    ReadRegStr $AutodlRegisteredInstallDir HKCU "${INSTALL_REGISTRY_KEY}" InstallLocation
    ReadRegStr $1 HKCU "${UNINSTALL_REGISTRY_KEY}" UninstallString
    ${If} $AutodlRegisteredInstallDir == ""
    ${AndIf} $1 != ""
      MessageBox MB_OK|MB_ICONSTOP "已有安装记录缺少程序目录。请修复现有安装后再试。" /SD IDOK
      SetErrorLevel 1
      Quit
    ${EndIf}
    ${If} $AutodlRegisteredInstallDir == ""
    ${AndIf} ${isUpdated}
      MessageBox MB_OK|MB_ICONSTOP "找不到原程序目录，更新已停止。请运行完整安装包。" /SD IDOK
      SetErrorLevel 1
      Quit
    ${EndIf}
    ; 静默安装不会进入目录页，也必须让注册路径优先于 /D。
    ${If} $AutodlRegisteredInstallDir != ""
      StrCpy $INSTDIR $AutodlRegisteredInstallDir
      Call AutodlValidateInstallLocation
    ${ElseIf} ${Silent}
      Call AutodlValidateNewDirectory
    ${EndIf}
  !macroend

  ; Assisted 模式的安装范围页直接使用当前用户，保持原来的安装身份。
  !macro customInstallmode
    StrCpy $isForceCurrentInstall "1"
  !macroend

  !macro customPageAfterChangeDir
    !define MUI_PAGE_CUSTOMFUNCTION_PRE AutodlDirectoryPre
    !define MUI_PAGE_CUSTOMFUNCTION_SHOW AutodlDirectoryShow
    !define MUI_PAGE_CUSTOMFUNCTION_LEAVE AutodlDirectoryLeave
    !insertmacro MUI_PAGE_DIRECTORY

    Function AutodlDirectoryShow
      ${If} $AutodlRegisteredInstallDir != ""
        !insertmacro MUI_HEADER_TEXT "选择安装目录" "默认沿用原目录，也可以迁移程序。"
        SendMessage $mui.DirectoryPage.Text ${WM_SETTEXT} 0 "STR:保留当前路径以原位更新，或浏览并选择新的或空的程序目录。个人资料仍保留在原位。"
      ${EndIf}
    FunctionEnd

    Function AutodlDirectoryPre
      ; 目录页读取当前登记，避免初始化的空结果把已有程序误判为新装。
      ReadRegStr $AutodlRegisteredInstallDir HKCU "${INSTALL_REGISTRY_KEY}" InstallLocation
      ; 手动安装显示原目录供确认，应用内更新直接沿用原目录。
      ${If} $AutodlRegisteredInstallDir != ""
        StrCpy $INSTDIR $AutodlRegisteredInstallDir
      ${EndIf}
      ${If} ${isUpdated}
        Abort
      ${EndIf}
    FunctionEnd

    Function AutodlDirectoryLeave
      StrCpy $AutodlRelocating "0"
      ; 安装前再次以当前登记判断覆盖或迁移，不沿用进入向导时的空缓存。
      ReadRegStr $AutodlRegisteredInstallDir HKCU "${INSTALL_REGISTRY_KEY}" InstallLocation
      ${If} $AutodlRegisteredInstallDir == ""
        Call AutodlValidateNewDirectory
        Return
      ${EndIf}

      ; 把当前输入与注册路径统一成带末尾反斜杠的绝对路径后再判断是否迁移。
      Call AutodlValidateInstallLocation
      System::Call 'kernel32::GetFullPathNameW(w "$AutodlRegisteredInstallDir", i ${NSIS_MAX_STRLEN}, w .r8, p 0) i .r7'
      ${If} $7 == 0
      ${OrIf} $7 >= ${NSIS_MAX_STRLEN}
        MessageBox MB_OK|MB_ICONSTOP "无法识别原安装目录，已停止。请修复现有安装后再试。" /SD IDOK
        Abort
      ${EndIf}
      StrCpy $4 $8 1 -1
      ${If} $4 != "\"
        StrCpy $8 "$8\"
      ${EndIf}
      ${If} $2 == $8
        StrCpy $INSTDIR $AutodlRegisteredInstallDir
        Return
      ${EndIf}

      Call AutodlValidateNewDirectory
      Call AutodlValidateRelocation
      MessageBox MB_OKCANCEL|MB_ICONQUESTION|MB_DEFBUTTON2 "将程序迁移到：$\r$\n$INSTDIR$\r$\n$\r$\n旧卸载器会清理原程序目录，个人资料仍保留在原位。继续安装？" /SD IDCANCEL IDOK +2
      Abort
      StrCpy $AutodlRelocating "1"
    FunctionEnd
  !macroend

  !macro customFinishPage
    !ifndef HIDE_RUN_AFTER_FINISH
      !define MUI_FINISHPAGE_RUN
      !define MUI_FINISHPAGE_RUN_TEXT "打开Auto算力与训练调度并进行基础设置"
      !define MUI_FINISHPAGE_RUN_FUNCTION "AutodlLaunchFromFinishPage"
      !define MUI_PAGE_CUSTOMFUNCTION_SHOW AutodlFinishPageShow
    !endif
    !insertmacro MUI_PAGE_FINISH

    !ifndef HIDE_RUN_AFTER_FINISH
      Function AutodlFinishPageShow
        ${If} ${isUpdated}
        ${AndIf} $mui.FinishPage.Run != 0
          SendMessage $mui.FinishPage.Run ${WM_SETTEXT} 0 "STR:打开Auto算力与训练调度"
        ${EndIf}
      FunctionEnd

      Function AutodlLaunchFromFinishPage
        ${If} ${isUpdated}
          StrCpy $1 "--updated"
        ${Else}
          StrCpy $1 "--autodl-open-setup"
        ${EndIf}
        ${StdUtils.ExecShellAsUser} $0 "$INSTDIR\${APP_EXECUTABLE_FILENAME}" "open" "$1"
      FunctionEnd
    !endif
  !macroend

  !macro AutodlCheckProfilePath PROFILE
    System::Call 'kernel32::GetFullPathNameW(w "${PROFILE}", i ${NSIS_MAX_STRLEN}, w .r3, p 0) i .r7'
    ${If} $7 == 0
    ${OrIf} $7 >= ${NSIS_MAX_STRLEN}
      Goto autodlInvalidLocation
    ${EndIf}
    StrCpy $3 "$3\"
    StrLen $4 $3
    StrCpy $5 $2 $4
    StrLen $4 $2
    StrCpy $6 $3 $4
    ${If} $5 == $3
    ${OrIf} $6 == $2
      Goto autodlProtectedLocation
    ${EndIf}
  !macroend

  Function AutodlValidateInstallLocation
    ; 安装与卸载处理整棵程序目录，不能选盘根或覆盖默认个人资料目录。
    ; 使用 Win32 规范化路径，允许用户输入尚未创建的文件夹。
    System::Call 'kernel32::GetFullPathNameW(w "$INSTDIR", i ${NSIS_MAX_STRLEN}, w .r2, p 0) i .r7'
    ${If} $7 == 0
    ${OrIf} $7 >= ${NSIS_MAX_STRLEN}
      Goto autodlInvalidLocation
    ${EndIf}
    ${GetRoot} "$2" $3
    ${If} "$2" == "$3"
    ${OrIf} "$2" == "$3\"
      Goto autodlProtectedLocation
    ${EndIf}
    StrCpy $4 $2 1 -1
    ${If} $4 != "\"
      StrCpy $2 "$2\"
    ${EndIf}
    !insertmacro AutodlCheckProfilePath "$LocalAppData\autodl-watcher"
    !insertmacro AutodlCheckProfilePath "$LocalAppData\autodl-watcher-update-test"
    Return

    autodlProtectedLocation:
      MessageBox MB_OK|MB_ICONSTOP "安装目录不能是盘根，也不能与应用的默认资料目录重叠。" /SD IDOK
      Goto autodlStopLocation
    autodlInvalidLocation:
      MessageBox MB_OK|MB_ICONSTOP "无法识别安装目录，请选择有效的程序文件夹。" /SD IDOK
    autodlStopLocation:
      ${If} ${Silent}
        SetErrorLevel 1
        Quit
      ${EndIf}
      Abort
  FunctionEnd

  Function AutodlValidateNewDirectory
    Call AutodlValidateInstallLocation
    ; 新目录或空目录用于首次安装与迁移，已登记目录支持原位覆盖。
    ClearErrors
    FindFirst $0 $1 "$INSTDIR\*"
    ${If} ${Errors}
      ${If} ${FileExists} "$INSTDIR"
        Goto autodlInvalidDirectory
      ${EndIf}
    ${Else}
      ${DoWhile} $1 != ""
        ${If} $1 != "."
        ${AndIf} $1 != ".."
          FindClose $0
          Goto autodlInvalidDirectory
        ${EndIf}
        FindNext $0 $1
        ${If} ${Errors}
          ${Break}
        ${EndIf}
      ${Loop}
      FindClose $0
    ${EndIf}
    ClearErrors
    Return

    autodlInvalidDirectory:
      MessageBox MB_OK|MB_ICONSTOP "请选择一个新的或空的程序文件夹，避免覆盖已有文件。" /SD IDOK
      ${If} ${Silent}
        SetErrorLevel 1
        Quit
      ${EndIf}
      Abort
  FunctionEnd

  !macro customHeader
    Function AutodlValidateRelocation
      InitPluginsDir
      File /oname=$PLUGINSDIR\validate-relocation.ps1 "${AUTODL_RELOCATION_SCRIPT}"
      Delete "$PLUGINSDIR\relocation-error.txt"
      ReadRegStr $0 HKCU "${UNINSTALL_REGISTRY_KEY}" UninstallString

      ; 通过当前安装器进程的环境传递原始路径与注册命令，避免命令参数重新解释引号。
      StrCpy $2 "error"
      StrCpy $3 $AutodlRegisteredInstallDir
      StrCpy $4 $INSTDIR
      System::Call 'kernel32::SetEnvironmentVariableW(w "AUTODL_RELOCATION_OLD_DIRECTORY", w r3) i .r7'
      ${If} $7 == 0
        Goto autodlRelocationClearEnvironment
      ${EndIf}
      System::Call 'kernel32::SetEnvironmentVariableW(w "AUTODL_RELOCATION_NEW_DIRECTORY", w r4) i .r7'
      ${If} $7 == 0
        Goto autodlRelocationClearEnvironment
      ${EndIf}
      System::Call 'kernel32::SetEnvironmentVariableW(w "AUTODL_RELOCATION_EXECUTABLE_NAME", w "${APP_EXECUTABLE_FILENAME}") i .r7'
      ${If} $7 == 0
        Goto autodlRelocationClearEnvironment
      ${EndIf}
      System::Call 'kernel32::SetEnvironmentVariableW(w "AUTODL_RELOCATION_UNINSTALL_COMMAND", w r0) i .r7'
      ${If} $7 == 0
        Goto autodlRelocationClearEnvironment
      ${EndIf}
      nsExec::Exec /TIMEOUT=15000 '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\validate-relocation.ps1" -FromEnvironment'
      Pop $2

      autodlRelocationClearEnvironment:
      System::Call 'kernel32::SetEnvironmentVariableW(w "AUTODL_RELOCATION_OLD_DIRECTORY", p 0)'
      System::Call 'kernel32::SetEnvironmentVariableW(w "AUTODL_RELOCATION_NEW_DIRECTORY", p 0)'
      System::Call 'kernel32::SetEnvironmentVariableW(w "AUTODL_RELOCATION_EXECUTABLE_NAME", p 0)'
      System::Call 'kernel32::SetEnvironmentVariableW(w "AUTODL_RELOCATION_UNINSTALL_COMMAND", p 0)'
      ${If} $2 == "0"
        Return
      ${EndIf}

      StrCpy $1 ""
      ClearErrors
      FileOpen $0 "$PLUGINSDIR\relocation-error.txt" r
      ${IfNot} ${Errors}
        FileReadUTF16LE $0 $1
        FileClose $0
      ${EndIf}
      ${If} $1 == ""
        StrCpy $1 "无法检查安装目录，请确认目录可访问后重试。"
      ${EndIf}
      MessageBox MB_OK|MB_ICONSTOP "$1" /SD IDOK
      Abort
    FunctionEnd
  !macroend

  !macro customUnInstallCheck
    ${If} $AutodlRelocating == "1"
      ; 迁移时必须确认旧程序已卸载，防止模板在卸载器启动失败后继续解包。
      IfErrors autodlRelocationUninstallFailed
      ${If} $R0 != 0
      ${OrIf} ${FileExists} "$AutodlRegisteredInstallDir\${APP_EXECUTABLE_FILENAME}"
        Goto autodlRelocationUninstallFailed
      ${EndIf}
      Return
      autodlRelocationUninstallFailed:
      MessageBox MB_OK|MB_ICONSTOP "原程序未能完成卸载，迁移已停止。新目录没有安装程序。" /SD IDOK
      SetErrorLevel 2
      Quit
    ${EndIf}

    ; 原位覆盖保留 electron-builder 原来的卸载结果处理。
    IfErrors 0 +3
    DetailPrint `Uninstall was not successful. Not able to launch uninstaller!`
    Return
    ${If} $R0 != 0
      MessageBox MB_OK|MB_ICONEXCLAMATION "$(uninstallFailed): $R0"
      DetailPrint `Uninstall was not successful. Uninstaller error code: $R0.`
      SetErrorLevel 2
      Quit
    ${EndIf}
  !macroend

  !macro customInstall
    ${IfNot} ${Silent}
    ${AndIfNot} ${isUpdated}
      ; 首次启动可以用安装包的来源定位同仓库旧资料，安装阶段不访问资料。
      ClearErrors
      FileOpen $0 "$INSTDIR\resources\installer-origin.txt" w
      ${IfNot} ${Errors}
        FileWriteUTF16LE $0 "$EXEDIR"
        FileClose $0
      ${EndIf}
      ClearErrors
    ${EndIf}
    ${If} ${isUpdated}
    ${AndIf} ${Silent}
    ${AndIfNot} ${isForceRun}
      ; 交互安装由完成页重开，带 --force-run 的静默安装由模板重开。
      SetOutPath "$INSTDIR"
      Exec '"$INSTDIR\${APP_EXECUTABLE_FILENAME}" --updated'
    ${EndIf}
  !macroend
!endif
