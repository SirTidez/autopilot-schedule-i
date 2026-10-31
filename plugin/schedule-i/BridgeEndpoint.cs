// Schedule I personal fork, Apache-2.0. Pure endpoint validation; no DNS lookup or network calls.
using System;
using System.Net;
using System.Collections.Generic;
using System.IO;
using Autopilot.Playtest;

namespace Autopilot.ScheduleI
{
    public static class BridgeEndpoint
    {
        public static Dictionary<string, object> ParseFrame(string text)
        {
            if (text == null || text.Length > 65536) throw new InvalidDataException("Bridge frame too large");
            // Upstream parser is recursive; bound structure depth before parsing untrusted frames.
            var depth = 0; var quoted = false; var escaped = false;
            foreach (var c in text)
            {
                if (quoted)
                {
                    if (escaped) escaped = false;
                    else if (c == '\\') escaped = true;
                    else if (c == '"') quoted = false;
                }
                else if (c == '"') quoted = true;
                else if (c == '[' || c == '{') { if (++depth > 32) throw new InvalidDataException("Bridge nesting exceeds 32"); }
                else if (c == ']' || c == '}') { if (--depth < 0) throw new InvalidDataException("Unbalanced bridge frame"); }
            }
            if (quoted || depth != 0) throw new InvalidDataException("Unbalanced bridge frame");
            return Json.ParseObject(text) ?? throw new InvalidDataException("Bridge frame must be an object");
        }

        public static Uri LocalUri(string value)
        {
            if (!Uri.TryCreate(value, UriKind.Absolute, out var uri) || uri.Scheme != "ws"
                || !IPAddress.TryParse(uri.Host.Trim('[', ']'), out var ip) || !IPAddress.IsLoopback(ip)
                || uri.AbsolutePath != "/v1/bridge" || uri.UserInfo.Length != 0 || uri.Query.Length != 0 || uri.Fragment.Length != 0)
                throw new ArgumentException("Bridge URL must be ws://<literal-loopback-IP>:<port>/v1/bridge");
            return uri;
        }
    }
}
