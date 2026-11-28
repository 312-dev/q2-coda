# Builds btrelay.exe beside this script from BtRelay.cs, with the C# compiler and the WinRT metadata that
# ship in Windows: nothing to install (docs/bt-relay.md#on-the-laptop). Run by ../btrelay.sh.
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$csc = "$env:windir\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
$meta = "$env:windir\System32\WinMetadata"
$gac = "$env:windir\Microsoft.NET\assembly\GAC_MSIL"
# The facades the WinRT metadata names: System.Runtime for its base types, and the event token's assembly.
$refs = 'System.Runtime', 'System.Runtime.InteropServices.WindowsRuntime' | ForEach-Object {
    (Get-ChildItem "$gac\$_" -Recurse -Filter "$_.dll" | Select-Object -First 1).FullName
}
$refs += 'Windows.Foundation', 'Windows.Devices', 'Windows.Media' | ForEach-Object { "$meta\$_.winmd" }
& $csc /nologo /optimize /platform:x64 "/out:$here\btrelay.exe" ($refs | ForEach-Object { "/r:$_" }) "$here\BtRelay.cs"
exit $LASTEXITCODE
