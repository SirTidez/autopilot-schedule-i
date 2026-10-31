// Personal Schedule I extension, Apache-2.0. Read-only Unity observations; no game methods or input hooks.
using System;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using MelonLoader;
using UnityEngine;
using UnityEngine.SceneManagement;

[assembly: MelonInfo(typeof(Autopilot.ScheduleI.ObserverMod), "Autopilot Schedule I Observer", "0.1.0", "SirTidez")]
[assembly: MelonGame("TVGS", "Schedule I")]

namespace Autopilot.ScheduleI
{
    public sealed class ObserverMod : MelonMod
    {
        bool enabled;
        double next;
        string snapshot;
        readonly int pid = System.Diagnostics.Process.GetCurrentProcess().Id;

        public override void OnInitializeMelon()
        {
            // Deliberate launch-time opt-in: never connects to a service, opens a port or installs anything.
            enabled = Environment.GetCommandLineArgs().Contains("-AutopilotScheduleIObserve");
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

        public override void OnUpdate()
        {
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

        public override void OnDeinitializeMelon() => RemoveSnapshot();
    }
}
