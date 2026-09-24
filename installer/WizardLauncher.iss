; Wizard Launcher - Inno Setup script (Windows installer)
;
; Input: the jpackage app-image built by
;   gradlew :launcher-app:jpackage -PjpackageType=app-image
; which already contains the Java 17 runtime, the launcher, the helper jars
; and resources\ (bundled 1.16.5 server + ViaProxy).
;
; Version: pass /DMyAppVersion=2.0.0 on the ISCC command line (CI does).
;
; Per-user install (no UAC), program under Programs\, player data in
; {localappdata}\WizardLauncher - kept apart so uninstalling never touches
; the world unless the player explicitly asks.

#ifndef MyAppVersion
  #define MyAppVersion "2.0.0"
#endif
#define MyAppName "Wizard Launcher"
#define MyAppPublisher "Foxy"
#define MyAppExeName "WizardLauncher.exe"
#define MyDataDirName "WizardLauncher"
#define ImageDir "..\launcher-app\build\jpackage\WizardLauncher"

[Setup]
AppId={{8F3D1C2A-6B4E-4E17-9D5A-1E7C2A9B4F60}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
VersionInfoVersion={#MyAppVersion}
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={localappdata}\Programs\{#MyDataDirName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
AllowNoIcons=yes
OutputDir=..\build_installer
OutputBaseFilename=WizardLauncher-Windows-Setup-{#MyAppVersion}
#if FileExists(AddBackslash(SourcePath) + "app.ico")
SetupIconFile=app.ico
#endif
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
LicenseFile=..\resources\servers\1.16.5\eula.txt

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "{#ImageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; 1.x (Python) leftovers in the same program folder.
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
{ The world, settings and logs are NOT removed on uninstall unless asked. }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\{#MyDataDirName}');
    if DirExists(DataDir) then
    begin
      if MsgBox('Also delete your saved world, progress and downloaded game files?' + #13#10 + #13#10 +
                DataDir + #13#10 + #13#10 +
                'Choose No to keep them - reinstalling later will pick up where you left off.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
    end;
  end;
end;
