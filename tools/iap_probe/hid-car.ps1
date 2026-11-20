# Desk rehearsal of milestone M1 on Windows (docs/iphone-mode.md#iap2): a stand-in car, speaking iAP2 to the Q2's
# iPhone HID interface through Windows' own HID driver, so the Q2's real kernel, FunctionFS, writer and interrupt
# endpoint carry it. The detect, a SYN, the ACK, a 900-byte certificate in several output reports, the challenge's
# answer, an identification, then StartPowerUpdates, StartNowPlayingUpdates and StartUSBDeviceModeAudio, then the
# BMW's own StartHID and a Pause press (the probe's log names it: Pause, so a desk run skips no song), and a cover
# asked for, set up on the file transfer session and taken in pieces; each step's answer checked and printed. Needs the probe in a min:iap2 phase (Apple's driver leaves configuration 2's HID
# interface to Windows only then). Exit 0 when every step answered as an iPhone does.
#   powershell -NoProfile -ExecutionPolicy Bypass -File hid-car.ps1
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
public static class Car {
    [DllImport("hid.dll")] static extern void HidD_GetHidGuid(out Guid g);
    [DllImport("hid.dll", SetLastError = true)] static extern bool HidD_SetOutputReport(SafeFileHandle h, byte[] b, int n);
    [DllImport("setupapi.dll", SetLastError = true)] static extern IntPtr SetupDiGetClassDevs(ref Guid g, IntPtr e, IntPtr w, int f);
    [DllImport("setupapi.dll", SetLastError = true)] static extern bool SetupDiEnumDeviceInterfaces(IntPtr s, IntPtr d, ref Guid g, int i, ref SP_DID did);
    [DllImport("setupapi.dll", SetLastError = true, CharSet = CharSet.Auto)] static extern bool SetupDiGetDeviceInterfaceDetail(IntPtr s, ref SP_DID did, IntPtr detail, int size, out int req, IntPtr info);
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Auto)] static extern SafeFileHandle CreateFile(string p, uint a, uint s, IntPtr sa, uint c, uint f, IntPtr t);
    [StructLayout(LayoutKind.Sequential)] struct SP_DID { public int cbSize; public Guid g; public int flags; public IntPtr r; }
    static readonly int[] OutSizes = { 5, 9, 13, 17, 25, 49, 95, 193, 255 };            // ids 13..21
    static readonly int[] InSizes = { 5, 9, 13, 17, 25, 49, 95, 193, 257, 385, 513, 767 }; // ids 1..12
    static SafeFileHandle h; static FileStream fs; static byte seq = 0x2a, ack; static int fails;
    public static List<string> Out = new List<string>();
    static void Say(string s) { Out.Add(s); }
    static void Check(bool ok, string what) { Say((ok ? "ok    " : "FAIL  ") + what); if (!ok) fails++; }

    static string Find() {
        Guid g; HidD_GetHidGuid(out g);
        IntPtr set = SetupDiGetClassDevs(ref g, IntPtr.Zero, IntPtr.Zero, 0x12);
        for (int i = 0; ; i++) {
            SP_DID did = new SP_DID(); did.cbSize = Marshal.SizeOf(did);
            if (!SetupDiEnumDeviceInterfaces(set, IntPtr.Zero, ref g, i, ref did)) return null;
            int req; SetupDiGetDeviceInterfaceDetail(set, ref did, IntPtr.Zero, 0, out req, IntPtr.Zero);
            IntPtr buf = Marshal.AllocHGlobal(req);
            Marshal.WriteInt32(buf, IntPtr.Size == 8 ? 8 : 6);
            SetupDiGetDeviceInterfaceDetail(set, ref did, buf, req, out req, IntPtr.Zero);
            string path = Marshal.PtrToStringAuto(buf + 4); Marshal.FreeHGlobal(buf);
            if (path.ToLower().Contains("vid_05ac&pid_12a8") || path.ToLower().Contains("vid_1d6b&pid_0106")) return path; // Apple's, or :linuxid's
        }
    }
    static byte Ck(byte[] b, int off, int n) { int s = 0; for (int i = 0; i < n; i++) s += b[off + i]; return (byte)(-s); }

    // bytes as output reports: the smallest that holds what is left, link control 00/02/03/01
    static void Write(byte[] data) {
        int off = 0;
        do {
            int left = data.Length - off, k = 0;
            while (k < OutSizes.Length - 1 && OutSizes[k] < left + 1) k++;
            int take = Math.Min(OutSizes[k] - 1, left);
            byte[] r = new byte[1 + OutSizes[k]];
            r[0] = (byte)(13 + k);
            r[1] = (byte)((off > 0 ? 1 : 0) | (off + take < data.Length ? 2 : 0));
            Array.Copy(data, off, r, 2, take);
            if (!HidD_SetOutputReport(h, r, r.Length)) throw new Exception("SET_REPORT failed: " + Marshal.GetLastWin32Error());
            off += take;
        } while (off < data.Length);
    }
    static byte[] Report(int ms) {
        byte[] b = new byte[768];
        var t = fs.ReadAsync(b, 0, b.Length);
        if (!t.Wait(ms)) throw new Exception("no input report within " + ms + " ms");
        int id = b[0]; byte[] r = new byte[1 + InSizes[id - 1]]; Array.Copy(b, r, r.Length); return r;
    }
    static byte[] Packet(int ms) {
        var d = new List<byte>();
        while (true) { byte[] r = Report(ms); for (int i = 2; i < r.Length; i++) d.Add(r[i]); if ((r[1] & 2) == 0) break; }
        byte[] p = d.ToArray(); int n = p[2] << 8 | p[3];
        if (p[0] != 0xff || p[1] != 0x5a || Ck(p, 0, 8) != p[8]) throw new Exception("not a packet: " + BitConverter.ToString(p, 0, 12));
        if (n > 9 && Ck(p, 9, n - 10) != p[n - 1]) throw new Exception("bad payload checksum");
        byte[] q = new byte[n]; Array.Copy(p, q, n); return q;
    }
    static byte[] Pkt(byte ctl, byte s, byte a, byte session, byte[] pl) {
        int n = 9 + (pl.Length > 0 ? pl.Length + 1 : 0);
        byte[] p = new byte[n];
        p[0] = 0xff; p[1] = 0x5a; p[2] = (byte)(n >> 8); p[3] = (byte)n; p[4] = ctl; p[5] = s; p[6] = a; p[7] = session; p[8] = Ck(p, 0, 8);
        if (pl.Length > 0) { Array.Copy(pl, 0, p, 9, pl.Length); p[n - 1] = Ck(pl, 0, pl.Length); }
        return p;
    }
    static byte[] P(int id, byte[] d) { byte[] b = new byte[4 + d.Length]; b[0] = (byte)(b.Length >> 8); b[1] = (byte)b.Length; b[2] = (byte)(id >> 8); b[3] = (byte)id; Array.Copy(d, 0, b, 4, d.Length); return b; }
    static byte[] Cat(params byte[][] a) { var l = new List<byte>(); foreach (var x in a) l.AddRange(x); return l.ToArray(); }
    static byte[] Msg(int id, params byte[][] ps) { byte[] body = Cat(ps); byte[] m = new byte[6 + body.Length]; m[0] = 0x40; m[1] = 0x40; m[2] = (byte)(m.Length >> 8); m[3] = (byte)m.Length; m[4] = (byte)(id >> 8); m[5] = (byte)id; Array.Copy(body, 0, m, 6, body.Length); return m; }
    static void Send(byte[] m) { seq++; Write(Pkt(0x40, seq, ack, 10, m)); }
    static List<byte[]> files = new List<byte[]>();
    // the next control message; file transfer packets (session 11) are kept in files, bare ACKs passed over
    static int Recv(out byte[] m) {
        while (true) {
            byte[] p = Packet(3000);
            if (p.Length <= 9) continue;
            ack = p[5];
            m = new byte[p.Length - 10]; Array.Copy(p, 9, m, 0, m.Length);
            if (p[7] == 11) { files.Add(m); continue; }
            return m[4] << 8 | m[5];
        }
    }
    // the next file transfer packet, read here: Recv() keeps them aside and waits on for a control message
    static byte[] FilePacket() {
        while (files.Count == 0) {
            byte[] p = Packet(3000);
            if (p.Length <= 9) continue;
            ack = p[5];
            byte[] m = new byte[p.Length - 10]; Array.Copy(p, 9, m, 0, m.Length);
            if (p[7] == 11) files.Add(m);
            else Say("      (control " + (m[4] << 8 | m[5]).ToString("X4") + " while waiting for the cover)");
        }
        byte[] f = files[0]; files.RemoveAt(0); return f;
    }
    static byte[] Ids(params int[] ids) { byte[] b = new byte[ids.Length * 2]; for (int i = 0; i < ids.Length; i++) { b[2 * i] = (byte)(ids[i] >> 8); b[2 * i + 1] = (byte)ids[i]; } return b; }

    public static int Run() {
        string path = Find();
        if (path == null) { Say("no HID device 05ac:12a8 (is a min:iap2 phase running?)"); return 2; }
        h = CreateFile(path, 0xC0000000, 3, IntPtr.Zero, 3, 0, IntPtr.Zero);
        if (h.IsInvalid) { Say("open failed: " + Marshal.GetLastWin32Error()); return 2; }
        fs = new FileStream(h, FileAccess.Read, 768);
        byte[] m;
        Write(new byte[] { 0xff, 0x55, 0x02, 0x00, 0xee, 0x10 });
        byte[] e = Report(3000);
        Check(e[0] == 2 && e[2] == 0xff && e[3] == 0x55 && e[7] == 0x10, "detect echoed: " + BitConverter.ToString(e));
        byte[] syn = { 1, 5, 4, 0, 3, 0xe8, 0, 0, 0, 0, 10, 0, 1, 11, 1, 1 };
        Write(Pkt(0x80, seq, 0, 0, syn));
        byte[] sa = Packet(3000);
        bool same = sa.Length == 9 + syn.Length + 1; for (int i = 0; same && i < syn.Length; i++) same = sa[9 + i] == syn[i];
        Check(sa[4] == 0xc0 && sa[6] == seq && same, "SYN+ACK with the car's parameters");
        ack = sa[5];
        Write(Pkt(0x40, seq, ack, 0, new byte[0]));
        Check(Recv(out m) == 0xAA00, "RequestAuthenticationCertificate");
        byte[] cert = new byte[900]; for (int i = 0; i < cert.Length; i++) cert[i] = (byte)i;
        Send(Msg(0xAA01, P(0, cert)));
        Check(Recv(out m) == 0xAA02 && m.Length == 6 + 4 + 20, "a 20-byte challenge to a 900-byte certificate");
        Send(Msg(0xAA03, P(0, new byte[128])));
        Check(Recv(out m) == 0xAA05, "AuthenticationSucceeded");
        Check(Recv(out m) == 0x1D00, "StartIdentification");
        Send(Msg(0x1D01, P(0, System.Text.Encoding.ASCII.GetBytes("Desk car\0")),
                 P(6, Ids(0x5000, 0xAE00, 0xDA00, 0x6800)),
                 P(7, Ids(0x4E0C, 0x4E0A, 0x4E09, 0x5001, 0xAE01, 0xDA01)),
                 P(17, P(0, new byte[] { 0, 5 }))));
        int[] want = { 0x1D02, 0x4E0C, 0x4E0A, 0x4E09 };
        foreach (int w in want) Check(Recv(out m) == w, "after identification: " + w.ToString("X4"));
        Send(Msg(0xAE00, P(0, new byte[0]), P(6, new byte[0])));
        Check(Recv(out m) == 0xAE01, "PowerUpdate (current)"); Check(Recv(out m) == 0xAE01, "PowerUpdate (battery)");
        Send(Msg(0x5000, P(0, Cat(P(1, new byte[0]), P(4, new byte[0]))), P(1, Cat(P(0, new byte[0]), P(1, new byte[0])))));
        int id = Recv(out m);
        Check(id == 0x5001, "NowPlayingUpdate: " + System.Text.Encoding.UTF8.GetString(m, 6, Math.Min(m.Length - 6, 60)).Replace("\0", "|"));
        Send(Msg(0xDA00));
        Check(Recv(out m) == 0xDA01 && m[10] == 7, "USBDeviceModeAudioInformation, rate index 7 (44.1 kHz)");
        byte[] hid = { 0x00,0x06,0x00,0x00,0x00,0x01, 0x00,0x06,0x00,0x01,0x05,0xac, 0x00,0x06,0x00,0x02,0x02,0x3c,
            0x00,0x25,0x00,0x04, 0x05,0x0c,0x09,0x01,0xa1,0x01,0x15,0x00,0x25,0x01,0x75,0x01,0x95,0x05,
            0x09,0xb0,0x09,0xb1,0x09,0xb5,0x09,0xb6,0x09,0xb9,0x81,0x02,0x75,0x01,0x95,0x03,0x81,0x03,0xc0 };
        byte[] start = new byte[6 + hid.Length]; start[0] = 0x40; start[1] = 0x40; start[3] = (byte)start.Length; start[4] = 0x68;
        Array.Copy(hid, 0, start, 6, hid.Length);
        Send(start);
        Send(Msg(0x6802, P(0, new byte[] { 0, 1 }), P(1, new byte[] { 0x02 })));
        Send(Msg(0x6802, P(0, new byte[] { 0, 1 }), P(1, new byte[] { 0x00 })));
        Say("ok    StartHID (the BMW's) and a Pause press sent: the probe's log has 'usage b1 pressed: pause'");
        Send(Msg(0x5000, P(0, Cat(P(1, new byte[0]), P(0x1a, new byte[0]))), P(1, P(0, new byte[0]))));
        int np = Recv(out m);
        int at = 6; int tid = -1;
        while (at + 4 <= m.Length) { int n = m[at] << 8 | m[at + 1], pid = m[at + 2] << 8 | m[at + 3];
            if (pid == 0) { int g = at + 4; while (g + 4 <= at + n) { int gn = m[g] << 8 | m[g + 1], gid = m[g + 2] << 8 | m[g + 3]; if (gid == 0x1a) tid = m[g + 4]; g += gn; } }
            at += n; }
        Check(np == 0x5001 && tid >= 0x80, "NowPlayingUpdate names an artwork transfer: " + tid.ToString("X2"));
        byte[] setup = FilePacket();
        long size = 0; for (int i = 2; i < 10; i++) size = size << 8 | setup[i];
        Check(setup[0] == tid && setup[1] == 4 && setup[10] == 0 && setup[11] == 2, "its setup on the file transfer session: " + size + " bytes");
        seq++; Write(Pkt(0x40, seq, ack, 11, new byte[] { (byte)tid, 0x01 }));
        long got = 0; int pieces = 0; bool end = size == 0;
        var jpeg = new List<byte>();
        while (!end) {
            byte[] f = FilePacket(); pieces++;
            for (int i = 2; i < f.Length; i++) jpeg.Add(f[i]);
            got += f.Length - 2; end = f[1] == 0x40 || f[1] == 0xC0;
        }
        if (size == 0) { byte[] f = FilePacket(); Check(f[1] == 0xC0 && f.Length == 2, "no cover for this song: the empty transfer"); }
        else {
            Check(got == size && jpeg[0] == 0xff && jpeg[1] == 0xd8, "the cover in " + pieces + " pieces, " + got + " bytes, a JPEG");
            File.WriteAllBytes(Environment.GetEnvironmentVariable("TEMP") + "\\q2-car-cover.jpg", jpeg.ToArray());
        }
        seq++; Write(Pkt(0x40, seq, ack, 11, new byte[] { (byte)tid, 0x05 }));
        return fails == 0 ? 0 : 1;
    }
}
'@
try { $rc = [Car]::Run() } catch { [Car]::Out.Add("FAIL  " + $_.Exception.InnerException.Message + $_.Exception.Message); $rc = 1 }
[Car]::Out
exit $rc
