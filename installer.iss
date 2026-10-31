; Inno Setup script for PrintPal
; Builds a proper Windows installer from the PyInstaller one-folder output.

#define MyAppName "PrintPal"
#define MyAppVersion "0.10.0"
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
; Upgrades reuse the folder and the shortcut/printer choices from last time.
UsePreviousAppDir=yes
UsePreviousTasks=yes

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
; "Update only" leaves the user's own shortcuts alone (a deleted desktop icon
; stays deleted).
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; Check: not IsUpdateOnly
; Taskbar pinning needs a user-startmenu shortcut that Windows can discover
Name: "{userstartmenu}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Check: not IsUpdateOnly

[Registry]
; Keep PrintPal warm in the tray from sign-in; toggled later in Settings.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "PrintPal"; ValueData: """{app}\{#MyAppExeName}"" --background"; Flags: uninsdeletevalue; Tasks: startup; Check: not IsUpdateOnly

[Run]
; Optional: install the virtual printer. The .ps1 self-elevates (needs admin).
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\install_printer.ps1"" -PrintPalExe ""{app}\{#MyAppExeName}"""; Description: "Install the PrintPal virtual printer"; Flags: postinstall skipifsilent runascurrentuser; Tasks: virtualprinter; Check: not IsUpdateOnly
; Not offered when setup already put a running PrintPal back in the tray.
Filename: "{app}\{#MyAppExeName}"; Description: "Launch PrintPal"; Flags: nowait postinstall skipifsilent; Check: not WasRunning

[UninstallRun]
; Remove the virtual printer on uninstall (ignore errors if it was never installed).
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\uninstall_printer.ps1"""; Flags: runhidden; RunOnceId: "RemovePrintPalPrinter"

[Code]
const
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{E8A3B2C1-4D5F-6A7B-8C9D-0E1F2A3B4C5D}_is1';

var
  ModePage: TInputOptionWizardPage;
  InstalledVersion: String;
  PrintPalWasRunning: Boolean;

{ The version already on this PC, or '' for a fresh install. A per-user
  install lives in HKCU; an admin one in HKLM (64-bit view on 64-bit Windows). }
function FindInstalledVersion(): String;
begin
  Result := '';
  if RegQueryStringValue(HKCU, UninstallKey, 'DisplayVersion', Result) then Exit;
  if IsWin64 and RegQueryStringValue(HKLM64, UninstallKey, 'DisplayVersion', Result) then Exit;
  if RegQueryStringValue(HKLM, UninstallKey, 'DisplayVersion', Result) then Exit;
  Result := '';
end;

{ "Update only": PrintPal is already installed and the user (or a silent
  install) chose to just replace the program files -- no questions, and
  settings, history, learned crops, shortcuts, the sign-in option and the
  virtual printer are all left exactly as they are. }
function IsUpdateOnly(): Boolean;
begin
  Result := (InstalledVersion <> '') and
            ((ModePage = nil) or (ModePage.SelectedValueIndex = 0));
end;

function WasRunning(): Boolean;
begin
  Result := PrintPalWasRunning;
end;

procedure UpdateNextCaption();
begin
  if (ModePage <> nil) and (WizardForm.CurPageID = ModePage.ID) then
  begin
    if ModePage.SelectedValueIndex = 0 then
      WizardForm.NextButton.Caption := 'Update now'
    else
      WizardForm.NextButton.Caption := SetupMessage(msgButtonNext);
  end;
end;

procedure ModeClicked(Sender: TObject);
begin
  UpdateNextCaption();
end;

procedure InitializeWizard();
begin
  InstalledVersion := FindInstalledVersion();
  if InstalledVersion = '' then Exit;
  ModePage := CreateInputOptionPage(wpWelcome,
    'PrintPal is already installed',
    'Version ' + InstalledVersion + ' is on this PC. This installer has version {#MyAppVersion}.',
    'Choose how to continue, then click the button below.',
    True, False);
  ModePage.Add('Update only (recommended): just replace the program. Your settings, ' +
               'history, learned crops, shortcuts, sign-in option and PrintPal printer ' +
               'stay exactly as they are -- no more questions.');
  ModePage.Add('Change install options: pick the shortcuts, the sign-in option and ' +
               'the virtual printer again.');
  ModePage.SelectedValueIndex := 0;
  ModePage.CheckListBox.OnClickCheck := @ModeClicked;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  UpdateNextCaption();
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := IsUpdateOnly() and
            ((PageID = wpSelectDir) or (PageID = wpSelectTasks) or
             (PageID = wpSelectProgramGroup) or (PageID = wpReady));
end;

function IsProcessRunning(Name: String): Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec(ExpandConstant('{cmd}'),
                 '/C tasklist /NH /FI "IMAGENAME eq ' + Name + '" | find /I "' + Name + '"',
                 '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
end;

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
  PrintPalWasRunning := IsProcessRunning('PrintPal.exe');
  StopPrintPal();
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep <> ssPostInstall then Exit;
  { Closing PrintPal for the upgrade also stopped the virtual printer's
    watcher; start it again now instead of waiting for the next sign-in
    (harmless when the printer isn't installed: there is no such task). }
  ExecAsOriginalUser(ExpandConstant('{sys}\schtasks.exe'), '/Run /TN PrintPalPortWatcher',
                     '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  { Put a PrintPal that was running back in the tray, as it was. }
  if PrintPalWasRunning then
    ExecAsOriginalUser(ExpandConstant('{app}\{#MyAppExeName}'), '--background', '',
                       SW_SHOWNORMAL, ewNoWait, ResultCode);
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
