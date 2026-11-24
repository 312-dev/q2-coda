// btrig.exe: this laptop as a Bluetooth A2DP sink for a Q2, a recorder of what it receives, and the Windows-side
// helpers the rig needs (docs/bt-rig.md). Built by tools/bt_rig.py (`build`) with the Roslyn csc of the installed .NET
// SDK against .NET Framework 4.8 and the Windows SDK's Windows.winmd, so nothing is fetched. Every command prints
// JSON lines on stdout.
//
//   btrig info                          the adapter, the render endpoints, the paired A2DP sources
//   btrig sessions                      audio sessions on every active render endpoint
//   btrig defaults                      the default output of each role
//   btrig set-default ROLE ID           a role's default output set (to put back one Windows moved)
//   btrig guard NAME SECONDS            while it runs, a default output named NAME goes back to the role's previous
//   btrig pair MAC [--refuse-after S]   pair with a device, the confirmation accepted here (no Windows dialog), or
//                                       held S seconds and refused (the laptop cancelling)
//   btrig unpair MAC                    forget it
//   btrig service MAC UUID on|off       one of a paired device's Bluetooth services on Windows
//   btrig link MAC                      the device's connection status
//   btrig scan SECONDS MAC [UUID]       a passive LE scan: advertisements from MAC or naming the service UUID
//   btrig create ID                     AudioPlaybackConnection.TryCreateFromId alone (does Windows take this source)
//   btrig receive --device MAC --seconds N --out FILE.wav [--route NAME] [--endpoint] [--no-open] [--stop-file F]
//   btrig selftest --route NAME --seconds N --out FILE.wav   a tone routed as receive routes, recorded as it records
//   btrig pipe HOST PORT                stdin and stdout joined to a TCP connection made from Windows
//
// receive: AudioPlaybackConnection (Windows 10 2004 and later) opens the A2DP stream from the device and Windows
// plays it on the default output. With --route, this process's output is first set to the named endpoint (an
// inaudible virtual one: Steam Streaming Speakers) and the recording is a process loopback capture of this process
// (AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK), which takes the audio before any endpoint volume. `selftest` proves
// that path with a tone; that Windows plays a received stream inside the process that opened it is assumed, not yet
// seen (no Q2 stream has opened), and receive stops at once if this process plays anywhere but the route. --endpoint records the default endpoint's loopback instead: everything playing, and audible.
// Windows offers the sink role only for a source it enabled it for; for a Q2, which also offers A2DP Sink, it does
// not (docs/bt-rig.md#why-windows-will-not-take-the-q2).
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Windows.Devices.Bluetooth;
using Windows.Devices.Enumeration;
using Windows.Media.Audio;

static class Json {
    public static string Q(string s) {
        if (s == null) return "null";
        var b = new StringBuilder("\"");
        foreach (var c in s) {
            if (c == '"' || c == '\\') b.Append('\\').Append(c);
            else if (c < 32) b.AppendFormat("\\u{0:x4}", (int)c);
            else b.Append(c);
        }
        return b.Append('"').ToString();
    }
    public static string Obj(params object[] kv) {
        var b = new StringBuilder("{");
        for (int i = 0; i < kv.Length; i += 2) {
            if (i > 0) b.Append(", ");
            b.Append(Q((string)kv[i])).Append(": ");
            var v = kv[i + 1];
            if (v == null) b.Append("null");
            else if (v is string) b.Append(Q((string)v));
            else if (v is bool) b.Append((bool)v ? "true" : "false");
            else if (v is double) b.Append(((double)v).ToString("R", System.Globalization.CultureInfo.InvariantCulture));
            else if (v is float) b.Append(((float)v).ToString("R", System.Globalization.CultureInfo.InvariantCulture));
            else if (v is RawJson) b.Append(((RawJson)v).Text);
            else b.Append(Convert.ToString(v, System.Globalization.CultureInfo.InvariantCulture));
        }
        return b.Append("}").ToString();
    }
    public static void Say(params object[] kv) { Console.Out.WriteLine(Obj(kv)); Console.Out.Flush(); }
}

class RawJson { public string Text; public RawJson(string t) { Text = t; } }

// ---- Core Audio COM interfaces (only the methods used; vtable order kept). [PreserveSig] on each: a ComImport
// method otherwise turns its HRESULT into an exception and takes an int return for an extra out parameter.

enum EDataFlow { eRender = 0, eCapture = 1, eAll = 2 }
enum ERole { eConsole = 0, eMultimedia = 1, eCommunications = 2 }

[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class MMDeviceEnumeratorCo { }

[ComImport, Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator {
    [PreserveSig] int EnumAudioEndpoints(EDataFlow flow, int stateMask, out IMMDeviceCollection devices);
    [PreserveSig] int GetDefaultAudioEndpoint(EDataFlow flow, ERole role, out IMMDevice device);
    [PreserveSig] int GetDevice([MarshalAs(UnmanagedType.LPWStr)] string id, out IMMDevice device);
}

[ComImport, Guid("0BD7A1BE-7A1A-44DB-8397-CC5392387B5E"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceCollection {
    [PreserveSig] int GetCount(out int count);
    [PreserveSig] int Item(int index, out IMMDevice device);
}

[ComImport, Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice {
    [PreserveSig] int Activate(ref Guid iid, int clsCtx, IntPtr activationParams, [MarshalAs(UnmanagedType.IUnknown)] out object iface);
    [PreserveSig] int OpenPropertyStore(int access, out IPropertyStore store);
    [PreserveSig] int GetId([MarshalAs(UnmanagedType.LPWStr)] out string id);
    [PreserveSig] int GetState(out int state);
}

[StructLayout(LayoutKind.Sequential)] struct PropertyKey { public Guid fmtid; public int pid; }

[StructLayout(LayoutKind.Explicit, Size = 24)]
struct PropVariant { [FieldOffset(0)] public ushort vt; [FieldOffset(8)] public IntPtr p; }

[ComImport, Guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IPropertyStore {
    [PreserveSig] int GetCount(out int count);
    [PreserveSig] int GetAt(int index, out PropertyKey key);
    [PreserveSig] int GetValue(ref PropertyKey key, out PropVariant value);
}

[ComImport, Guid("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioSessionManager2 {
    [PreserveSig] int GetAudioSessionControl(IntPtr guid, int flags, out IntPtr control);
    [PreserveSig] int GetSimpleAudioVolume(IntPtr guid, int crossProcess, out ISimpleAudioVolume volume);
    [PreserveSig] int GetSessionEnumerator(out IAudioSessionEnumerator sessions);
}

[ComImport, Guid("E2F5BB11-0570-40CA-ACDD-3AA01277DEE8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioSessionEnumerator {
    [PreserveSig] int GetCount(out int count);
    [PreserveSig] int GetSession(int index, out IAudioSessionControl2 session);
}

[ComImport, Guid("bfb7ff88-7239-4fc9-8fa2-07c950be9c6d"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioSessionControl2 {
    [PreserveSig] int GetState(out int state);
    [PreserveSig] int GetDisplayName([MarshalAs(UnmanagedType.LPWStr)] out string name);
    [PreserveSig] int SetDisplayName(string name, IntPtr ctx);
    [PreserveSig] int GetIconPath([MarshalAs(UnmanagedType.LPWStr)] out string path);
    [PreserveSig] int SetIconPath(string path, IntPtr ctx);
    [PreserveSig] int GetGroupingParam(out Guid g);
    [PreserveSig] int SetGroupingParam(ref Guid g, IntPtr ctx);
    [PreserveSig] int RegisterAudioSessionNotification(IntPtr n);
    [PreserveSig] int UnregisterAudioSessionNotification(IntPtr n);
    [PreserveSig] int GetSessionIdentifier([MarshalAs(UnmanagedType.LPWStr)] out string id);
    [PreserveSig] int GetSessionInstanceIdentifier([MarshalAs(UnmanagedType.LPWStr)] out string id);
    [PreserveSig] int GetProcessId(out int pid);
    [PreserveSig] int IsSystemSoundsSession();
}

[ComImport, Guid("87CE5498-68D6-44E5-9215-6DA47EF883D8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface ISimpleAudioVolume {
    [PreserveSig] int SetMasterVolume(float level, ref Guid ctx);
    [PreserveSig] int GetMasterVolume(out float level);
    [PreserveSig] int SetMute(int mute, ref Guid ctx);
    [PreserveSig] int GetMute(out int mute);
}

[ComImport, Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioEndpointVolume {
    [PreserveSig] int RegisterControlChangeNotify(IntPtr n);
    [PreserveSig] int UnregisterControlChangeNotify(IntPtr n);
    [PreserveSig] int GetChannelCount(out int n);
    [PreserveSig] int SetMasterVolumeLevel(float db, ref Guid ctx);
    [PreserveSig] int SetMasterVolumeLevelScalar(float v, ref Guid ctx);
    [PreserveSig] int GetMasterVolumeLevel(out float db);
    [PreserveSig] int GetMasterVolumeLevelScalar(out float v);
    [PreserveSig] int SetChannelVolumeLevel(int ch, float db, ref Guid ctx);
    [PreserveSig] int SetChannelVolumeLevelScalar(int ch, float v, ref Guid ctx);
    [PreserveSig] int GetChannelVolumeLevel(int ch, out float db);
    [PreserveSig] int GetChannelVolumeLevelScalar(int ch, out float v);
    [PreserveSig] int SetMute(int mute, ref Guid ctx);
    [PreserveSig] int GetMute(out int mute);
}

[ComImport, Guid("1CB9AD4C-DBFA-4c32-B178-C2F568A703B2"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioClient {
    [PreserveSig] int Initialize(int shareMode, int flags, long bufferDuration, long periodicity, IntPtr format, IntPtr session);
    [PreserveSig] int GetBufferSize(out int frames);
    [PreserveSig] int GetStreamLatency(out long latency);
    [PreserveSig] int GetCurrentPadding(out int frames);
    [PreserveSig] int IsFormatSupported(int shareMode, IntPtr format, out IntPtr closest);
    [PreserveSig] int GetMixFormat(out IntPtr format);
    [PreserveSig] int GetDevicePeriod(out long def, out long min);
    [PreserveSig] int Start();
    [PreserveSig] int Stop();
    [PreserveSig] int Reset();
    [PreserveSig] int SetEventHandle(IntPtr handle);
    [PreserveSig] int GetService(ref Guid iid, out IntPtr service);
}

[ComImport, Guid("C8ADBD64-E71E-48a0-A4DE-185C395CD317"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioCaptureClient {
    [PreserveSig] int GetBuffer(out IntPtr data, out int frames, out int flags, out long devicePosition, out long qpcPosition);
    [PreserveSig] int ReleaseBuffer(int frames);
    [PreserveSig] int GetNextPacketSize(out int frames);
}

[ComImport, Guid("F294ACFC-3146-4483-A7BF-ADDCA7C260E2"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioRenderClient {
    [PreserveSig] int GetBuffer(int frames, out IntPtr data);
    [PreserveSig] int ReleaseBuffer(int frames, int flags);
}

[ComImport, Guid("72A22D78-CDE4-431D-B8CC-843A71199B6D"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IActivateAudioInterfaceAsyncOperation {
    [PreserveSig] int GetActivateResult(out int hr, [MarshalAs(UnmanagedType.IUnknown)] out object iface);
}

[ComImport, Guid("41D949AB-9862-444A-80F6-C261334DA5EB"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IActivateAudioInterfaceCompletionHandler {
    [PreserveSig] int ActivateCompleted(IActivateAudioInterfaceAsyncOperation op);
}

[ComImport, Guid("94ea2b94-e9cc-49e0-c0ff-ee64ca8f5b90"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAgileObject { }

[ClassInterface(ClassInterfaceType.None), ComVisible(true)]
class Activated : IActivateAudioInterfaceCompletionHandler, IAgileObject {
    public readonly ManualResetEvent Done = new ManualResetEvent(false);
    public int Hr; public object Iface;
    public int ActivateCompleted(IActivateAudioInterfaceAsyncOperation op) {
        op.GetActivateResult(out Hr, out Iface);
        Done.Set();
        return 0;
    }
}

static class Audio {
    public static Guid IID_IAudioClient = new Guid("1CB9AD4C-DBFA-4c32-B178-C2F568A703B2");
    public static Guid IID_IAudioCaptureClient = new Guid("C8ADBD64-E71E-48a0-A4DE-185C395CD317");
    public static Guid IID_IAudioRenderClient = new Guid("F294ACFC-3146-4483-A7BF-ADDCA7C260E2");
    public static Guid IID_ISimpleAudioVolume = new Guid("87CE5498-68D6-44E5-9215-6DA47EF883D8");
    public static Guid IID_IAudioSessionManager2 = new Guid("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F");
    public static Guid IID_IAudioEndpointVolume = new Guid("5CDF2C82-841E-4546-9722-0CF74078229A");
    public const int CLSCTX_ALL = 23;
    public const int LOOPBACK = 0x00020000, EVENTCALLBACK = 0x00040000, AUTOCONVERTPCM = unchecked((int)0x80000000),
                     SRC_DEFAULT_QUALITY = 0x08000000;
    public static Guid NoContext = Guid.Empty;

    [DllImport("Mmdevapi.dll", ExactSpelling = true, PreserveSig = false)]
    static extern IActivateAudioInterfaceAsyncOperation ActivateAudioInterfaceAsync(
        [MarshalAs(UnmanagedType.LPWStr)] string path, [MarshalAs(UnmanagedType.LPStruct)] Guid riid, IntPtr activationParams,
        IActivateAudioInterfaceCompletionHandler handler);

    [DllImport("kernel32.dll")] public static extern IntPtr CreateEvent(IntPtr a, bool manual, bool initial, string name);
    [DllImport("kernel32.dll")] public static extern int WaitForSingleObject(IntPtr h, int ms);
    [DllImport("kernel32.dll")] public static extern bool QueryPerformanceCounter(out long v);
    [DllImport("kernel32.dll")] public static extern bool QueryPerformanceFrequency(out long v);

    public static void Check(int hr, string what) {
        if (hr < 0) throw new COMException(what + " failed", hr);
    }

    public static object Service(IAudioClient c, Guid iid, string what) {
        IntPtr p;
        Check(c.GetService(ref iid, out p), "GetService(" + what + ")");
        var o = Marshal.GetObjectForIUnknown(p);
        Marshal.Release(p);
        return o;
    }

    public static IMMDeviceEnumerator Enumerator() { return (IMMDeviceEnumerator)new MMDeviceEnumeratorCo(); }

    public static string Name(IMMDevice d) {
        IPropertyStore ps;
        if (d.OpenPropertyStore(0, out ps) < 0) return null;
        var key = new PropertyKey { fmtid = new Guid("a45c254e-df1c-4efd-8020-67d146a850e0"), pid = 14 };
        PropVariant v;
        if (ps.GetValue(ref key, out v) < 0 || v.vt != 31) return null;
        return Marshal.PtrToStringUni(v.p);
    }

    public static List<IMMDevice> RenderEndpoints() {
        IMMDeviceCollection c;
        Check(Enumerator().EnumAudioEndpoints(EDataFlow.eRender, 1, out c), "EnumAudioEndpoints");
        int n; c.GetCount(out n);
        var list = new List<IMMDevice>();
        for (int i = 0; i < n; i++) { IMMDevice d; c.Item(i, out d); list.Add(d); }
        return list;
    }

    public static IMMDevice DefaultRender() {
        IMMDevice d;
        Check(Enumerator().GetDefaultAudioEndpoint(EDataFlow.eRender, ERole.eMultimedia, out d), "GetDefaultAudioEndpoint");
        return d;
    }

    public static T Activate<T>(IMMDevice d, Guid iid) {
        object o;
        Check(d.Activate(ref iid, CLSCTX_ALL, IntPtr.Zero, out o), "Activate");
        return (T)o;
    }

    // Every session on every active render endpoint: (endpoint name, pid, display name, state, volume, mute, control)
    public static IEnumerable<Tuple<string, int, string, int, float, bool, IAudioSessionControl2>> Sessions() {
        foreach (var d in RenderEndpoints()) {
            string name = Name(d);
            IAudioSessionManager2 m;
            try { m = Activate<IAudioSessionManager2>(d, IID_IAudioSessionManager2); } catch (COMException) { continue; }
            IAudioSessionEnumerator e;
            if (m.GetSessionEnumerator(out e) < 0) continue;
            int n; e.GetCount(out n);
            for (int i = 0; i < n; i++) {
                IAudioSessionControl2 s;
                if (e.GetSession(i, out s) < 0) continue;
                int pid, st, mute; string dn; float vol;
                s.GetProcessId(out pid); s.GetState(out st); s.GetDisplayName(out dn);
                var v = (ISimpleAudioVolume)s;
                v.GetMasterVolume(out vol); v.GetMute(out mute);
                yield return Tuple.Create(name, pid, dn, st, vol, mute != 0, s);
            }
        }
    }

    // This process's sessions at full volume and unmuted. Windows keeps an app's session volume and mute per
    // endpoint from one run to the next, so a run left at 0 would record silence ever after.
    public static void Unity(int pid) {
        var g = Guid.Empty;
        foreach (var s in Sessions()) {
            if (s.Item2 != pid) continue;
            var v = (ISimpleAudioVolume)s.Item7;
            v.SetMute(0, ref g);
            v.SetMasterVolume(1f, ref g);
        }
    }

    // The format asked for in every capture: 48 kHz stereo 32-bit float (the A2DP stream's own rate on a Q2).
    public static IntPtr FloatFormat(int rate, int channels) {
        var p = Marshal.AllocHGlobal(18);
        var w = new byte[18];
        BitConverter.GetBytes((short)3).CopyTo(w, 0);              // WAVE_FORMAT_IEEE_FLOAT
        BitConverter.GetBytes((short)channels).CopyTo(w, 2);
        BitConverter.GetBytes(rate).CopyTo(w, 4);
        BitConverter.GetBytes(rate * channels * 4).CopyTo(w, 8);
        BitConverter.GetBytes((short)(channels * 4)).CopyTo(w, 12);
        BitConverter.GetBytes((short)32).CopyTo(w, 14);
        Marshal.Copy(w, 0, p, 18);
        return p;
    }

    public static IAudioClient ProcessLoopback(int pid) {
        // AUDIOCLIENT_ACTIVATION_PARAMS { PROCESS_LOOPBACK (1), { pid, PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE } }
        var prm = Marshal.AllocHGlobal(12);
        Marshal.WriteInt32(prm, 0, 1); Marshal.WriteInt32(prm, 4, pid); Marshal.WriteInt32(prm, 8, 0);
        var pv = Marshal.AllocHGlobal(24);
        for (int i = 0; i < 24; i += 4) Marshal.WriteInt32(pv, i, 0);
        Marshal.WriteInt16(pv, 0, 65);                              // VT_BLOB
        Marshal.WriteInt32(pv, 8, 12);
        Marshal.WriteIntPtr(pv, 16, prm);
        var h = new Activated();
        ActivateAudioInterfaceAsync("VAD\\Process_Loopback", IID_IAudioClient, pv, h);
        if (!h.Done.WaitOne(5000)) throw new TimeoutException("process loopback activation");
        Check(h.Hr, "process loopback activation");
        return (IAudioClient)h.Iface;
    }
}

// ---- Per-application default endpoint: what Settings > Sound > Volume mixer sets for an app ("Output device").
// Not a published API: Windows.Media.Internal.AudioPolicyConfig's factory, its layout as of Windows 10 21H2
// (EarTrumpet uses the same). Only this process's own choice is set, and cleared again at the end.

[ComImport, Guid("ab3d4648-e242-459f-b02f-541c70306324"), InterfaceType(ComInterfaceType.InterfaceIsIInspectable)]
interface IAudioPolicyConfigFactory {
    [PreserveSig] int Unused1(); [PreserveSig] int Unused2(); [PreserveSig] int Unused3(); [PreserveSig] int Unused4();
    [PreserveSig] int Unused5(); [PreserveSig] int Unused6(); [PreserveSig] int Unused7(); [PreserveSig] int Unused8();
    [PreserveSig] int Unused9(); [PreserveSig] int Unused10(); [PreserveSig] int Unused11(); [PreserveSig] int Unused12();
    [PreserveSig] int Unused13(); [PreserveSig] int Unused14(); [PreserveSig] int Unused15(); [PreserveSig] int Unused16();
    [PreserveSig] int Unused17(); [PreserveSig] int Unused18(); [PreserveSig] int Unused19();
    [PreserveSig] int SetPersistedDefaultAudioEndpoint(int pid, EDataFlow flow, ERole role, IntPtr deviceId);
    [PreserveSig] int GetPersistedDefaultAudioEndpoint(int pid, EDataFlow flow, ERole role, out IntPtr deviceId);
}

// The system's default endpoint per role (Settings > Sound's "Choose where to play sound"). Not a published API:
// the PolicyConfig client every sound switcher uses. Only `defaults` reads it and `set-default` writes it.
[ComImport, Guid("870af99c-171d-4f9e-af0d-e63df40c2bc9")] class PolicyConfigClient { }

[ComImport, Guid("f8679f50-850a-41cf-9c72-430f290290c8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IPolicyConfig {
    [PreserveSig] int GetMixFormat(); [PreserveSig] int GetDeviceFormat(); [PreserveSig] int ResetDeviceFormat();
    [PreserveSig] int SetDeviceFormat(); [PreserveSig] int GetProcessingPeriod(); [PreserveSig] int SetProcessingPeriod();
    [PreserveSig] int GetShareMode(); [PreserveSig] int SetShareMode(); [PreserveSig] int GetPropertyValue();
    [PreserveSig] int SetPropertyValue();
    [PreserveSig] int SetDefaultEndpoint([MarshalAs(UnmanagedType.LPWStr)] string id, ERole role);
}

static class Policy {
    [DllImport("combase.dll", PreserveSig = false)]
    static extern void RoGetActivationFactory(IntPtr classId, [MarshalAs(UnmanagedType.LPStruct)] Guid iid,
                                              [MarshalAs(UnmanagedType.IUnknown)] out object factory);
    [DllImport("combase.dll", PreserveSig = false)]
    static extern void WindowsCreateString([MarshalAs(UnmanagedType.LPWStr)] string s, int len, out IntPtr h);
    [DllImport("combase.dll")] static extern int WindowsDeleteString(IntPtr h);
    [DllImport("combase.dll")] static extern IntPtr WindowsGetStringRawBuffer(IntPtr h, out int len);

    static IAudioPolicyConfigFactory Factory() {
        IntPtr cls; WindowsCreateString("Windows.Media.Internal.AudioPolicyConfig", 40, out cls);
        object f;
        try { RoGetActivationFactory(cls, new Guid("ab3d4648-e242-459f-b02f-541c70306324"), out f); }
        finally { WindowsDeleteString(cls); }
        return (IAudioPolicyConfigFactory)f;
    }

    // An endpoint's MMDevice id as the policy names it: its render device interface path.
    static string InterfacePath(string mmId) {
        return "\\\\?\\SWD#MMDEVAPI#" + mmId + "#{e6327cad-dcec-4949-ae8a-991e976a79d2}";
    }

    // This process's output set to the endpoint mmId (null: back to following the default), for every role.
    public static void Route(int pid, string mmId) {
        var f = Factory();
        IntPtr h = IntPtr.Zero;
        if (mmId != null) { var path = InterfacePath(mmId); WindowsCreateString(path, path.Length, out h); }
        try {
            foreach (var role in new[] { ERole.eConsole, ERole.eMultimedia })
                Audio.Check(f.SetPersistedDefaultAudioEndpoint(pid, EDataFlow.eRender, role, h),
                            "SetPersistedDefaultAudioEndpoint(" + (mmId == null ? "clear" : "set") + ", " + role + ")");
        } finally { if (h != IntPtr.Zero) WindowsDeleteString(h); }
    }

    // The same for an endpoint, giving this process a session there first: Windows refuses (E_INVALIDARG) a route
    // for a process with no audio session. The stream returned holds that session; it is never started.
    public static IAudioClient Route(int pid, IMMDevice dev) {
        var hold = Audio.Activate<IAudioClient>(dev, Audio.IID_IAudioClient);
        Audio.Check(hold.Initialize(0, Audio.AUTOCONVERTPCM, 2000000, 0, Audio.FloatFormat(48000, 2), IntPtr.Zero), "Initialize (session)");
        string id; dev.GetId(out id);
        Route(pid, id);
        return hold;
    }

    public static string Routed(int pid) {
        IntPtr h;
        if (Factory().GetPersistedDefaultAudioEndpoint(pid, EDataFlow.eRender, ERole.eMultimedia, out h) < 0 || h == IntPtr.Zero)
            return null;
        int n; var p = WindowsGetStringRawBuffer(h, out n);
        var s = Marshal.PtrToStringUni(p, n);
        WindowsDeleteString(h);
        return s;
    }
}

// ---- WAV writer: IEEE float, the header patched with the sizes at Close

class Wav : IDisposable {
    readonly FileStream f; readonly BinaryWriter w; long data; public readonly int Rate, Channels;
    public Wav(string path, int rate, int channels) {
        Rate = rate; Channels = channels;
        f = new FileStream(path, FileMode.Create, FileAccess.Write);
        w = new BinaryWriter(f);
        w.Write(Encoding.ASCII.GetBytes("RIFF")); w.Write(0); w.Write(Encoding.ASCII.GetBytes("WAVEfmt "));
        w.Write(16); w.Write((short)3); w.Write((short)channels); w.Write(rate); w.Write(rate * channels * 4);
        w.Write((short)(channels * 4)); w.Write((short)32);
        w.Write(Encoding.ASCII.GetBytes("data")); w.Write(0);
    }
    public long Frames { get { return data / (4 * Channels); } }
    public void Write(byte[] b, int n) { w.Write(b, 0, n); data += n; }
    public void Silence(long frames) {
        var z = new byte[65536];
        long left = frames * 4 * Channels;
        while (left > 0) { int k = (int)Math.Min(left, z.Length); w.Write(z, 0, k); left -= k; data += k; }
    }
    public void Dispose() {
        w.Flush();
        f.Seek(4, SeekOrigin.Begin); w.Write((int)(36 + data));
        f.Seek(40, SeekOrigin.Begin); w.Write((int)data);
        w.Close();
    }
}

// ---- A capture loop: packets in order, gaps the engine did not deliver filled with silence by their QPC times

class Capture {
    readonly IAudioClient client; readonly IAudioCaptureClient cap; readonly IntPtr ev; readonly int channels;
    public readonly int Rate;
    public long Frames, GapFrames, Packets, SilentPackets, Discontinuities;
    public double PeakAbs; public string FirstQpcUtc;
    long qpcFreq;

    public Capture(IAudioClient c, int rate, int channels, bool fmtFromMix) {
        client = c; Rate = rate; this.channels = channels;
        var fmt = Audio.FloatFormat(rate, channels);
        Audio.Check(client.Initialize(0, Audio.LOOPBACK | Audio.EVENTCALLBACK | Audio.AUTOCONVERTPCM | Audio.SRC_DEFAULT_QUALITY,
                                      2000000, 0, fmt, IntPtr.Zero), "IAudioClient.Initialize");
        ev = Audio.CreateEvent(IntPtr.Zero, false, false, null);
        Audio.Check(client.SetEventHandle(ev), "SetEventHandle");
        cap = (IAudioCaptureClient)Audio.Service(client, Audio.IID_IAudioCaptureClient, "capture");
        Audio.QueryPerformanceFrequency(out qpcFreq);
    }

    static long Now100ns(long freq) { long q; Audio.QueryPerformanceCounter(out q); return (long)(q * (10000000.0 / freq)); }

    // Records until `until` (a QPC time in 100 ns) or stop is set; returns the QPC time of frame 0.
    public long Run(Wav wav, long until, Func<bool> stop, Action<double> tick) {
        Audio.Check(client.Start(), "Start");
        long t0 = Now100ns(qpcFreq);           // frame 0 is the start of the capture
        long nextTick = t0 + 10000000;
        var buf = new byte[0];
        while (true) {
            Audio.WaitForSingleObject(ev, 100);
            long now = Now100ns(qpcFreq);
            int n;
            while (cap.GetNextPacketSize(out n) >= 0 && n > 0) {
                IntPtr data; int frames, flags; long dev, qpc;
                if (cap.GetBuffer(out data, out frames, out flags, out dev, out qpc) < 0) break;
                if (FirstQpcUtc == null) FirstQpcUtc = DateTime.UtcNow.ToString("o");
                Packets++;
                if ((flags & 1) != 0) Discontinuities++;
                // where this packet's first frame belongs on the capture's clock; a gap of over 20 ms is filled
                long at = (long)Math.Round((qpc - t0) * (Rate / 1e7));
                if (at > wav.Frames + Rate / 50) { GapFrames += at - wav.Frames; wav.Silence(at - wav.Frames); }
                int bytes = frames * 4 * channels;
                if (buf.Length < bytes) buf = new byte[bytes];
                if ((flags & 2) != 0) { Array.Clear(buf, 0, bytes); SilentPackets++; }
                else Marshal.Copy(data, buf, 0, bytes);
                for (int i = 0; i < bytes; i += 4) {
                    double a = Math.Abs(BitConverter.ToSingle(buf, i));
                    if (a > PeakAbs) PeakAbs = a;
                }
                wav.Write(buf, bytes);
                cap.ReleaseBuffer(frames);
            }
            if (now >= nextTick) { tick((now - t0) / 1e7); nextTick += 10000000; }
            if (now >= until || stop()) {
                // the end too: nothing arriving means silence up to the end
                long end = (long)Math.Round((Math.Min(now, until) - t0) * (Rate / 1e7));
                if (end > wav.Frames + Rate / 50) { GapFrames += end - wav.Frames; wav.Silence(end - wav.Frames); }
                break;
            }
        }
        client.Stop();
        Frames = wav.Frames;
        return t0;
    }
}

// A paired device's Bluetooth services on Windows (Devices and Printers > the device > Services): which profiles
// Windows uses it for. The rig turns off the Q2's Audio Sink (0x110B) so Windows never makes the Q2 its speakers.
static class Services {
    [DllImport("bthprops.cpl", SetLastError = true)] static extern IntPtr BluetoothFindFirstRadio(ref int prm, out IntPtr radio);
    [DllImport("bthprops.cpl")] static extern bool BluetoothFindRadioClose(IntPtr find);
    [DllImport("bthprops.cpl")] static extern int BluetoothGetDeviceInfo(IntPtr radio, byte[] info);
    [DllImport("bthprops.cpl")] static extern int BluetoothSetServiceState(IntPtr radio, byte[] info, ref Guid service, int flags);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);

    // BLUETOOTH_DEVICE_INFO: dwSize, the address at 8, ..., 560 bytes in all
    public static int Set(ulong address, Guid service, bool on) {
        int prm = 4;
        IntPtr radio;
        var find = BluetoothFindFirstRadio(ref prm, out radio);
        if (find == IntPtr.Zero) return Marshal.GetLastWin32Error();
        BluetoothFindRadioClose(find);
        try {
            var info = new byte[560];
            BitConverter.GetBytes(560).CopyTo(info, 0);
            BitConverter.GetBytes(address).CopyTo(info, 8);
            int r = BluetoothGetDeviceInfo(radio, info);
            if (r != 0) return r;
            return BluetoothSetServiceState(radio, info, ref service, on ? 1 : 0);
        } finally { CloseHandle(radio); }
    }
}

static class Program {
    static string Arg(string[] a, string name, string dflt) {
        int i = Array.IndexOf(a, name);
        return i >= 0 && i + 1 < a.Length ? a[i + 1] : dflt;
    }
    static bool Flag(string[] a, string name) { return Array.IndexOf(a, name) >= 0; }

    static ulong ParseMac(string mac) { return Convert.ToUInt64(mac.Replace(":", "").Replace("-", ""), 16); }
    static string Mac(ulong a) {
        var s = a.ToString("X12");
        return string.Join(":", Enumerable.Range(0, 6).Select(i => s.Substring(2 * i, 2)));
    }

    [MTAThread]
    static int Main(string[] args) {
        try {
            if (args.Length == 0) { Console.Error.WriteLine("usage: btrig info|sessions|pair MAC|receive|selftest"); return 2; }
            switch (args[0]) {
                case "info": return Info().GetAwaiter().GetResult();
                case "sessions": return ListSessions();
                case "pair": return Pair(args[1], Arg(args, "--refuse-after", null)).GetAwaiter().GetResult();
                case "receive": return Receive(args).GetAwaiter().GetResult();
                case "selftest": return SelfTest(args);
            case "create": {  // TryCreateFromId alone, nothing started: does the API take this id
                try { var c = AudioPlaybackConnection.TryCreateFromId(args[1]); Json.Say("created", c != null); if (c != null) c.Dispose(); }
                catch (Exception e) { Json.Say("created", false, "type", e.GetType().Name, "hresult", e.HResult.ToString("X8"), "message", e.Message); }
                return 0;
            }
            case "defaults":
                foreach (var role in new[] { ERole.eConsole, ERole.eMultimedia, ERole.eCommunications }) {
                    IMMDevice d; string id = null;
                    if (Audio.Enumerator().GetDefaultAudioEndpoint(EDataFlow.eRender, role, out d) >= 0) d.GetId(out id);
                    Json.Say("role", role.ToString(), "id", id, "name", d == null ? null : Audio.Name(d));
                }
                return 0;
            case "set-default": {  // set-default ROLE ID: puts back a default Windows moved (docs/bt-rig.md#pairing-and-what-it-does-to-windows)
                var role = (ERole)Enum.Parse(typeof(ERole), args[1]);
                Audio.Check(((IPolicyConfig)new PolicyConfigClient()).SetDefaultEndpoint(args[2], role), "SetDefaultEndpoint");
                Json.Say("event", "default_set", "role", role.ToString(), "id", args[2]);
                return 0;
            }
            case "service": {  // service MAC UUID on|off: one of a paired device's services on Windows
                int r = Services.Set(ParseMac(args[1]), new Guid(args[2]), args[3] == "on");
                Json.Say("event", "service", "uuid", args[2], "on", args[3] == "on", "win32", r);
                return r == 0 ? 0 : 1;
            }
            case "link": {  // link MAC: the device's connection status as Windows sees it
                var dev = BluetoothDevice.FromBluetoothAddressAsync(ParseMac(args[1])).AsTask().GetAwaiter().GetResult();
                Json.Say("name", dev == null ? null : dev.Name, "status", dev == null ? null : dev.ConnectionStatus.ToString());
                return 0;
            }
            case "unpair": {  // unpair MAC: Windows forgets the device (only the rig's Q2 is ever named here)
                var dev = BluetoothDevice.FromBluetoothAddressAsync(ParseMac(args[1])).AsTask().GetAwaiter().GetResult();
                if (dev == null || !dev.DeviceInformation.Pairing.IsPaired) { Json.Say("event", "unpaired", "status", "NotPaired"); return 0; }
                var r = dev.DeviceInformation.Pairing.UnpairAsync().AsTask().GetAwaiter().GetResult();
                Json.Say("event", "unpaired", "status", r.Status.ToString());
                return r.Status == DeviceUnpairingResultStatus.Unpaired ? 0 : 1;
            }
            case "pipe": {  // pipe HOST PORT: stdin and stdout joined to a TCP connection (WSL reaching a Windows-side port)
                var tcp = new System.Net.Sockets.TcpClient();
                tcp.NoDelay = true;
                tcp.Connect(args[1], int.Parse(args[2]));
                var net = tcp.GetStream();
                var stdin = Console.OpenStandardInput();
                var stdout = Console.OpenStandardOutput();
                var up = Task.Run(() => { var b = new byte[65536]; int n; while ((n = stdin.Read(b, 0, b.Length)) > 0) net.Write(b, 0, n); tcp.Client.Shutdown(System.Net.Sockets.SocketShutdown.Send); });
                var down = Task.Run(() => { var b = new byte[65536]; int n; while ((n = net.Read(b, 0, b.Length)) > 0) { stdout.Write(b, 0, n); stdout.Flush(); } });
                Task.WaitAny(up, down);
                tcp.Close();
                return 0;
            }
            case "guard": {  // guard NAME SECONDS: while it runs, a role's default output named NAME goes back to the
                             // one that role had when the guard started (Windows makes a newly linked headset default)
                var pc = (IPolicyConfig)new PolicyConfigClient();
                var roles = new[] { ERole.eConsole, ERole.eMultimedia, ERole.eCommunications };
                var keep = new Dictionary<ERole, string>();
                foreach (var role in roles) {
                    IMMDevice d; string id;
                    if (Audio.Enumerator().GetDefaultAudioEndpoint(EDataFlow.eRender, role, out d) < 0) continue;
                    d.GetId(out id); keep[role] = id;
                    Json.Say("event", "guarding", "role", role.ToString(), "keep", Audio.Name(d), "id", id, "against", args[1]);
                }
                var end = DateTime.UtcNow.AddSeconds(double.Parse(args[2], System.Globalization.CultureInfo.InvariantCulture));
                var stop = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "guard.stop");
                while (DateTime.UtcNow < end && !File.Exists(stop)) {
                    foreach (var role in keep.Keys) {
                        IMMDevice d;
                        if (Audio.Enumerator().GetDefaultAudioEndpoint(EDataFlow.eRender, role, out d) < 0) continue;
                        var name = Audio.Name(d) ?? "";
                        if (name.IndexOf(args[1], StringComparison.OrdinalIgnoreCase) < 0) continue;
                        pc.SetDefaultEndpoint(keep[role], role);
                        Json.Say("event", "default_restored", "role", role.ToString(), "was", name, "utc", DateTime.UtcNow.ToString("o"));
                    }
                    Thread.Sleep(20);
                }
                Json.Say("event", "guard_end");
                return 0;
            }
            case "scan": {  // scan SECONDS MAC [UUID]: passive LE scan; each advertisement from MAC or naming UUID
                var want = ParseMac(args[2]);
                var svc = args.Length > 3 ? new Guid(args[3]) : Guid.Empty;
                var w = new Windows.Devices.Bluetooth.Advertisement.BluetoothLEAdvertisementWatcher();
                w.ScanningMode = Windows.Devices.Bluetooth.Advertisement.BluetoothLEScanningMode.Passive;
                int seen = 0, total = 0;
                w.Received += (o, e) => {
                    Interlocked.Increment(ref total);
                    bool ours = e.BluetoothAddress == want || (svc != Guid.Empty && e.Advertisement.ServiceUuids.Contains(svc));
                    if (!ours) return;
                    Interlocked.Increment(ref seen);
                    Json.Say("event", "advertisement", "utc", DateTime.UtcNow.ToString("o"), "address", Mac(e.BluetoothAddress),
                             "name", e.Advertisement.LocalName, "rssi", (int)e.RawSignalStrengthInDBm,
                             "services", new RawJson("[" + string.Join(", ", e.Advertisement.ServiceUuids.Select(g => Json.Q(g.ToString()))) + "]"));
                };
                Json.Say("event", "scanning", "utc", DateTime.UtcNow.ToString("o"));
                w.Start();
                Thread.Sleep((int)(double.Parse(args[1], System.Globalization.CultureInfo.InvariantCulture) * 1000));
                w.Stop();
                Json.Say("event", "scan_end", "utc", DateTime.UtcNow.ToString("o"), "matching", seen, "all_advertisements", total,
                         "status", w.Status.ToString());
                return 0;
            }
            case "routed": Json.Say("policy", Policy.Routed(int.Parse(args[1]))); return 0;
            }
            Console.Error.WriteLine("btrig: unknown command " + args[0]);
            return 2;
        } catch (Exception e) {
            Json.Say("event", "error", "type", e.GetType().Name, "message", e.Message,
                     "hresult", e is COMException ? ((COMException)e).ErrorCode.ToString("X8") : null,
                     "where", Environment.GetEnvironmentVariable("BTRIG_TRACE") != null ? e.StackTrace : null);
            return 1;
        }
    }

    static async Task<int> Info() {
        var ad = await BluetoothAdapter.GetDefaultAsync();
        if (ad != null)
            Json.Say("adapter", Mac(ad.BluetoothAddress), "classic", ad.IsClassicSupported, "le", ad.IsLowEnergySupported);
        var def = Audio.DefaultRender();
        string defId; def.GetId(out defId);
        foreach (var d in Audio.RenderEndpoints()) {
            string id; d.GetId(out id);
            float vol = -1; int mute = -1;
            try {
                var ev = Audio.Activate<IAudioEndpointVolume>(d, Audio.IID_IAudioEndpointVolume);
                ev.GetMasterVolumeLevelScalar(out vol); ev.GetMute(out mute);
            } catch (COMException) { }
            Json.Say("endpoint", Audio.Name(d), "id", id, "default", id == defId, "volume", vol, "muted", mute == 1);
        }
        var sel = AudioPlaybackConnection.GetDeviceSelector();
        foreach (var di in await DeviceInformation.FindAllAsync(sel))
            Json.Say("a2dp_source", di.Name, "id", di.Id, "paired", di.Pairing.IsPaired, "enabled", di.IsEnabled);
        if (Environment.GetEnvironmentVariable("BTRIG_TRACE") != null) {
            Json.Say("selector", sel);
            foreach (var di in await DeviceInformation.FindAllAsync("System.Devices.InterfaceClassGuid:=\"{6994AD04-93EF-11D0-A3CC-00A0C9223196}\""))
                if (di.Id.IndexOf("110a", StringComparison.OrdinalIgnoreCase) >= 0)
                    Json.Say("ks_interface", di.Name, "id", di.Id, "enabled", di.IsEnabled);
        }
        return 0;
    }

    static int ListSessions() {
        foreach (var s in Audio.Sessions()) {
            string proc = null;
            try { proc = s.Item2 == 0 ? "system" : Process.GetProcessById(s.Item2).ProcessName; } catch (ArgumentException) { }
            Json.Say("endpoint", s.Item1, "pid", s.Item2, "process", proc, "name", s.Item3, "state", s.Item4,
                     "volume", s.Item5, "muted", s.Item6);
        }
        return 0;
    }

    // refuseAfter: hold the confirmation that many seconds, then refuse it (the laptop cancelling the pairing).
    static async Task<int> Pair(string mac, string refuseAfter) {
        var dev = await BluetoothDevice.FromBluetoothAddressAsync(ParseMac(mac));
        if (dev == null) { Json.Say("event", "error", "message", "no such device"); return 1; }
        var di = dev.DeviceInformation;
        Json.Say("event", "device", "name", dev.Name, "id", di.Id, "paired", di.Pairing.IsPaired);
        if (di.Pairing.IsPaired) return 0;
        var custom = di.Pairing.Custom;
        custom.PairingRequested += (s, a) => {
            Json.Say("event", "pairing_requested", "kind", a.PairingKind.ToString(), "pin", a.Pin, "utc", DateTime.UtcNow.ToString("o"));
            if (refuseAfter != null) {  // not accepted: Windows answers no once the deferral completes
                var d = a.GetDeferral();
                Thread.Sleep((int)(double.Parse(refuseAfter, System.Globalization.CultureInfo.InvariantCulture) * 1000));
                Json.Say("event", "refused", "utc", DateTime.UtcNow.ToString("o"));
                d.Complete();
                return;
            }
            if (a.PairingKind == DevicePairingKinds.ProvidePin) a.Accept("0000"); else a.Accept();
        };
        var r = await custom.PairAsync(DevicePairingKinds.ConfirmOnly | DevicePairingKinds.ConfirmPinMatch |
                                       DevicePairingKinds.DisplayPin | DevicePairingKinds.ProvidePin,
                                       DevicePairingProtectionLevel.Default);
        Json.Say("event", "paired", "status", r.Status.ToString(), "level", r.ProtectionLevelUsed.ToString());
        return r.Status == DevicePairingResultStatus.Paired || r.Status == DevicePairingResultStatus.AlreadyPaired ? 0 : 1;
    }

    // The device's A2DP source as Windows' sink sees it: the selector's entry, else (an interface Windows has not
    // enabled yet, as for a source paired moments ago) the same KS interface found by its class alone.
    static async Task<DeviceInformation> FindSource(string mac) {
        var want = mac.Replace(":", "").ToLowerInvariant();
        Func<DeviceInformation, bool> mine = di => di.Id.ToLowerInvariant().Contains(want) && di.Id.ToLowerInvariant().Contains("110a")
                                                   && di.Id.EndsWith("\\SNK", StringComparison.OrdinalIgnoreCase);
        foreach (var di in await DeviceInformation.FindAllAsync(AudioPlaybackConnection.GetDeviceSelector()))
            if (mine(di)) return di;
        foreach (var di in await DeviceInformation.FindAllAsync("System.Devices.InterfaceClassGuid:=\"{6994AD04-93EF-11D0-A3CC-00A0C9223196}\""))
            if (mine(di)) { Json.Say("event", "source_interface_disabled", "id", di.Id); return di; }
        return null;
    }

    static volatile bool stopping;

    static async Task<int> Receive(string[] a) {
        string mac = Arg(a, "--device", null), outPath = Arg(a, "--out", null);
        double seconds = double.Parse(Arg(a, "--seconds", "30"), System.Globalization.CultureInfo.InvariantCulture);
        bool endpoint = Flag(a, "--endpoint"), noOpen = Flag(a, "--no-open");
        string stopFile = Arg(a, "--stop-file", null), route = Arg(a, "--route", null);
        int pid = Process.GetCurrentProcess().Id;
        Console.CancelKeyPress += (s, e) => { e.Cancel = true; stopping = true; };

        var src = await FindSource(mac);
        if (src == null) { Json.Say("event", "error", "message", "no paired A2DP source with address " + mac); return 1; }

        // This process's output on the endpoint named, before the stream exists, so that a received stream played
        // in this process plays there and not on the default output (docs/bt-rig.md#silence: assumed, not yet seen).
        string routeId = null;
        IAudioClient hold = null;
        if (route != null) {
            var dev = Audio.RenderEndpoints().FirstOrDefault(d => (Audio.Name(d) ?? "").Contains(route));
            if (dev == null) { Json.Say("event", "error", "message", "no active render endpoint named " + route); return 1; }
            dev.GetId(out routeId);
            hold = Policy.Route(pid, dev);
            Audio.Unity(pid);
            Json.Say("event", "routed", "endpoint", Audio.Name(dev), "id", routeId, "policy", Policy.Routed(pid));
        }
        try {
            var conn = AudioPlaybackConnection.TryCreateFromId(src.Id);
            if (conn == null) { Json.Say("event", "error", "message", "TryCreateFromId gave nothing"); return 1; }
            conn.StateChanged += (s, e) => Json.Say("event", "state", "state", s.State.ToString(), "utc", DateTime.UtcNow.ToString("o"));
            await conn.StartAsync();
            Json.Say("event", "started", "device", src.Name, "id", src.Id, "utc", DateTime.UtcNow.ToString("o"));
            if (!noOpen) {
                var r = await conn.OpenAsync();
                Json.Say("event", "open", "status", r.Status.ToString(), "error", r.ExtendedError == null ? null : r.ExtendedError.Message,
                         "utc", DateTime.UtcNow.ToString("o"));
                if (r.Status != AudioPlaybackConnectionOpenResultStatus.Success) { conn.Dispose(); return 1; }
            }

            IAudioClient client = endpoint ? Audio.Activate<IAudioClient>(Audio.DefaultRender(), Audio.IID_IAudioClient)
                                           : Audio.ProcessLoopback(pid);
            var capture = new Capture(client, 48000, 2, false);
            long until;
            { long q, f; Audio.QueryPerformanceCounter(out q); Audio.QueryPerformanceFrequency(out f); until = (long)(q * (1e7 / f)) + (long)(seconds * 1e7); }
            string startUtc = DateTime.UtcNow.ToString("o");
            Json.Say("event", "recording", "utc", startUtc, "mode", endpoint ? "endpoint-loopback" : "process-loopback", "rate", 48000);
            // Where this process's audio plays, every second: a stream of it on any endpoint but the route's stops
            // the recording at once (it would be heard).
            string wrong = null;
            Func<bool> misrouted = () => {
                foreach (var s in Audio.Sessions()) {
                    if (s.Item2 != pid || s.Item4 != 1) continue;
                    string id; s.Item7.GetSessionIdentifier(out id);
                    if (routeId == null || (id ?? "").IndexOf(routeId, StringComparison.OrdinalIgnoreCase) < 0) { wrong = s.Item1; return true; }
                }
                return false;
            };
            using (var wav = new Wav(outPath, 48000, 2)) {
                capture.Run(wav, until, () => stopping || wrong != null || (stopFile != null && File.Exists(stopFile)),
                            t => {
                                Json.Say("event", "tick", "seconds", t, "frames", wav.Frames, "peak", capture.PeakAbs);
                                Audio.Unity(pid);
                                if (route != null && misrouted()) Json.Say("event", "error", "message", "this process plays on " + wrong + ": stopped");
                            });
            }
            conn.Dispose();
            GC.KeepAlive(hold);
            Json.Say("event", "done", "start_utc", startUtc, "first_packet_utc", capture.FirstQpcUtc, "frames", capture.Frames,
                     "rate", 48000, "gap_frames", capture.GapFrames, "packets", capture.Packets,
                     "silent_packets", capture.SilentPackets, "discontinuities", capture.Discontinuities, "peak", capture.PeakAbs,
                     "mode", endpoint ? "endpoint-loopback" : "process-loopback", "route", route, "misrouted", wrong);
            return wrong == null ? 0 : 1;
        } finally {
            if (routeId != null) { Policy.Route(pid, (string)null); Json.Say("event", "unrouted", "policy", Policy.Routed(pid)); }
        }
    }

    // A 1 kHz tone at -6 dBFS that follows the default output, as the received stream does, with this process routed
    // to --route's endpoint: recorded as receive records it. Checks the route before the tone starts (the stream's
    // session must be on that endpoint, else nothing plays) and that the capture hears it.
    static int SelfTest(string[] a) {
        string outPath = Arg(a, "--out", null), route = Arg(a, "--route", null);
        double seconds = double.Parse(Arg(a, "--seconds", "2"), System.Globalization.CultureInfo.InvariantCulture);
        int pid = Process.GetCurrentProcess().Id;
        var dev = Audio.RenderEndpoints().FirstOrDefault(d => (Audio.Name(d) ?? "").Contains(route ?? "\0"));
        if (dev == null) { Json.Say("event", "error", "message", "no active render endpoint named " + route); return 1; }
        string routeId; dev.GetId(out routeId);
        var hold = Policy.Route(pid, dev);
        Json.Say("event", "routed", "policy", Policy.Routed(pid));
        try {
            var h = new Activated();
            ActivateDefault(h);
            if (!h.Done.WaitOne(5000)) throw new TimeoutException("default render activation");
            Audio.Check(h.Hr, "default render activation");
            var render = (IAudioClient)h.Iface;
            Audio.Check(render.Initialize(0, Audio.AUTOCONVERTPCM | Audio.SRC_DEFAULT_QUALITY, 2000000, 0, Audio.FloatFormat(48000, 2), IntPtr.Zero), "render Initialize");
            Audio.Unity(pid);
            var mine = Audio.Sessions().Where(x => x.Item2 == pid).Select(x => x.Item1).ToList();
            Json.Say("event", "sessions", "endpoints", new RawJson("[" + string.Join(", ", mine.Select(Json.Q)) + "]"));
            if (mine.Count == 0 || mine.Any(n => n != Audio.Name(dev))) { Json.Say("event", "error", "message", "not routed: nothing played"); return 1; }
            var rc = (IAudioRenderClient)Audio.Service(render, Audio.IID_IAudioRenderClient, "render");
            int size; render.GetBufferSize(out size);
            long phase = 0, written = 0; int renderErr = 0;
            Action fill = () => {
                int pad; render.GetCurrentPadding(out pad);
                int n = size - pad; if (n <= 0) return;
                IntPtr p; int hr = rc.GetBuffer(n, out p); if (hr < 0) { renderErr = hr; return; }
                written += n;
                var f = new float[n * 2];
                for (int i = 0; i < n; i++, phase++) f[2 * i] = f[2 * i + 1] = (float)(0.5 * Math.Sin(2 * Math.PI * 1000 * phase / 48000.0));
                Marshal.Copy(f, 0, p, f.Length);
                rc.ReleaseBuffer(n, 0);
            };
            fill();
            var capture = new Capture(Audio.ProcessLoopback(pid), 48000, 2, false);
            render.Start();
            var feeder = new Timer(_ => { try { fill(); } catch (Exception) { } }, null, 0, 20);
            long until; { long q, f; Audio.QueryPerformanceCounter(out q); Audio.QueryPerformanceFrequency(out f); until = (long)(q * (1e7 / f)) + (long)(seconds * 1e7); }
            var playing = new List<string>();
            using (var wav = new Wav(outPath, 48000, 2)) capture.Run(wav, until, () => false, t => {
                foreach (var x in Audio.Sessions()) if (x.Item2 == pid) playing.Add(x.Item1 + " state " + x.Item4);
            });
            Json.Say("event", "sessions_playing", "list", new RawJson("[" + string.Join(", ", playing.Select(Json.Q)) + "]"));
            feeder.Dispose();
            render.Stop();
            GC.KeepAlive(hold);
            Json.Say("event", "selftest", "route", Audio.Name(dev), "peak", capture.PeakAbs, "frames", capture.Frames,
                     "rendered", written, "render_error", renderErr.ToString("X8"),
                     "packets", capture.Packets, "gap_frames", capture.GapFrames);
            return 0;
        } finally {
            Policy.Route(pid, (string)null);
            Json.Say("event", "unrouted", "policy", Policy.Routed(pid));
        }
    }

    [DllImport("Mmdevapi.dll", ExactSpelling = true, PreserveSig = false)]
    static extern IActivateAudioInterfaceAsyncOperation ActivateAudioInterfaceAsync(
        [MarshalAs(UnmanagedType.LPWStr)] string path, [MarshalAs(UnmanagedType.LPStruct)] Guid riid, IntPtr activationParams,
        IActivateAudioInterfaceCompletionHandler handler);

    // The default render device, followed as it changes: DEVINTERFACE_AUDIO_RENDER's string as the path.
    static void ActivateDefault(Activated h) {
        ActivateAudioInterfaceAsync("{E6327CAD-DCEC-4949-AE8A-991E976A79D2}", Audio.IID_IAudioClient, IntPtr.Zero, h);
    }
}
