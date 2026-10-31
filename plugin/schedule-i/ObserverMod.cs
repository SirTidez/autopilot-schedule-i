// Personal Schedule I extension, Apache-2.0. Explicit runtime bridge; no game methods or input hooks.
using System;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using MelonLoader;
using UnityEngine;
using UnityEngine.SceneManagement;

[assembly: MelonInfo(typeof(Autopilot.ScheduleI.ObserverMod), "Autopilot Schedule I", "0.2.0", "SirTidez")]
[assembly: MelonGame("TVGS", "Schedule I")]

namespace Autopilot.ScheduleI
{
    public sealed class ObserverMod : MelonMod
    {
        bool enabled;
        bool diagnostics;
        double next;
        string snapshot;
        RuntimeBridge bridge;
        readonly int pid = System.Diagnostics.Process.GetCurrentProcess().Id;

        public override void OnInitializeMelon()
        {
            // Separate explicit opt-ins. Never starts a service or model, installs anything or opens a listener.
            var args = Environment.GetCommandLineArgs();
            diagnostics = args.Contains("-AutopilotScheduleIDiagnostics");
            if (args.Contains("-AutopilotScheduleIConnect"))
            {
                var url = "ws://127.0.0.1:8765/v1/bridge";
                foreach (var arg in args)
                    if (arg.StartsWith("-AutopilotScheduleIUrl=", StringComparison.Ordinal)) url = arg.Substring("-AutopilotScheduleIUrl=".Length);
                try
                {
                    bridge = new RuntimeBridge(url, args.Contains("-AutopilotScheduleIControl"));
                    LoggerInstance.Msg("Explicit local bridge enabled. Control requires its separate launch flag; no service is started.");
                }
                catch (Exception e) { LoggerInstance.Warning("Bridge disabled: " + e.Message); }
            }
            enabled = args.Contains("-AutopilotScheduleIObserve");
            if (!enabled) return;
            var folder = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "UserData", "AutopilotScheduleI");
            Directory.CreateDirectory(folder);
            snapshot = Path.Combine(folder, "state-" + pid + ".json");
            LoggerInstance.Msg("Local observation enabled (camera pose only). No network or game control.");
        }

        static string Number(double n)
        {
            if (double.IsNaN(n) || double.IsInfinity(n)) throw new InvalidDataException("Nonfinite camera pose");
            return n.ToString("R", CultureInfo.InvariantCulture);
        }
        static string Quote(string s)
        {
            var b = new StringBuilder("\"");
            foreach (var c in s ?? "")
            {
                if (c == '\\' || c == '"') b.Append('\\').Append(c);
                else if (c < 0x20) b.Append("\\u").Append(((int)c).ToString("x4"));
                else b.Append(c);
            }
            return b.Append('"').ToString();
        }

        // Same kit frame as upstream Runtime/Frame.cs: X forward, Y right, Z up, centimetres.
        static string Point(Vector3 v) => "{\"X\":" + Number(v.z * 100) + ",\"Y\":" + Number(v.x * 100)
            + ",\"Z\":" + Number(v.y * 100) + "}";
        static double Wrap(double n) => (n + 540) % 360 - 180;

        public override void OnGUI()
        {
            if (!diagnostics) return;
            var before = GUI.depth;
            try
            {
                GUI.depth = -1000;
                var scale = Math.Max(1f, Screen.height / 1080f);
                var style = new GUIStyle(GUI.skin.box) { fontSize = (int)(18 * scale), alignment = TextAnchor.MiddleLeft };
                GUI.Box(new Rect(12 * scale, 12 * scale, 620 * scale, 112 * scale),
                    "Autopilot Schedule I 0.2.0 - local diagnostics\n"
                    + (bridge?.DiagnosticState ?? "Bridge: disabled"), style);
            }
            finally { GUI.depth = before; }
        }

        public override void OnUpdate()
        {
            if (bridge != null && !bridge.Connected && bridge.Problem.Length > 0)
            {
                LoggerInstance.Warning("Local bridge ended: " + bridge.Problem);
                bridge.Dispose(); bridge = null;
            }
            try { bridge?.Tick(); }
            catch (Exception e)
            {
                bridge?.Dispose(); bridge = null;
                LoggerInstance.Warning("Bridge disabled: " + e.Message);
            }
            if (!enabled || Time.realtimeSinceStartupAsDouble < next) return;
            next = Time.realtimeSinceStartupAsDouble + 0.25; // bounded, four small snapshots per second
            try
            {
                var scene = SceneManager.GetActiveScene();
                var camera = Camera.main;
                var pose = "null";
                if (camera != null)
                {
                    var t = camera.transform;
                    pose = "{\"eye\":" + Point(t.position) + ",\"yaw\":" + Number(Wrap(t.eulerAngles.y))
                        + ",\"pitch\":" + Number(Wrap(-t.eulerAngles.x)) + "}";
                }
                var json = "{\"protocol\":1,\"pid\":" + pid + ",\"unix_ms\":"
                    + DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() + ",\"engine\":\"unity\",\"game\":\"Schedule I\","
                    + "\"unity\":" + Quote(Application.unityVersion) + ",\"scene\":" + Quote(scene.name)
                    + ",\"focused\":" + (Application.isFocused ? "true" : "false") + ",\"pose_source\":\"camera\",\"camera\":" + pose + "}";
                var temp = snapshot + ".tmp";
                File.WriteAllText(temp, json);
                if (File.Exists(snapshot)) File.Replace(temp, snapshot, null);
                else File.Move(temp, snapshot);
            }
            catch (Exception e)
            {
                // A repeated failure must not flood the game log or frame loop.
                enabled = false;
                LoggerInstance.Warning("Observer disabled: " + e.Message);
                RemoveSnapshot();
            }
        }

        void RemoveSnapshot()
        {
            if (snapshot == null) return;
            try
            {
                if (File.Exists(snapshot)) File.Delete(snapshot);
                if (File.Exists(snapshot + ".tmp")) File.Delete(snapshot + ".tmp");
            }
            catch (IOException) { } // stale files are rejected by PID and timestamp by the external adapter
        }

        public override void OnDeinitializeMelon()
        {
            bridge?.Dispose(); bridge = null;
            RemoveSnapshot();
        }
    }
}
