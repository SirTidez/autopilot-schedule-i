// Offline executable tests: fake input sink only, no Unity, MelonLoader, Win32 calls, network or model.
using System;
using System.Collections.Generic;
using Autopilot.ScheduleI;

sealed class FakeInput : IInputSink
{
    public bool Focused { get; set; } = true;
    public bool EmergencyStop { get; set; }
    public bool FailDown;
    public string FailUp;
    public readonly List<string> Events = new List<string>();
    public void Button(string key, bool down)
    {
        Events.Add(key + (down ? ":down" : ":up"));
        if ((down && FailDown) || (!down && key == FailUp)) throw new InvalidOperationException("injected failure");
    }
    public void Mouse(int dx, int dy) => Events.Add("mouse:" + dx + ":" + dy);
}

static class SafetyTests
{
    static int passed;
    static void Check(bool ok, string what) { if (!ok) throw new Exception(what); }
    static void Test(string name, Action body) { body(); passed++; Console.WriteLine("PASS " + name); }
    static void Fails(Action body)
    {
        var failed = false;
        try { body(); } catch (Exception) { failed = true; }
        Check(failed, "expected explicit failure");
    }
    static Dictionary<string, object> Key(string key, string mode = "hold") =>
        new Dictionary<string, object> { ["key"] = key, ["mode"] = mode };

    static int Main()
    {
        Test("unrenewed lease expires at 0.6s", () => {
            var s = new FakeInput(); double t = 0;
            using (var input = new RuntimeInput(s, () => t, false))
            {
                input.Key(Key("W")); t = 0.59; input.Poll(); Check(s.Events.Count == 1, "held before expiry");
                t = 0.61; input.Poll(); Check(s.Events[1] == "W:up", "expired input released");
            }
        });
        Test("renewal works but cannot reset connection budget", () => {
            var s = new FakeInput(); double t = 0;
            using (var input = new RuntimeInput(s, () => t, false))
            {
                input.Key(Key("W")); t = 0.5; input.Key(Key("W")); t = 0.8; input.Poll();
                Check(!s.Events.Contains("W:up"), "renewed lease should remain held");
                t = 60; input.Poll(); Check(!input.Active && s.Events.Contains("W:up"), "hard session stop");
                Fails(() => input.Key(Key("W")));
            }
        });
        Test("timed hold bounded and exact", () => {
            var s = new FakeInput(); double t = 0;
            using (var input = new RuntimeInput(s, () => t, false))
            {
                var args = Key("A"); args["seconds"] = 2.0; input.Key(args);
                t = 1.0; input.Poll(); Check(s.Events.Count == 1, "explicit timed hold still held");
                t = 2.01; input.Poll(); Check(s.Events[1] == "A:up", "timed hold released");
            }
        });
        foreach (var stop in new[] { "focus", "F8", "remote" }) Test(stop + " stops and releases all, remains latched", () => {
            var s = new FakeInput();
            using (var input = new RuntimeInput(s, () => 0, false))
            {
                input.Key(Key("W")); input.Key(Key("LeftMouseButton"));
                if (stop == "focus") s.Focused = false;
                else if (stop == "F8") s.EmergencyStop = true;
                else input.Stop("remote stop");
                input.Poll(); Check(s.Events.Contains("W:up") && s.Events.Contains("LeftMouseButton:up"), "release both");
                s.Focused = true; s.EmergencyStop = false; Fails(() => input.Key(Key("W")));
            }
        });
        Test("partial down failure still releases", () => {
            var s = new FakeInput { FailDown = true };
            using (var input = new RuntimeInput(s, () => 0, false))
            {
                Fails(() => input.Key(Key("E"))); Check(s.Events.Contains("E:up"), "partial failure cleanup");
            }
        });
        Test("failed release does not skip other keys", () => {
            var s = new FakeInput();
            using (var input = new RuntimeInput(s, () => 0, false))
            {
                input.Key(Key("W")); input.Key(Key("A")); s.FailUp = "W"; input.Stop("test");
                Check(s.Events.Contains("A:up") && input.StopReason.Contains("Release failed"), "report failed release, attempt all");
            }
        });
        Test("strict numeric and mode validation", () => {
            foreach (var bad in new object[] { double.NaN, double.PositiveInfinity, true, "5", -1.0, 6.0 })
            {
                var s = new FakeInput();
                using (var input = new RuntimeInput(s, () => 0, false))
                {
                    var args = Key("W"); args["seconds"] = bad; Fails(() => input.Key(args));
                    Check(s.Events.Count == 0, "invalid duration emitted input");
                }
            }
            foreach (var lease in new[] { 0.0, 0.61, 100.0 })
            {
                using (var input = new RuntimeInput(new FakeInput(), () => 0, false))
                { var args = Key("W"); args["lease"] = lease; Fails(() => input.Key(args)); }
            }
        });
        Test("mouse one-shot signs and total budget", () => {
            var s = new FakeInput();
            using (var input = new RuntimeInput(s, () => 0, false))
            {
                var args = Key("MouseY", "press"); args["value"] = 200.0;
                for (var n = 0; n < 20; n++) input.Key(args);
                Check(s.Events[0] == "mouse:0:-200", "MouseY positive up");
                Fails(() => input.Key(args)); Check(s.Events.Count == 20, "count budget enforced before emission");
            }
        });
        Test("loopback endpoint restriction without DNS/network", () => {
            Check(BridgeEndpoint.LocalUri("ws://127.0.0.1:8765/v1/bridge").IsLoopback, "IPv4");
            Check(BridgeEndpoint.LocalUri("ws://[::1]:8765/v1/bridge").IsLoopback, "IPv6");
            foreach (var url in new[] { "ws://localhost/v1/bridge", "wss://127.0.0.1/v1/bridge", "ws://8.8.8.8/v1/bridge",
                "ws://user@127.0.0.1/v1/bridge", "ws://127.0.0.1/other", "ws://127.0.0.1/v1/bridge?token=x" })
                Fails(() => BridgeEndpoint.LocalUri(url));
        });
        Test("frame depth/shape limits preserve quoted braces", () => {
            Check(BridgeEndpoint.ParseFrame("{\"type\":\"request\",\"note\":\"[{\\\"}]\"}")["type"].ToString() == "request", "quoted delimiters");
            Fails(() => BridgeEndpoint.ParseFrame("[]"));
            Fails(() => BridgeEndpoint.ParseFrame("{\"x\":" + new string('[', 33) + "0" + new string(']', 33) + "}"));
            Fails(() => BridgeEndpoint.ParseFrame(new string(' ', 65537)));
            Fails(() => BridgeEndpoint.ParseFrame("{\"x\":"));
        });
        Console.WriteLine(passed + " offline C# safety tests passed; no game or OS input touched.");
        return 0;
    }
}
