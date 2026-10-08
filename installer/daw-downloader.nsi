Unicode true
ManifestDPIAware true
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 32
AllowSkipFiles off

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!include "Sections.nsh"
!include "x64.nsh"
!include "${PAYLOAD_INCLUDE}"

!define APP_NAME "daw下载器"
!define APP_KEY "Software\dawDownloader"
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\dawDownloader"
!define BRIDGE_KEY "Software\Google\Chrome\NativeMessagingHosts\com.laowang.downloader"

Name "${APP_NAME} ${APP_VERSION}"
OutFile "${OUTPUT_FILE}"
InstallDir "$LOCALAPPDATA\Programs\dawDownloader"
InstallDirRegKey HKCU "${APP_KEY}" "InstallDir"
BrandingText "${APP_NAME}"
VIProductVersion "${APP_VERSION}.0"
VIAddVersionKey /LANG=2052 "ProductName" "${APP_NAME}"
VIAddVersionKey /LANG=2052 "ProductVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=2052 "FileVersion" "${APP_VERSION}.0"
VIAddVersionKey /LANG=2052 "FileDescription" "${APP_NAME}安装程序"
VIAddVersionKey /LANG=2052 "LegalCopyright" "Licensed under Apache-2.0"

!define MUI_ICON "${PROJECT_ROOT}\assets\icon.ico"
!define MUI_UNICON "${PROJECT_ROOT}\assets\icon.ico"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "欢迎安装 ${APP_NAME}"
!define MUI_WELCOMEPAGE_TEXT "安装向导将帮助您安装 ${APP_NAME}。$\r$\n$\r$\n您可以选择安装位置，以及是否创建桌面快捷方式和连接浏览器。$\r$\n$\r$\n升级前请正常退出下载器，包括托盘中的程序。"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "${PROJECT_ROOT}\LICENSE"
!insertmacro MUI_PAGE_COMPONENTS
!define MUI_DIRECTORYPAGE_TEXT_TOP "请选择当前用户有写入权限的安装文件夹。升级时选择原目录，配置、记录和断点会保留。"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\daw下载器.exe"
!define MUI_FINISHPAGE_RUN_TEXT "启动 ${APP_NAME}"
!define MUI_FINISHPAGE_RUN_NOTCHECKED
!define MUI_FINISHPAGE_SHOWREADME "$INSTDIR\使用说明.txt"
!define MUI_FINISHPAGE_SHOWREADME_TEXT "查看使用说明（含浏览器扩展安装步骤）"
!define MUI_FINISHPAGE_SHOWREADME_NOTCHECKED
!insertmacro MUI_PAGE_FINISH

!define MUI_UNCONFIRMPAGE_TEXT_TOP "将卸载 ${APP_NAME} 的程序文件和快捷方式。$\r$\n$\r$\n下载文件、配置、下载记录和未完成的断点文件会保留。卸载前请正常退出下载器。"
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_UNPAGE_FINISH
!insertmacro MUI_LANGUAGE "SimpChinese"

!macro CheckFileUnlocked RELATIVE_PATH
  ${If} ${FileExists} "$INSTDIR\${RELATIVE_PATH}"
    System::Call 'kernel32::CreateFileW(w "$INSTDIR\${RELATIVE_PATH}", i 0x40000000, i 7, p 0, i 3, i 0x80, p 0) p .r0'
    ${If} $0 == -1
      MessageBox MB_OK|MB_ICONEXCLAMATION "无法更新或移除 $INSTDIR\${RELATIVE_PATH}。请正常退出下载器（包括托盘），并确认此目录可写后重试。" /SD IDOK
      SetErrorLevel 2
      Abort
    ${EndIf}
    System::Call 'kernel32::CloseHandle(p r0)'
  ${EndIf}
!macroend

Section "下载器与完整视频组件（必选）" SecProgram
  SectionIn RO
  SetShellVarContext current
  !insertmacro CheckPayloadUnlocked
  SetOutPath "$INSTDIR"
  !insertmacro InstallPayload
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "$INSTDIR\daw下载器.exe"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\卸载 ${APP_NAME}.lnk" "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "${APP_KEY}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "${APP_NAME}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\daw下载器.exe,0"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
  WriteRegStr HKCU "${UNINSTALL_KEY}" "QuietUninstallString" '$\"$INSTDIR\Uninstall.exe$\" /S'
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "EstimatedSize" ${INSTALL_SIZE_KB}
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1
SectionEnd

Section "创建桌面快捷方式" SecDesktop
  CreateShortcut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\daw下载器.exe"
SectionEnd

Section "连接 Chrome / Edge 浏览器" SecBrowser
  DetailPrint "注册当前用户的浏览器连接..."
  nsExec::ExecToStack /TIMEOUT=60000 '$\"$INSTDIR\BrowserBridge.exe$\" --install'
  Pop $0
  Pop $1
  ${If} $0 != 0
    MessageBox MB_OK|MB_ICONEXCLAMATION "浏览器连接注册失败。请从安装目录运行 install_browser_bridge.cmd 重试。$\r$\n$1" /SD IDOK
    SetErrorLevel 3
    Abort
  ${EndIf}
SectionEnd

!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SecProgram} "安装下载器、浏览器扩展和离线视频组件，无需另装 Python。"
  !insertmacro MUI_DESCRIPTION_TEXT ${SecDesktop} "在当前用户的桌面上创建启动快捷方式。"
  !insertmacro MUI_DESCRIPTION_TEXT ${SecBrowser} "让浏览器扩展连接此安装目录。浏览器中仍需手动加载 chrome-extension 文件夹，步骤见安装后的使用说明。"
!insertmacro MUI_FUNCTION_DESCRIPTION_END

Function .onInit
  SetShellVarContext current
  ${IfNot} ${RunningX64}
    MessageBox MB_OK|MB_ICONSTOP "此安装包需要 64 位 Windows。" /SD IDOK
    SetErrorLevel 1
    Abort
  ${EndIf}
  ${GetParameters} $0
  ClearErrors
  ${GetOptions} $0 "/NODESKTOP" $1
  ${IfNot} ${Errors}
    SectionSetFlags ${SecDesktop} 0
  ${EndIf}
  ClearErrors
  ${GetOptions} $0 "/NOBRIDGE" $1
  ${IfNot} ${Errors}
    SectionSetFlags ${SecBrowser} 0
  ${EndIf}
FunctionEnd

Section "Uninstall"
  SetShellVarContext current
  !insertmacro CheckPayloadUnlocked
  ; An older uninstaller must not unregister a different installation or portable copy.
  ReadRegStr $0 HKCU "${BRIDGE_KEY}" ""
  ${If} $0 == "$INSTDIR\browser-native-host.json"
    DeleteRegKey HKCU "${BRIDGE_KEY}"
  ${EndIf}
  ReadRegStr $0 HKCU "${APP_KEY}" "InstallDir"
  ${If} $0 == $INSTDIR
    Delete "$DESKTOP\${APP_NAME}.lnk"
    Delete "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk"
    Delete "$SMPROGRAMS\${APP_NAME}\卸载 ${APP_NAME}.lnk"
    RMDir "$SMPROGRAMS\${APP_NAME}"
    DeleteRegKey HKCU "${UNINSTALL_KEY}"
    DeleteRegValue HKCU "${APP_KEY}" "InstallDir"
    DeleteRegKey /ifempty HKCU "${APP_KEY}"
  ${EndIf}
  !insertmacro RemovePayload
  Delete "$INSTDIR\browser-native-host.json"
  Delete "$INSTDIR\Uninstall.exe"
  ; No recursive removal: runtime data and any user-added files must survive.
  RMDir "$INSTDIR"
SectionEnd
