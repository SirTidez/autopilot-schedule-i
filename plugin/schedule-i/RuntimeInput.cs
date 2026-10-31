// Schedule I personal fork, Apache-2.0. Typed OS input only; no Harmony or game-method injection.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Threading;
using Autopilot.Playtest;

namespace Autopilot.ScheduleI
{
    public interface IInputSink
    {
        bool Focused { get; }
        bool EmergencyStop { get; }
        void Button(string key, bool down);
        void Mouse(int dx, int dy);
    }

    // Testable lease policy. No Unity access: its independent timer still releases input if frames stall.
    public sealed class RuntimeInput : IDisposable
    {
        readonly IInputSink sink;
        readonly Func<double> clock;
        readonly object gate = new object();
        readonly Dictionary<string, double> held = new Dictionary<string, double>();
        readonly double started;
        Timer timer;
        bool stopped;
        int mouseCounts;
        public string StopReason { get; private set; } = "";
        public bool Active { get { lock (gate) return !stopped; } }
        public static readonly HashSet<string> Keys = MakeKeys();

        public RuntimeInput(IInputSink sink, Func<double> clock = null, bool watchdog = true)
        {
            this.sink = sink;
            this.clock = clock ?? (() => (double)Stopwatch.GetTimestamp() / Stopwatch.Frequency);
            started = this.clock();
            if (watchdog) timer = new Timer(_ => Poll(), null, 20, 20);
        }

        static HashSet<string> MakeKeys()
        {
            var keys = new HashSet<string> { "SpaceBar", "Enter", "Tab", "Escape", "LeftShift", "LeftControl",
                "LeftMouseButton", "RightMouseButton", "MiddleMouseButton" };
            for (var c = 'A'; c <= 'Z'; c++) keys.Add(c.ToString());
            foreach (var s in new[] { "Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine" }) keys.Add(s);
            return keys;
        }

        static double Numeric(Dictionary<string, object> args, string key, double fallback, double min, double max)
        {
            if (!args.TryGetValue(key, out var raw)) return fallback;
            if (!(raw is double) && !(raw is long) && !(raw is int)) throw new ArgumentException(key + " must be numeric");
            var n = Convert.ToDouble(raw);
            if (double.IsNaN(n) || double.IsInfinity(n) || n < min || n > max) throw new ArgumentException(key + " out of bounds");
            return n;
        }

        public static void ValidateKey(Dictionary<string, object> args)
        {
            var mode = Json.Str(args, "mode", "press");
            if (mode == "release_all") return;
            var key = Json.Str(args, "key");
            if (key == "MouseX" || key == "MouseY")
            {
                if (mode != "press" || Json.Bool(args, "rate", false) || args.ContainsKey("seconds"))
                    throw new ArgumentException("Mouse axes are one-shot counts only");
                Numeric(args, "value", 1, -200, 200);
                return;
            }
            if (!Keys.Contains(key) || (mode != "press" && mode != "hold" && mode != "release"))
                throw new ArgumentException("Unsupported key/mode");
            if (mode == "release") return;
            Numeric(args, "value", 1, 1, 1);
            if (Json.Bool(args, "rate", false)) throw new ArgumentException("Digital keys only");
            if (mode == "hold") Numeric(args, "lease", 0.6, 0.001, 0.6);
            if (args.ContainsKey("seconds")) Numeric(args, "seconds", 0.1, 0.001, 5);
        }

        void Check()
        {
            if (stopped) throw new InvalidOperationException("Control stopped: " + StopReason);
            if (!sink.Focused || sink.EmergencyStop || clock() - started >= 60)
            {
                Stop("focus/F8/60-second control budget");
                throw new InvalidOperationException("Control stopped: " + StopReason);
            }
        }

        public void Poll()
        {
            lock (gate)
            {
                if (stopped) return;
                try
                {
                    Check();
                    var expired = new List<string>();
                    foreach (var pair in held) if (clock() >= pair.Value) expired.Add(pair.Key);
                    foreach (var key in expired) { sink.Button(key, false); held.Remove(key); }
                }
                catch (Exception e) { Stop(e.Message); }
            }
        }

        public void Key(Dictionary<string, object> args)
        {
            lock (gate)
            {
                try
                {
                    ValidateKey(args);
                    var mode = Json.Str(args, "mode", "press");
                    if (mode == "release_all") { ReleaseAll(); return; }
                    var key = Json.Str(args, "key");
                    if (mode == "release")
                    {
                        if (!Keys.Contains(key)) throw new ArgumentException("Unsupported key");
                        if (held.ContainsKey(key)) { sink.Button(key, false); held.Remove(key); }
                        return;
                    }
                    Check();
                    if (key == "MouseX" || key == "MouseY")
                    {
                        if (mode != "press" || Json.Bool(args, "rate", false) || args.ContainsKey("seconds"))
                            throw new ArgumentException("Mouse axes are one-shot counts only");
                        var n = (int)Math.Round(Numeric(args, "value", 1, -200, 200), MidpointRounding.AwayFromZero);
                        if (mouseCounts + Math.Abs(n) > 4000) throw new ArgumentException("Mouse count budget exceeded");
                        mouseCounts += Math.Abs(n);
                        if (n != 0) sink.Mouse(key == "MouseX" ? n : 0, key == "MouseY" ? -n : 0);
                        return;
                    }
                    if (!Keys.Contains(key) || (mode != "press" && mode != "hold")) throw new ArgumentException("Unsupported key/mode");
                    if (Numeric(args, "value", 1, 1, 1) != 1 || Json.Bool(args, "rate", false)) throw new ArgumentException("Digital keys only");
                    if (mode == "hold") Numeric(args, "lease", 0.6, 0.001, 0.6);
                    var duration = mode == "press" ? Numeric(args, "seconds", 0.1, 0.001, 5)
                        : args.ContainsKey("seconds") ? Numeric(args, "seconds", 0.6, 0.001, 5)
                        : Numeric(args, "lease", 0.6, 0.001, 0.6);
                    held[key] = clock() + duration; // ownership before possible partial SendInput failure
                    sink.Button(key, true);
                }
                catch { Stop("input error"); throw; }
            }
        }

        public void ReleaseAll()
        {
            lock (gate)
            {
                var errors = new List<string>();
                foreach (var key in new List<string>(held.Keys))
                {
                    try { sink.Button(key, false); held.Remove(key); }
                    catch (Exception e) { errors.Add(e.Message); }
                }
                if (errors.Count > 0) throw new InvalidOperationException("Release failed: " + string.Join("; ", errors));
            }
        }

        public void Stop(string reason)
        {
            lock (gate)
            {
                if (!stopped || StopReason.Length == 0) StopReason = reason;
                stopped = true;
                timer?.Dispose();
                timer = null;
                try { ReleaseAll(); }
                catch (Exception e) { StopReason += "; " + e.Message; }
            }
        }

        public void Dispose() => Stop("disposed");
    }

    public sealed class WindowsInputSink : IInputSink
    {
        readonly uint pid = (uint)Process.GetCurrentProcess().Id;
        public bool Focused { get { GetWindowThreadProcessId(GetForegroundWindow(), out var p); return p == pid; } }
        public bool EmergencyStop => (GetAsyncKeyState(0x77) & 0x8000) != 0;
        [StructLayout(LayoutKind.Sequential)] struct MouseData { public int X, Y; public uint Data, Flags, Time; public UIntPtr Extra; }
        [StructLayout(LayoutKind.Sequential)] struct KeyData { public ushort Key, Scan; public uint Flags, Time; public UIntPtr Extra; }
        [StructLayout(LayoutKind.Explicit)] struct Payload
        {
            [FieldOffset(0)] public MouseData Mouse;
            [FieldOffset(0)] public KeyData Key;
        }
        [StructLayout(LayoutKind.Sequential)] struct Input { public uint Type; public Payload Data; }
        [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
        [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
        [DllImport("user32.dll")] static extern short GetAsyncKeyState(int key);
        [DllImport("user32.dll", SetLastError = true)] static extern uint SendInput(uint count, Input[] inputs, int size);

        void Send(Input input, bool down)
        {
            if (down && (!Focused || EmergencyStop)) throw new InvalidOperationException("Input focus/F8 guard");
            if (SendInput(1, new[] { input }, Marshal.SizeOf(typeof(Input))) != 1) throw new InvalidOperationException("SendInput denied");
        }
        public void Mouse(int dx, int dy) => Send(new Input { Data = new Payload { Mouse = new MouseData { X = dx, Y = dy, Flags = 1 } } }, true);
        public void Button(string key, bool down)
        {
            if (key.EndsWith("MouseButton", StringComparison.Ordinal))
            {
                var flags = key == "LeftMouseButton" ? 2u : key == "RightMouseButton" ? 8u : 32u;
                Send(new Input { Data = new Payload { Mouse = new MouseData { Flags = down ? flags : flags * 2 } } }, down);
                return;
            }
            ushort vk;
            if (key.Length == 1) vk = key[0];
            else
            {
                var names = new[] { "Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine" };
                var digit = Array.IndexOf(names, key);
                vk = digit >= 0 ? (ushort)(0x30 + digit) : key == "SpaceBar" ? (ushort)0x20 : key == "Enter" ? (ushort)0x0D
                    : key == "Tab" ? (ushort)9 : key == "Escape" ? (ushort)0x1B : key == "LeftShift" ? (ushort)0xA0 : (ushort)0xA2;
            }
            Send(new Input { Type = 1, Data = new Payload { Key = new KeyData { Key = vk, Flags = down ? 0u : 2u } } }, down);
        }
    }
}
