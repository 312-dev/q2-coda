// btrelay.exe: the laptop's half of the Bluetooth relay (docs/bt-relay.md#on-the-laptop). Windows is the A2DP
// sink the Q2 plays to, through Windows.Media.Audio.AudioPlaybackConnection, and this program opens that sink
// for one device, records what Windows then plays (WASAPI loopback of the default output) to a WAV, and
// prints the media sessions Windows shows, which is where a source's AVRCP state would surface.
//
//   btrelay probe                       the adapter, the devices that can play to this computer, the output's
//                                       format, the media sessions; changes nothing
//   btrelay pair ADDR [--seconds N]     find the Q2 (it must be discoverable) and pair with it, accepting the
//                                       confirmation here; the address is noted in paired.txt beside the exe
//   btrelay unpair ADDR                 remove a pairing this program made (only those in paired.txt)
//   btrelay sink ADDR [--seconds N] [--wav FILE] [--send KEY@S,...]
//                                       open the sink for N seconds (30), record to FILE, and at S seconds
//                                       send KEY (play, pause, next, previous) to the source's media session
//   btrelay capture [--seconds N] --wav FILE
//                                       the loopback recording alone
//
// Built by build.ps1 with the C# 5 compiler that ships in Windows (.NET Framework 4), against the system's
// own WinRT metadata: nothing to install. That compiler has no reference for WinRT's Task adapters, so async
// operations are polled (Wait), and the language is kept to C# 5.
using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using Windows.Devices.Bluetooth;
using Windows.Devices.Enumeration;
using Windows.Foundation;
using Windows.Media.Audio;
using Windows.Media.Control;

[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
class MMDeviceEnumerator { }

[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator {
    void EnumAudioEndpoints(int dataFlow, int stateMask, out IntPtr devices);
    void GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice device);
}

[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice {
    void Activate(ref Guid iid, int clsCtx, IntPtr activationParams, [MarshalAs(UnmanagedType.IUnknown)] out object instance);
    void OpenPropertyStore(int access, out IntPtr properties);
    void GetId([MarshalAs(UnmanagedType.LPWStr)] out string id);
}

[Guid("1CB9AD4C-DBFA-4c32-B178-C2F568A703B2"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioClient {
    void Initialize(int shareMode, int streamFlags, long bufferDuration, long periodicity, IntPtr format, IntPtr session);
    void GetBufferSize(out uint frames);
    void GetStreamLatency(out long latency);
    void GetCurrentPadding(out uint frames);
    void IsFormatSupported(int shareMode, IntPtr format, out IntPtr closest);
    void GetMixFormat(out IntPtr format);
    void GetDevicePeriod(out long defaultPeriod, out long minimumPeriod);
    void Start();
    void Stop();
    void Reset();
    void SetEventHandle(IntPtr handle);
    void GetService(ref Guid iid, [MarshalAs(UnmanagedType.IUnknown)] out object service);
}

[Guid("C8ADBD64-E71E-48a0-A4DE-185C395CD317"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioCaptureClient {
    void GetBuffer(out IntPtr data, out uint frames, out uint flags, out ulong devicePosition, out ulong qpcPosition);
    void ReleaseBuffer(uint frames);
    void GetNextPacketSize(out uint frames);
}

// What Windows is playing on the default output, to a WAV in the output's own mix format (as a rule 32-bit
// float at 48 kHz). Loopback delivers nothing while nothing plays, so a stretch with no packets (before the
// first, between two, after the last) is written as silence from the packets' clock, and the file is as long
// as the recording ran.
class Loopback {
    const int RENDER = 0, CONSOLE = 0, CLSCTX_ALL = 23, SHARED = 0, LOOPBACK = 0x00020000, SILENT = 2;
    public int Rate, Channels, Bits, Gaps;
    public long Frames;
    public string Problem;
    volatile bool stop;
    Thread thread;

    public void Start(string path) {
        thread = new Thread(delegate() {
            try { Run(path); } catch (Exception e) { Problem = e.Message; }
        });
        thread.Start();
    }

    public void Stop() {
        stop = true;
        thread.Join();
    }

    // The performance counter in 100 ns units: the clock a packet's position is stamped with.
    static long Ticks() {
        return (long)(System.Diagnostics.Stopwatch.GetTimestamp() * (10000000.0 / System.Diagnostics.Stopwatch.Frequency));
    }

    void Run(string path) {
        IMMDevice device;
        ((IMMDeviceEnumerator)new MMDeviceEnumerator()).GetDefaultAudioEndpoint(RENDER, CONSOLE, out device);
        Guid iid = typeof(IAudioClient).GUID;
        object o;
        device.Activate(ref iid, CLSCTX_ALL, IntPtr.Zero, out o);
        IAudioClient client = (IAudioClient)o;
        IntPtr format;
        client.GetMixFormat(out format);
        Channels = Marshal.ReadInt16(format, 2);
        Rate = Marshal.ReadInt32(format, 4);
        int align = Marshal.ReadInt16(format, 12);
        Bits = Marshal.ReadInt16(format, 14);
        byte[] fmt = new byte[18 + Marshal.ReadInt16(format, 16)];
        Marshal.Copy(format, fmt, 0, fmt.Length);
        client.Initialize(SHARED, LOOPBACK, 10000000, 0, format, IntPtr.Zero);  // a buffer of 1 s, in 100 ns units
        iid = typeof(IAudioCaptureClient).GUID;
        client.GetService(ref iid, out o);
        IAudioCaptureClient capture = (IAudioCaptureClient)o;
        using (FileStream file = new FileStream(path, FileMode.Create))
        using (BinaryWriter w = new BinaryWriter(file)) {
            w.Write(new char[] { 'R', 'I', 'F', 'F' }); w.Write(0); w.Write(new char[] { 'W', 'A', 'V', 'E' });
            w.Write(new char[] { 'f', 'm', 't', ' ' }); w.Write(fmt.Length); w.Write(fmt);
            if ((fmt.Length & 1) != 0) w.Write((byte)0);
            w.Write(new char[] { 'd', 'a', 't', 'a' });
            long sizeAt = file.Position;
            w.Write(0);
            client.Start();
            byte[] block = new byte[0];
            long first = Ticks();
            while (!stop) {
                Thread.Sleep(10);
                uint next;
                capture.GetNextPacketSize(out next);
                while (next != 0) {
                    IntPtr data; uint frames, flags; ulong position, qpc;
                    capture.GetBuffer(out data, out frames, out flags, out position, out qpc);
                    long due = ((long)qpc - first) * Rate / 10000000;
                    if (due - Frames > Rate / 50) {  // more than 20 ms with no packet: nothing was playing
                        w.Write(new byte[(due - Frames) * align]);
                        Frames = due;
                        Gaps++;
                    }
                    int size = (int)frames * align;
                    if (block.Length < size) block = new byte[size];
                    if ((flags & SILENT) != 0) Array.Clear(block, 0, size); else Marshal.Copy(data, block, 0, size);
                    w.Write(block, 0, size);
                    Frames += frames;
                    capture.ReleaseBuffer(frames);
                    capture.GetNextPacketSize(out next);
                }
            }
            client.Stop();
            long whole = (Ticks() - first) * Rate / 10000000;
            if (whole - Frames > Rate / 50) {  // quiet to the end
                w.Write(new byte[(whole - Frames) * align]);
                Frames = whole;
                Gaps++;
            }
            long bytes = Frames * align;
            file.Position = sizeAt; w.Write((uint)bytes);
            file.Position = 4; w.Write((uint)(file.Length - 8));
        }
        Marshal.FreeCoTaskMem(format);
    }
}

static class BtRelay {
    static readonly DateTime began = DateTime.UtcNow;
    static readonly string home = Path.GetDirectoryName(System.Reflection.Assembly.GetExecutingAssembly().Location);
    static string Ledger { get { return Path.Combine(home, "paired.txt"); } }

    static void Say(string text) {
        Console.WriteLine("{0,7:0.00} {1}", (DateTime.UtcNow - began).TotalSeconds, text);
    }

    static T Wait<T>(IAsyncOperation<T> op, int seconds) {
        DateTime end = DateTime.UtcNow.AddSeconds(seconds);
        while (op.Status == AsyncStatus.Started) {
            if (DateTime.UtcNow > end) { op.Cancel(); throw new TimeoutException("no answer from Windows in " + seconds + " s"); }
            Thread.Sleep(20);
        }
        return op.GetResults();
    }

    static void Wait(IAsyncAction op, int seconds) {
        DateTime end = DateTime.UtcNow.AddSeconds(seconds);
        while (op.Status == AsyncStatus.Started) {
            if (DateTime.UtcNow > end) { op.Cancel(); throw new TimeoutException("no answer from Windows in " + seconds + " s"); }
            Thread.Sleep(20);
        }
        op.GetResults();
    }

    // "AA:BB:CC:DD:EE:FF" as twelve upper-case hex digits; anything else is refused.
    static string Hex(string address) {
        string hex = address.Replace(":", "").Replace("-", "").ToUpperInvariant();
        ulong value;
        if (hex.Length != 12 || !ulong.TryParse(hex, System.Globalization.NumberStyles.HexNumber, null, out value))
            throw new ArgumentException("not a Bluetooth address (AA:BB:CC:DD:EE:FF): " + address);
        return hex;
    }

    static string Colons(string hex) {
        List<string> parts = new List<string>();
        for (int i = 0; i < 12; i += 2) parts.Add(hex.Substring(i, 2));
        return string.Join(":", parts.ToArray());
    }

    static string Option(List<string> args, string name, string fallback) {
        int i = args.IndexOf(name);
        if (i < 0) return fallback;
        if (i + 1 >= args.Count) throw new ArgumentException(name + " needs a value");
        string value = args[i + 1];
        args.RemoveRange(i, 2);
        return value;
    }

    static string OutPath(string name) {
        return Path.IsPathRooted(name) ? name : Path.Combine(home, name);
    }

    // The interface Windows made for a paired device's A2DP source role, by the address in its id.
    static DeviceInformation Source(string hex) {
        foreach (DeviceInformation d in Wait(DeviceInformation.FindAllAsync(AudioPlaybackConnection.GetDeviceSelector()), 20))
            if (d.Id.ToUpperInvariant().Contains(hex)) return d;
        return null;
    }

    static List<string> Sessions(GlobalSystemMediaTransportControlsSessionManager manager) {
        List<string> lines = new List<string>();
        foreach (GlobalSystemMediaTransportControlsSession s in manager.GetSessions()) {
            string line = s.SourceAppUserModelId + ": " + s.GetPlaybackInfo().PlaybackStatus;
            try {
                GlobalSystemMediaTransportControlsSessionMediaProperties p = Wait(s.TryGetMediaPropertiesAsync(), 5);
                if (p != null && (p.Title + p.Artist).Length > 0) line += ", \"" + p.Title + "\" by \"" + p.Artist + "\"";
            } catch (Exception) { }
            lines.Add(line);
        }
        return lines;
    }

    static int Probe() {
        BluetoothAdapter adapter = Wait(BluetoothAdapter.GetDefaultAsync(), 20);
        if (adapter == null) { Console.WriteLine("no Bluetooth adapter"); return 1; }
        Console.WriteLine("adapter " + Colons(adapter.BluetoothAddress.ToString("X12")) + ", classic " + adapter.IsClassicSupported
            + ", low energy " + adapter.IsLowEnergySupported);
        DeviceInformationCollection sources = Wait(DeviceInformation.FindAllAsync(AudioPlaybackConnection.GetDeviceSelector()), 20);
        Console.WriteLine("A2DP sink: " + sources.Count + " paired device(s) can play to this computer");
        foreach (DeviceInformation d in sources) Console.WriteLine("  source " + d.Name);
        Loopback probe = new Loopback();
        string scratch = Path.Combine(Path.GetTempPath(), "btrelay-probe.wav");
        probe.Start(scratch);
        Thread.Sleep(300);
        probe.Stop();
        Console.WriteLine(probe.Problem != null ? "loopback: " + probe.Problem
            : "loopback: default output is " + probe.Rate + " Hz, " + probe.Channels + " channels, " + probe.Bits + " bits");
        foreach (string line in Sessions(Wait(GlobalSystemMediaTransportControlsSessionManager.RequestAsync(), 20)))
            Console.WriteLine("media session " + line);
        if (File.Exists(Ledger)) Console.WriteLine("paired by btrelay: " + File.ReadAllText(Ledger).Trim().Replace("\r\n", " "));
        return 0;
    }

    static int Pair(string hex, int seconds) {
        string want = Colons(hex).ToLowerInvariant();
        DeviceInformation found = null;
        // An unpaired device is an "association endpoint"; watching for those runs an inquiry.
        DeviceWatcher watcher = DeviceInformation.CreateWatcher(BluetoothDevice.GetDeviceSelectorFromPairingState(false), null,
            DeviceInformationKind.AssociationEndpoint);
        watcher.Added += delegate(DeviceWatcher w, DeviceInformation d) {
            if (d.Id.ToLowerInvariant().EndsWith(want)) found = d;
        };
        watcher.Start();
        Say("looking for " + Colons(hex) + " (is the Q2 discoverable?)");
        DateTime end = DateTime.UtcNow.AddSeconds(seconds);
        while (found == null && DateTime.UtcNow < end) Thread.Sleep(100);
        watcher.Stop();
        if (found == null) { Say("not found in " + seconds + " s: nothing was changed"); return 1; }
        Say("found \"" + found.Name + "\", pairing");
        found.Pairing.Custom.PairingRequested += delegate(DeviceInformationCustomPairing p, DevicePairingRequestedEventArgs e) {
            Say("pairing asks " + e.PairingKind + (string.IsNullOrEmpty(e.Pin) ? "" : ", code " + e.Pin) + ": accepted");
            if (e.PairingKind == DevicePairingKinds.ProvidePin) e.Accept("0000"); else e.Accept();
        };
        DevicePairingResult result = Wait(found.Pairing.Custom.PairAsync(DevicePairingKinds.ConfirmOnly | DevicePairingKinds.ConfirmPinMatch
            | DevicePairingKinds.DisplayPin | DevicePairingKinds.ProvidePin), 60);
        Say("pairing: " + result.Status);
        if (result.Status != DevicePairingResultStatus.Paired) return 1;
        File.AppendAllText(Ledger, hex + "\r\n");
        Say("noted in " + Ledger + "; undo with: btrelay unpair " + Colons(hex));
        return 0;
    }

    static int Unpair(string hex) {
        if (!File.Exists(Ledger) || Array.IndexOf(File.ReadAllLines(Ledger), hex) < 0) {
            Say(Colons(hex) + " was not paired by btrelay (no line in " + Ledger + "): left alone");
            return 1;
        }
        BluetoothDevice device = Wait(BluetoothDevice.FromBluetoothAddressAsync(Convert.ToUInt64(hex, 16)), 20);
        if (device == null) { Say("Windows does not know " + Colons(hex)); return 1; }
        DeviceUnpairingResult result = Wait(device.DeviceInformation.Pairing.UnpairAsync(), 30);
        Say("unpairing: " + result.Status);
        if (result.Status != DeviceUnpairingResultStatus.Unpaired && result.Status != DeviceUnpairingResultStatus.AlreadyUnpaired) return 1;
        List<string> rest = new List<string>(File.ReadAllLines(Ledger));
        rest.RemoveAll(delegate(string line) { return line == hex; });
        File.WriteAllLines(Ledger, rest.ToArray());
        return 0;
    }

    static void Send(GlobalSystemMediaTransportControlsSession session, string key) {
        bool done;
        if (key == "play") done = Wait(session.TryPlayAsync(), 10);
        else if (key == "pause") done = Wait(session.TryPauseAsync(), 10);
        else if (key == "next") done = Wait(session.TrySkipNextAsync(), 10);
        else if (key == "previous") done = Wait(session.TrySkipPreviousAsync(), 10);
        else throw new ArgumentException("--send takes play, pause, next or previous, not " + key);
        Say("sent " + key + " to " + session.SourceAppUserModelId + ": " + (done ? "taken" : "refused"));
    }

    static int Sink(string hex, int seconds, string wav, string send) {
        List<KeyValuePair<double, string>> sends = new List<KeyValuePair<double, string>>();
        foreach (string item in send.Split(new char[] { ',' }, StringSplitOptions.RemoveEmptyEntries)) {
            string[] parts = item.Split('@');
            if (parts.Length != 2) throw new ArgumentException("--send is KEY@SECONDS,...: " + item);
            sends.Add(new KeyValuePair<double, string>(double.Parse(parts[1], System.Globalization.CultureInfo.InvariantCulture), parts[0]));
        }
        DeviceInformation source = Source(hex);
        if (source == null) {
            Say(Colons(hex) + " is not a paired device that can play to this computer (btrelay probe lists them; btrelay pair ADDR)");
            return 1;
        }
        GlobalSystemMediaTransportControlsSessionManager manager = Wait(GlobalSystemMediaTransportControlsSessionManager.RequestAsync(), 20);
        List<string> before = new List<string>();
        foreach (GlobalSystemMediaTransportControlsSession s in manager.GetSessions()) before.Add(s.SourceAppUserModelId);
        Loopback loop = null;
        if (wav != null) {
            loop = new Loopback();
            loop.Start(OutPath(wav));
        }
        int status = 0;
        AudioPlaybackConnection connection = AudioPlaybackConnection.TryCreateFromId(source.Id);
        try {
            connection.StateChanged += delegate(AudioPlaybackConnection c, object o) { Say("sink " + c.State); };
            Wait(connection.StartAsync(), 30);
            Say("sink enabled for \"" + source.Name + "\", connecting");
            AudioPlaybackConnectionOpenResult opened = Wait(connection.OpenAsync(), 60);
            Say("sink open: " + opened.Status + (opened.ExtendedError != null ? " (" + opened.ExtendedError.Message.Trim() + ")" : ""));
            if (opened.Status != AudioPlaybackConnectionOpenResultStatus.Success) status = 1;
            List<string> shown = new List<string>();
            DateTime start = DateTime.UtcNow;
            while (status == 0 && (DateTime.UtcNow - start).TotalSeconds < seconds) {
                Thread.Sleep(250);
                double at = (DateTime.UtcNow - start).TotalSeconds;
                GlobalSystemMediaTransportControlsSession theirs = null;
                foreach (GlobalSystemMediaTransportControlsSession s in manager.GetSessions())
                    if (!before.Contains(s.SourceAppUserModelId)) theirs = s;
                List<string> now = Sessions(manager);
                foreach (string line in now) if (!shown.Contains(line)) Say("media session " + line);
                shown = now;
                for (int i = sends.Count - 1; i >= 0; i--) {
                    if (sends[i].Key > at) continue;
                    // Only a session that appeared with the sink is the Q2's. A media key would reach whatever
                    // the owner is playing, so without such a session nothing is sent.
                    if (theirs == null) Say("no media session came with the sink: " + sends[i].Value + " not sent");
                    else Send(theirs, sends[i].Value);
                    sends.RemoveAt(i);
                }
            }
        } finally {
            connection.Dispose();
            Say("sink closed");
            if (loop != null) {
                loop.Stop();
                if (loop.Problem != null) { Say("loopback failed: " + loop.Problem); status = 1; }
                else Say("wrote " + OutPath(wav) + ": " + loop.Frames + " frames at " + loop.Rate + " Hz, " + loop.Channels + " channels, "
                    + loop.Bits + " bits, " + loop.Gaps + " silent stretches");
            }
        }
        return status;
    }

    static int Capture(int seconds, string wav) {
        Loopback loop = new Loopback();
        loop.Start(OutPath(wav));
        Thread.Sleep(seconds * 1000);
        loop.Stop();
        if (loop.Problem != null) { Say("loopback failed: " + loop.Problem); return 1; }
        Say("wrote " + OutPath(wav) + ": " + loop.Frames + " frames at " + loop.Rate + " Hz, " + loop.Channels + " channels, " + loop.Bits
            + " bits, " + loop.Gaps + " silent stretches");
        return 0;
    }

    [MTAThread]
    static int Main(string[] argv) {
        List<string> args = new List<string>(argv);
        try {
            string verb = args.Count > 0 ? args[0] : "";
            if (args.Count > 0) args.RemoveAt(0);
            int seconds = int.Parse(Option(args, "--seconds", "30"));
            string wav = Option(args, "--wav", null);
            string send = Option(args, "--send", "");
            if (verb == "probe" && args.Count == 0) return Probe();
            if (verb == "pair" && args.Count == 1) return Pair(Hex(args[0]), seconds);
            if (verb == "unpair" && args.Count == 1) return Unpair(Hex(args[0]));
            if (verb == "sink" && args.Count == 1) return Sink(Hex(args[0]), seconds, wav, send);
            if (verb == "capture" && args.Count == 0 && wav != null) return Capture(seconds, wav);
            Console.Error.WriteLine("usage: btrelay probe | pair ADDR [--seconds N] | unpair ADDR\n"
                + "       btrelay sink ADDR [--seconds N] [--wav FILE] [--send KEY@S,...] | capture [--seconds N] --wav FILE");
            return 2;
        } catch (Exception e) {
            Console.Error.WriteLine("btrelay: " + e.Message);
            return 2;
        }
    }
}
