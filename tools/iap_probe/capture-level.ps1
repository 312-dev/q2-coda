# Each active capture endpoint's name, mute and level (Windows Core Audio); with an argument, the endpoints whose
# name holds it are unmuted at full level (the Q2's test device only: 'AC Interface')
param([string]$Unmute = '')
Add-Type -TypeDefinition @'
using System; using System.Runtime.InteropServices;
[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator { int EnumAudioEndpoints(int flow, int mask, out IMMDeviceCollection c); }
[Guid("0BD7A1BE-7A1A-44DB-8397-CC5392387B5E"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceCollection { int GetCount(out int n); int Item(int i, out IMMDevice d); }
[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice { int Activate(ref Guid iid, int ctx, IntPtr p, [MarshalAs(UnmanagedType.IUnknown)] out object o); int OpenPropertyStore(int a, out IPropertyStore s); }
[Guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IPropertyStore { int GetCount(out int n); int GetAt(int i, out PK k); int GetValue(ref PK k, out PV v); }
[StructLayout(LayoutKind.Sequential)] public struct PK { public Guid f; public int p; }
[StructLayout(LayoutKind.Sequential)] public struct PV { public short vt, r1, r2, r3; public IntPtr p; public IntPtr q; }
[Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioEndpointVolume { int a(IntPtr x); int b(IntPtr x); int GetChannelCount(out int n); int SetMasterVolumeLevel(float l, ref Guid g); int SetMasterVolumeLevelScalar(float l, ref Guid g); int GetMasterVolumeLevel(out float l); int GetMasterVolumeLevelScalar(out float l); int c1(); int c2(); int c3(); int c4(); int SetMute(bool m, ref Guid g); int GetMute(out bool m); }
[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class MMDE {}
public static class Cap { public static string Run(string unmute) {
  var e = (IMMDeviceEnumerator)new MMDE(); IMMDeviceCollection c; e.EnumAudioEndpoints(1, 1, out c); int n; c.GetCount(out n);
  string o = ""; for (int i = 0; i < n; i++) { IMMDevice d; c.Item(i, out d); IPropertyStore ps; d.OpenPropertyStore(0, out ps);
    PK k = new PK { f = new Guid("a45c254e-df1c-4efd-8020-67d146a850e0"), p = 14 }; PV v; ps.GetValue(ref k, out v);
    string name = Marshal.PtrToStringUni(v.p); Guid iid = typeof(IAudioEndpointVolume).GUID; object ov; d.Activate(ref iid, 23, IntPtr.Zero, out ov);
    var vol = (IAudioEndpointVolume)ov; bool m; float s; Guid z = Guid.Empty;
    if (unmute.Length > 0 && name.Contains(unmute)) { vol.SetMute(false, ref z); vol.SetMasterVolumeLevelScalar(1.0f, ref z); }
    vol.GetMute(out m); vol.GetMasterVolumeLevelScalar(out s);
    o += name + " | mute " + m + " | level " + s + "\n"; } return o; } }
'@
[Cap]::Run($Unmute)
