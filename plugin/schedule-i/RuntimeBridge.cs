// Schedule I personal fork, Apache-2.0. Protocol-1 subset over an explicit loopback-only outgoing socket.
using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Autopilot.Playtest;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace Autopilot.ScheduleI
{
    public sealed class RuntimeBridge : IDisposable
    {
        const int MaxQueue = 256;
        readonly ConcurrentQueue<Dictionary<string, object>> incoming = new ConcurrentQueue<Dictionary<string, object>>();
        readonly ConcurrentQueue<string> outgoing = new ConcurrentQueue<string>();
        readonly CancellationTokenSource cancel = new CancellationTokenSource();
        readonly ClientWebSocket socket = new ClientWebSocket();
        readonly bool controlAllowed;
        readonly string hello;
        RuntimeInput input;
        volatile bool ready;
        volatile bool dead;
        volatile bool controlStopped;
        double nextHeartbeat;
        int sceneHandle;
        public string Problem { get; private set; } = "";
        public bool Connected => ready && !dead;
        bool CanControl => controlAllowed && !controlStopped && (input == null || input.Active);
        public string DiagnosticState
        {
            get
            {
                var owned = input?.OwnedInputs;
                return "Bridge: " + (Connected ? "connected" : "disconnected")
                    + " | control: " + (CanControl ? (input == null ? "ready (unarmed)" : "armed") : "stopped/disabled")
                    + "\nOwned inputs: " + (owned == null || owned.Length == 0 ? "none" : string.Join(", ", owned))
                    + "\nStop reason: " + (input?.StopReason ?? (controlStopped ? "remote stop" : "none"));
            }
        }
        public static readonly string[] Ops = { "context", "session", "ping", "key", "pose", "find", "transforms", "view", "movement", "ui" };

        public RuntimeBridge(string url, bool controlAllowed)
        {
            var uri = BridgeEndpoint.LocalUri(url);
            this.controlAllowed = controlAllowed;
            sceneHandle = SceneManager.GetActiveScene().handle;
            hello = Json.Write(new Dictionary<string, object> {
                ["type"] = "hello", ["protocol"] = 1, ["plugin_version"] = "0.2.0", ["engine"] = "schedule_i",
                ["engine_version"] = Application.unityVersion, ["project"] = "Schedule I", ["host"] = "game",
                ["game_version"] = Application.version, ["game_pid"] = System.Diagnostics.Process.GetCurrentProcess().Id,
#if MONO
                ["runtime"] = "Mono",
#else
                ["runtime"] = "IL2CPP",
#endif
                ["loader_version"] = typeof(MelonLoader.MelonMod).Assembly.GetName().Version.ToString(),
                ["adapter"] = "melonloader", ["source_export"] = false, ["share"] = false,
                ["features"] = new[] { "timed_hold", "schedule_i_runtime" }, ["capabilities"] = Ops,
                ["input"] = new Dictionary<string, object> { ["keys"] = controlAllowed, ["handling"] = "os",
                    ["actions"] = false, ["pointer"] = false, ["move"] = "input" } });
            _ = Task.Run(() => Connect(uri));
        }

        async Task Send(string text)
        {
            var bytes = Encoding.UTF8.GetBytes(text);
            await socket.SendAsync(new ArraySegment<byte>(bytes), WebSocketMessageType.Text, true, cancel.Token);
        }

        async Task Connect(Uri uri)
        {
            Task writer = null;
            try
            {
                using (var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancel.Token))
                {
                    timeout.CancelAfter(10000);
                    await socket.ConnectAsync(uri, timeout.Token);
                }
                await Send(hello);
                writer = Task.Run(async () => {
                    while (!cancel.IsCancellationRequested)
                    {
                        if (outgoing.TryDequeue(out var text)) await Send(text);
                        else await Task.Delay(10, cancel.Token);
                    }
                });
                var buffer = new byte[4096];
                while (!cancel.IsCancellationRequested)
                {
                    using (var body = new MemoryStream())
                    {
                        WebSocketReceiveResult part;
                        do
                        {
                            part = await socket.ReceiveAsync(new ArraySegment<byte>(buffer), cancel.Token);
                            if (part.MessageType != WebSocketMessageType.Text) throw new InvalidDataException("Bridge needs text frames");
                            body.Write(buffer, 0, part.Count);
                            if (body.Length > 65536) throw new InvalidDataException("Bridge frame exceeds 64 KiB");
                        } while (!part.EndOfMessage);
                        var frame = BridgeEndpoint.ParseFrame(new UTF8Encoding(false, true).GetString(body.ToArray()));
                        var type = Json.Str(frame, "type");
                        if (type == "welcome") ready = true;
                        else if (type == "stop" || type == "disconnect")
                        {
                            controlStopped = true;
                            input?.Stop("remote stop/disconnect"); // independent of stalled Unity frame loop
                            if (type == "disconnect") throw new IOException("Remote disconnect");
                        }
                        else if (type == "request")
                        {
                            if (Json.Str(frame, "op") == "session" && Json.Str(Json.Obj(frame, "args"), "action") == "stop")
                            {
                                controlStopped = true;
                                input?.Stop("remote session stop");
                            }
                            if (incoming.Count >= MaxQueue) throw new InvalidDataException("Bridge request queue full");
                            incoming.Enqueue(frame);
                        }
                    }
                }
            }
            catch (Exception e) { Problem = e.Message; }
            finally
            {
                controlStopped = true;
                dead = true;
                ready = false;
                input?.Stop("bridge disconnected");
                cancel.Cancel();
                socket.Abort();
                if (writer != null) { try { await writer; } catch (Exception) { } }
            }
        }

        void SendFrame(Dictionary<string, object> frame)
        {
            if (outgoing.Count >= MaxQueue) { Dispose(); throw new IOException("Bridge response queue full"); }
            var text = Json.Write(frame);
            if (Encoding.UTF8.GetByteCount(text) > 65536) { Dispose(); throw new IOException("Bridge response exceeds 64 KiB"); }
            outgoing.Enqueue(text);
        }

        public void Tick()
        {
            if (!Connected) return;
            var scene = SceneManager.GetActiveScene();
            if (scene.handle != sceneHandle)
            {
                input?.ReleaseAll();
                RuntimeRead.ClearUiCache();
                sceneHandle = scene.handle;
            }
            input?.Poll();
            for (var count = 0; count < 32 && incoming.TryDequeue(out var frame); count++)
            {
                var response = new Dictionary<string, object> { ["type"] = "response", ["id"] = frame.ContainsKey("id") ? frame["id"] : null,
                    ["t"] = Time.timeAsDouble };
                try
                {
                    if (frame.ContainsKey("args") && frame["args"] != null && !(frame["args"] is Dictionary<string, object>))
                        throw new ArgumentException("Request args must be an object");
                    var args = Json.Obj(frame, "args") ?? new Dictionary<string, object>();
                    response["result"] = Request(Json.Str(frame, "op"), args);
                    response["ok"] = true;
                }
                catch (Exception e)
                {
                    controlStopped = true;
                    input?.Stop("request error: " + e.Message);
                    response["ok"] = false; response["status"] = 400; response["error"] = e.Message;
                }
                SendFrame(response);
            }
            if (Time.realtimeSinceStartupAsDouble >= nextHeartbeat)
            {
                nextHeartbeat = Time.realtimeSinceStartupAsDouble + 0.5;
                SendFrame(new Dictionary<string, object> { ["type"] = "event", ["kind"] = "heartbeat", ["t"] = Time.timeAsDouble });
            }
        }

        object Request(string op, Dictionary<string, object> args)
        {
            switch (op)
            {
                case "context": return RuntimeRead.Context(CanControl, input != null && input.Active);
                case "ping": return new Dictionary<string, object> { ["game_time"] = Time.timeAsDouble };
                case "pose": return RuntimeRead.Pose();
                case "movement": return RuntimeRead.Movement();
                case "find": return RuntimeRead.Find(args);
                case "transforms": return RuntimeRead.Transforms(args);
                case "view": return RuntimeRead.View(args);
                case "ui": return RuntimeRead.Ui(args);
                case "key":
                    // Upstream missing_ops probes input support with an empty key; it must never mutate state.
                    if (Json.Str(args, "key") == "" && Json.Str(args, "mode", "press") == "press")
                        return new Dictionary<string, object> { ["keys"] = CanControl };
                    RuntimeInput.ValidateKey(args); // malformed/probe/release requests never arm control
                    var mode = Json.Str(args, "mode", "press");
                    if ((mode == "release_all" || mode == "release") && input == null) return new Dictionary<string, object>();
                    if (!CanControl && mode != "release_all" && mode != "release")
                        throw new InvalidOperationException("Control disabled, stopped or not explicitly enabled with -AutopilotScheduleIControl");
                    if (input == null) input = new RuntimeInput(new WindowsInputSink()); // first valid action starts one 60s budget
                    // A socket-thread stop may race the assignment above; never lose that stop before arming.
                    if (!Connected || controlStopped) input.Stop("connection stopped while arming");
                    input.Key(args); return new Dictionary<string, object>();
                case "session":
                    var action = Json.Str(args, "action", "status");
                    if (action == "stop") input?.Stop("session stop");
                    else if (action != "status") throw new InvalidOperationException("Retail runtime supports session status/stop only");
                    return new Dictionary<string, object> { ["running"] = true, ["control_active"] = input != null && input.Active,
                        ["owned_inputs"] = input?.OwnedInputs ?? new string[0], ["control_stopped"] = controlStopped,
                        ["control_stop_reason"] = input?.StopReason ?? "control disabled", ["restart_supported"] = false };
                default: throw new InvalidOperationException("unknown op: " + op + " (unsupported Schedule I runtime capability)");
            }
        }

        public void Dispose()
        {
            dead = true; ready = false; controlStopped = true;
            input?.Stop("bridge disposed");
            cancel.Cancel(); socket.Abort();
        }
    }
}
