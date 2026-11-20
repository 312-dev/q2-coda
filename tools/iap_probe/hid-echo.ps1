# Desk rehearsal on Windows (docs/iphone-mode.md#desk-rehearsal): the Q2's iPhone HID interface, opened through
# Windows' own HID driver, is sent iAP2's detect as an output report (Windows sends it as a SET_REPORT on endpoint 0,
# the interface has no OUT endpoint) and the echo is read back from the interrupt IN endpoint. Needs the probe running
# with echo on and the min variant (Apple's driver leaves configuration 2's HID interface to Windows only then).
#   powershell -NoProfile -ExecutionPolicy Bypass -File hid-echo.ps1
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
public static class Hid {
    [DllImport("hid.dll")] static extern void HidD_GetHidGuid(out Guid g);
    [DllImport("hid.dll", SetLastError = true)] static extern bool HidD_SetOutputReport(SafeFileHandle h, byte[] b, int n);
    [DllImport("setupapi.dll", SetLastError = true)] static extern IntPtr SetupDiGetClassDevs(ref Guid g, IntPtr e, IntPtr w, int f);
    [DllImport("setupapi.dll", SetLastError = true)] static extern bool SetupDiEnumDeviceInterfaces(IntPtr s, IntPtr d, ref Guid g, int i, ref SP_DID did);
    [DllImport("setupapi.dll", SetLastError = true, CharSet = CharSet.Auto)] static extern bool SetupDiGetDeviceInterfaceDetail(IntPtr s, ref SP_DID did, IntPtr detail, int size, out int req, IntPtr info);
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Auto)] static extern SafeFileHandle CreateFile(string p, uint a, uint s, IntPtr sa, uint c, uint f, IntPtr t);
    [StructLayout(LayoutKind.Sequential)] struct SP_DID { public int cbSize; public Guid g; public int flags; public IntPtr r; }
    public static string Find() {
        Guid g; HidD_GetHidGuid(out g);
        IntPtr set = SetupDiGetClassDevs(ref g, IntPtr.Zero, IntPtr.Zero, 0x12);
        for (int i = 0; ; i++) {
            SP_DID did = new SP_DID(); did.cbSize = Marshal.SizeOf(did);
            if (!SetupDiEnumDeviceInterfaces(set, IntPtr.Zero, ref g, i, ref did)) return null;
            int req; SetupDiGetDeviceInterfaceDetail(set, ref did, IntPtr.Zero, 0, out req, IntPtr.Zero);
            IntPtr buf = Marshal.AllocHGlobal(req);
            Marshal.WriteInt32(buf, IntPtr.Size == 8 ? 8 : 6);
            SetupDiGetDeviceInterfaceDetail(set, ref did, buf, req, out req, IntPtr.Zero);
            string path = Marshal.PtrToStringAuto(buf + 4);
            Marshal.FreeHGlobal(buf);
            if (path.ToLower().Contains("vid_05ac&pid_12a8") || path.ToLower().Contains("vid_1d6b&pid_0106")) return path; // Apple's, or :linuxid's
        }
    }
    public static string Echo(string path) {
        SafeFileHandle h = CreateFile(path, 0xC0000000, 3, IntPtr.Zero, 3, 0, IntPtr.Zero);
        if (h.IsInvalid) return "open failed: " + Marshal.GetLastWin32Error();
        // output report 14 (9 bytes): link control 0, then the detect FF 55 02 00 EE 10, zero padded
        byte[] r = new byte[10]; r[0] = 14; r[1] = 0;
        byte[] det = { 0xFF, 0x55, 0x02, 0x00, 0xEE, 0x10 }; Array.Copy(det, 0, r, 2, 6);
        if (!HidD_SetOutputReport(h, r, r.Length)) return "SET_REPORT failed: " + Marshal.GetLastWin32Error();
        // ReadFile takes a buffer of the longest input report (767 + id); the echo is report 2
        using (FileStream fs = new FileStream(h, FileAccess.Read, 768)) {
            byte[] inb = new byte[768];
            var t = fs.ReadAsync(inb, 0, inb.Length);
            if (!t.Wait(3000)) return "sent; no input report within 3 s";
            return "sent; input report: " + BitConverter.ToString(inb, 0, 10);
        }
    }
}
'@
$p = [Hid]::Find()
if (-not $p) { 'no HID device 05ac:12a8 (is the min variant running?)'; exit 1 }
"device: $p"
[Hid]::Echo($p)
