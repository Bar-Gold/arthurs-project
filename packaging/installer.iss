; Inno Setup script for the client build.
;
; Produces a single Setup.exe: the client double-clicks it, gets a Start Menu
; entry, and is asked two questions -- start at logon, and keep the machine
; awake. Both are reversible from the uninstaller.
;
; Build with:  iscc packaging\installer.iss
; (or let packaging\build.ps1 do the whole thing.)

#define AppName "Facebook Auto-Poster"
#define AppVersion "1.4.0"
#define AppExe "FacebookAutoPoster.exe"
#define AppPublisher "Bar Goldstein"

[Setup]
AppId={{8B3C1F42-9D5E-4A77-B1C6-2E5A9F0D7C31}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\FacebookAutoPoster
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=FacebookAutoPoster-Setup-{#AppVersion}
SetupIconFile=app.ico
; The account blocking policy: the client cannot install without accepting it
; (the user's request, 2026-10-19). Built from policy.txt by make_policy.py.
LicenseFile=policy.rtf
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Admin: the install goes to Program Files and the optional power settings are
; machine-wide. The app itself runs as the ordinary user afterwards.
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
; The licence page holds the blocking policy, so it is named for it. The
; policy quotes LicenseAccepted word for word, and tests/test_policy.py keeps
; the two the same.
WizardLicense=Account blocking policy
LicenseLabel=Please read this before installing.
LicenseLabel3=Please read this policy (in Hebrew, with English below). You need to accept it to install.
LicenseAccepted=I &accept this policy
LicenseNotAccepted=I &do not accept this policy
; There is no setup guide to read. The app's own Welcome screen walks the
; client through Chrome and the Facebook login one step at a time, so the
; last page of the installer just says so.
FinishedLabel=[name] is installed.%n%nWhen it opens, it will walk you through the rest: starting Chrome and logging into Facebook. It takes about two minutes.

[Tasks]
Name: "desktopicon"; Description: "Create a shortcut on the desktop"; \
    GroupDescription: "Shortcuts:"

Name: "autostart"; Description: "Start {#AppName} when I sign in"; \
    GroupDescription: "Posting on schedule:"
; Deliberately unchecked by default. A machine that never sleeps is a real
; decision -- on a laptop kept in a bag it is a fire risk -- so it is opted
; into, never assumed. The script it runs is fully reversible: plugged in the
; PC stays awake with the lid shut, on battery only while the lid is open.
Name: "alwayson"; Description: "Keep this PC awake so scheduled posts go out"; \
    GroupDescription: "Posting on schedule:"; Flags: unchecked

[Files]
; The whole one-folder PyInstaller output.
Source: "..\dist\FacebookAutoPoster\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs
; Shipped so the uninstaller can put the power settings back, and so the
; logon task can be repaired without a source checkout.
Source: "..\scripts\setup_always_on.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion
; No setup guide is shipped. It used to open in Notepad at the end of the
; install (the user found that unprofessional, 2026-10-19), and it repeated
; what the Welcome screen already says, going out of date as the app changed.

[InstallDelete]
; Left behind by an older version, which shipped the guide.
Type: files; Name: "{app}\Setup Guide.txt"
Type: files; Name: "{group}\Setup Guide.lnk"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
; For the support call. Runs the bundle's own checks and shows the result,
; so a client who cannot describe a symptom can still send back a picture.
Name: "{group}\Check my setup"; Filename: "{app}\{#AppExe}"; Parameters: "--selftest"; Comment: "Checks this PC and shows a report you can send on"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
; Power settings AND the logon task.
Filename: "powershell.exe"; \
    Parameters: "-ExecutionPolicy Bypass -NoProfile -File ""{app}\scripts\setup_always_on.ps1"" -AppPath ""{app}\{#AppExe}"""; \
    StatusMsg: "Setting this PC up to post on schedule..."; \
    Flags: runhidden waituntilterminated; Check: WantsAlwaysOn

; The logon task only, leaving every power setting alone.
Filename: "powershell.exe"; \
    Parameters: "-ExecutionPolicy Bypass -NoProfile -File ""{app}\scripts\setup_always_on.ps1"" -AppPath ""{app}\{#AppExe}"" -SkipPower"; \
    StatusMsg: "Setting the app to start when you sign in..."; \
    Flags: runhidden waituntilterminated; Check: WantsAutostartOnly

Filename: "{app}\{#AppExe}"; Description: "Open {#AppName} now"; \
    Flags: nowait postinstall skipifsilent

[UninstallRun]
; Always attempted. The script reads its own backup file, so with nothing
; recorded it removes the logon task and leaves the power settings alone.
Filename: "powershell.exe"; \
    Parameters: "-ExecutionPolicy Bypass -NoProfile -File ""{app}\scripts\setup_always_on.ps1"" -Revert"; \
    Flags: runhidden waituntilterminated; RunOnceId: "RevertAlwaysOn"

[Code]
function WantsAlwaysOn(): Boolean;
begin
  Result := WizardIsTaskSelected('alwayson');
end;

function WantsAutostartOnly(): Boolean;
begin
  // "Always on" already registers the logon task, so running both would do
  // the same work twice and report it twice.
  Result := WizardIsTaskSelected('autostart') and not WizardIsTaskSelected('alwayson');
end;

function ChromeInstalled(): Boolean;
var
  Paths: TArrayOfString;
  I: Integer;
begin
  SetArrayLength(Paths, 3);
  Paths[0] := ExpandConstant('{commonpf}\Google\Chrome\Application\chrome.exe');
  Paths[1] := ExpandConstant('{commonpf32}\Google\Chrome\Application\chrome.exe');
  Paths[2] := ExpandConstant('{localappdata}\Google\Chrome\Application\chrome.exe');
  Result := False;
  for I := 0 to GetArrayLength(Paths) - 1 do
    if FileExists(Paths[I]) then
      Result := True;
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  // A warning, never a block. Chrome can perfectly well be installed after
  // this, and the app's own setup screen says so too -- refusing to install
  // would just leave the client with nothing.
  if not ChromeInstalled() then
    MsgBox('Google Chrome was not found on this PC.' + #13#10 + #13#10 +
           'Facebook Auto-Poster posts through a real Chrome window, so ' +
           'Chrome needs to be installed before it can post. You can carry ' +
           'on with this installation and install Chrome afterwards.',
           mbInformation, MB_OK);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  // The database, the Chrome profile and therefore the Facebook login all
  // live outside the install directory -- in C:\FBAutomation or under
  // LocalAppData. Uninstalling deliberately leaves them: a reinstall picks up
  // every group, template and posting record exactly where they were, and
  // nobody has to log into Facebook again.
  //
  // Written with // rather than { }: braces are a Pascal comment, so the
  // literal {app} that used to be in this sentence closed the comment early
  // and the rest of it was compiled as code.
  if CurUninstallStep = usPostUninstall then
    MsgBox('Facebook Auto-Poster has been removed.' + #13#10 + #13#10 +
           'Your groups, templates, posting history and Facebook login have ' +
           'been left on this PC, so reinstalling picks up where you left ' +
           'off. To erase them as well, delete the FBAutomation folder.',
           mbInformation, MB_OK);
end;
