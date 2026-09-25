; Inno Setup script for PrintPal
; Builds a proper Windows installer from the PyInstaller one-folder output.

#define MyAppName "PrintPal"
#define MyAppVersion "0.8.0"
#define MyAppPublisher "Parsa J."
#define MyAppURL "https://github.com/parsaj-dev/PrintPal"
#define MyAppExeName "PrintPal.exe"

[Setup]
AppId={{E8A3B2C1-4D5F-6A7B-8C9D-0E1F2A3B4C5D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=installer_output
OutputBaseFilename=PrintPal-Setup
Compression=lzma2
SolidCompression=yes
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "taskbarpin"; Description: "Pin to taskbar (you can also do this manually)"; GroupDescription: "Shortcuts:"
Name: "startup"; Description: "Start PrintPal in the tray when I sign in (labels printed to PrintPal appear instantly)"; GroupDescription: "Speed:"
Name: "virtualprinter"; Description: "Install the ""PrintPal"" virtual printer (print to PrintPal from any app)"; GroupDescription: "Virtual printer:"; Flags: unchecked

[Files]
Source: "dist\PrintPal\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
; Taskbar pinning needs a user-startmenu shortcut that Windows can discover
Name: "{userstartmenu}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"

[Registry]
; Keep PrintPal warm in the tray from sign-in; toggled later in Settings.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "PrintPal"; ValueData: """{app}\{#MyAppExeName}"" --background"; Flags: uninsdeletevalue; Tasks: startup

[Run]
; Optional: install the virtual printer. The .ps1 self-elevates (needs admin).
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\install_printer.ps1"" -PrintPalExe ""{app}\{#MyAppExeName}"""; Description: "Install the PrintPal virtual printer"; Flags: postinstall skipifsilent runascurrentuser; Tasks: virtualprinter
Filename: "{app}\{#MyAppExeName}"; Description: "Launch PrintPal"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Remove the virtual printer on uninstall (ignore errors if it was never installed).
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\uninstall_printer.ps1"""; Flags: runhidden; RunOnceId: "RemovePrintPalPrinter"

[Code]
{ PrintPal can keep running in the tray and the virtual printer's watcher runs
  all the time; both hold their .exe open, which would make an upgrade or an
  uninstall fail with "file in use". Close them first. }
procedure KillProcess(Name: String);
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM ' + Name, '', SW_HIDE,
       ewWaitUntilTerminated, ResultCode);
end;

procedure StopPrintPal();
begin
  KillProcess('PrintPal.exe');
  KillProcess('PrintPalWatcher.exe');
  KillProcess('PrintPalPort.exe');
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopPrintPal();
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  StopPrintPal();
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  { The app's own "start when I sign in" setting writes the same value. }
  if CurUninstallStep = usPostUninstall then
    RegDeleteValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run', 'PrintPal');
end;
