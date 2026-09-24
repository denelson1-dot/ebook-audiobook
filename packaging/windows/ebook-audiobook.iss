; The Windows installer: a wizard, an entry in Add/Remove Programs, shortcuts,
; and an uninstaller. Built by packaging/windows/build.py, which stages a
; standalone Python with the app already installed in it; this script only
; packages that folder. See that file for why each piece is the way it is.
;
;   ISCC /DAppVersion=1.6.1 /DStage=<stage dir> /DRoot=<repo root> /O<out dir> ebook-audiobook.iss
;
; Saved as UTF-8 with a byte-order mark: without one, Inno Setup reads the
; file as ANSI and the translated messages below come out garbled.

#ifndef AppVersion
  #error Pass /DAppVersion=<version> (build.py does)
#endif
#ifndef Stage
  #error Pass /DStage=<stage dir> (build.py does)
#endif
#ifndef Root
  #define Root "..\.."
#endif

[Setup]
; Windows knows the app by this id: upgrades, and its Add/Remove Programs
; entry. It must never change.
AppId={{C2B6260A-7FC8-4A3F-945D-47C4FFB734FE}
AppName=ebook-audiobook
AppVersion={#AppVersion}
AppVerName=ebook-audiobook {#AppVersion}
AppPublisher=denelson1
AppPublisherURL=https://github.com/denelson1-dot/ebook-audiobook
AppSupportURL=https://github.com/denelson1-dot/ebook-audiobook/issues
AppUpdatesURL=https://github.com/denelson1-dot/ebook-audiobook/releases
VersionInfoVersion={#AppVersion}
; Per-user, like the PowerShell installer: no administrator prompt, and the
; in-app updater never needs one either.
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\ebook-audiobook
DisableProgramGroupPage=yes
DisableDirPage=auto
UsePreviousAppDir=yes
LicenseFile={#Root}\LICENSE
OutputBaseFilename=ebook-audiobook-setup-{#AppVersion}
SetupIconFile={#Stage}\app.ico
UninstallDisplayIcon={app}\app.ico
UninstallDisplayName=ebook-audiobook
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
; PyTorch has no 32-bit or ARM64 Windows build.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
; Close a running copy (it holds the bundled Python's files) before upgrading.
CloseApplications=yes
RestartApplications=no
; The command-line folder goes on PATH; tell running programs so.
ChangesEnvironment=yes
ShowLanguageDialog=auto

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "ja"; MessagesFile: "compiler:Languages\Japanese.isl"

[CustomMessages]
en.DeleteData=Also delete this app's data: the books you imported, narration in progress and your settings?%n%nIt is in:%n%1%n%nAudiobooks saved to a library folder of your own are never touched. Choose No to keep everything for a reinstall.
fr.DeleteData=Supprimer aussi les données de l'application : les livres importés, les narrations en cours et vos réglages ?%n%nElles se trouvent dans :%n%1%n%nLes livres audio enregistrés dans votre propre dossier de bibliothèque ne sont jamais touchés. Choisissez Non pour tout garder en vue d'une réinstallation.
es.DeleteData=¿Borrar también los datos de la aplicación: los libros importados, las narraciones en curso y tus ajustes?%n%nEstán en:%n%1%n%nLos audiolibros guardados en una carpeta de biblioteca propia nunca se tocan. Elige No para conservarlo todo para una reinstalación.
ja.DeleteData=このアプリのデータ（取り込んだ本、進行中のナレーション、設定）も削除しますか？%n%n保存場所:%n%1%n%nご自分のライブラリフォルダーに保存したオーディオブックは削除されません。再インストールのためにすべて残す場合は「いいえ」を選んでください。

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; The app's own package is replaced whole on an upgrade, so a module a new
; version dropped cannot linger and be imported.
Type: filesandordirs; Name: "{app}\python\Lib\site-packages\ebook_audiobook"

[Files]
Source: "{#Stage}\python\*"; DestDir: "{app}\python"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#Stage}\bin\*"; DestDir: "{app}\bin"; Flags: ignoreversion
Source: "{#Stage}\app.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Through pythonw and `-m ebook_audiobook --gui`: no console window, and no
; pip-generated launcher with the build machine's paths baked into it.
Name: "{userprograms}\ebook-audiobook"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m ebook_audiobook --gui"; WorkingDir: "{app}"; IconFilename: "{app}\app.ico"
Name: "{userdesktop}\ebook-audiobook"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m ebook_audiobook --gui"; WorkingDir: "{app}"; IconFilename: "{app}\app.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: "-m ebook_audiobook --gui"; WorkingDir: "{app}"; Description: "{cm:LaunchProgram,ebook-audiobook}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Whatever pip added after setup (the speech engine, 2-5 GB) is not in the
; uninstaller's own list of files, so the folders go whole.
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\bin"

[Code]
const
  EnvKey = 'Environment';

function DataDir(): String;
begin
  Result := ExpandConstant('{localappdata}\ebook-audiobook');
end;

function PathHas(Paths, Dir: String): Boolean;
begin
  Result := Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(Paths) + ';') > 0;
end;

procedure AddToPath(Dir: String);
var
  Paths: String;
begin
  if not RegQueryStringValue(HKCU, EnvKey, 'Path', Paths) then
    Paths := '';
  if PathHas(Paths, Dir) then
    exit;
  if (Paths <> '') and (Copy(Paths, Length(Paths), 1) <> ';') then
    Paths := Paths + ';';
  RegWriteExpandStringValue(HKCU, EnvKey, 'Path', Paths + Dir);
end;

procedure RemoveFromPath(Dir: String);
var
  Paths, Kept, Part: String;
  P: Integer;
begin
  if not RegQueryStringValue(HKCU, EnvKey, 'Path', Paths) then
    exit;
  if not PathHas(Paths, Dir) then
    exit;
  Kept := '';
  Paths := Paths + ';';
  while Paths <> '' do
  begin
    P := Pos(';', Paths);
    Part := Copy(Paths, 1, P - 1);
    Delete(Paths, 1, P);
    if (Part <> '') and (CompareText(Part, Dir) <> 0) then
    begin
      if Kept <> '' then
        Kept := Kept + ';';
      Kept := Kept + Part;
    end;
  end;
  RegWriteExpandStringValue(HKCU, EnvKey, 'Path', Kept);
end;

{ Stop the processes that would hold Dir's files. By default those running
  from it: the app's own Python. ByCommandLine instead matches any process
  whose command line names it: the app window's browser, which runs from
  Program Files with its profile in the data folder.

  Never this PowerShell, whose own command line names Dir, nor Inno Setup's
  uninstaller, whose command line names the program folder too: an earlier
  version matched both, and killed the uninstaller partway through. }
procedure StopProcesses(Dir: String; ByCommandLine: Boolean);
var
  Quoted, Test: String;
  RC: Integer;
begin
  Quoted := Dir;
  { A single quote in a user name would end the PowerShell string early. }
  StringChangeEx(Quoted, '''', '''''', True);
  if ByCommandLine then
    Test := '($_.CommandLine -and $_.CommandLine.IndexOf($d, [StringComparison]::OrdinalIgnoreCase) -ge 0)'
  else
    Test := '($_.ExecutablePath -and $_.ExecutablePath.StartsWith($d, [StringComparison]::OrdinalIgnoreCase))';
  Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "' +
    '$d = ''' + Quoted + '''; ' +
    'Get-CimInstance Win32_Process | Where-Object { ' +
    '$_.ProcessId -ne $PID -and $_.Name -notlike ''*unins*'' -and ' + Test + ' } | ' +
    'ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"',
    '', SW_HIDE, ewWaitUntilTerminated, RC);
end;

{ The app's own Python, installed in Dir. }
procedure StopRunningFrom(Dir: String);
begin
  StopProcesses(Dir, False);
end;

{ Anything started with Dir on its command line: the app window's browser,
  and the real Python behind a venv's python.exe, which is only a launcher. }
procedure StopNaming(Dir: String);
begin
  StopProcesses(Dir, True);
end;

{ A copy installed by the PowerShell one-liner lives inside the data folder
  (venv\ and bin\). Two copies would fight over the same shortcuts and PATH, so
  the old program goes: the books, settings and voice model stay. }
procedure RemovePowerShellInstall();
var
  Old: String;
begin
  Old := DataDir();
  if not DirExists(Old + '\venv') then
    exit;
  StopNaming(Old + '\venv');
  StopNaming(Old + '\browser-profile');
  DelTree(Old + '\venv', True, True, True);
  DelTree(Old + '\bin', True, True, True);
  RemoveFromPath(Old + '\bin');
  DeleteFile(ExpandConstant('{userprograms}\ebook-audiobook.lnk'));
  DeleteFile(ExpandConstant('{userdesktop}\ebook-audiobook.lnk'));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  RemovePowerShellInstall();
  { An upgrade over a running copy: it holds the files about to be replaced. }
  StopRunningFrom(ExpandConstant('{app}'));
  StopNaming(DataDir() + '\browser-profile');
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    AddToPath(ExpandConstant('{app}\bin'));
end;

{ First thing, before the uninstaller looks at which files are in use: a
  running copy holds the bundled Python's files, and a silent uninstall can't
  ask anyone to close it. }
function InitializeUninstall(): Boolean;
begin
  StopRunningFrom(ExpandConstant('{app}'));
  StopNaming(DataDir() + '\browser-profile');
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Data: String;
begin
  Data := DataDir();
  if CurUninstallStep = usPostUninstall then
  begin
    RemoveFromPath(ExpandConstant('{app}\bin'));
    { Only the app window's Chromium cache; regenerated on the next launch. }
    DelTree(Data + '\browser-profile', True, True, True);
    if DirExists(Data) and not UninstallSilent() then
      if MsgBox(FmtMessage(CustomMessage('DeleteData'), [Data]),
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(Data, True, True, True);
  end;
end;
