// Schedule I personal fork, Apache-2.0. Bounded typed Unity reads; no game SDK, reflection or source export.
using System;
using System.Collections.Generic;
using Autopilot.Playtest;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace Autopilot.ScheduleI
{
    public static class RuntimeRead
    {
        const int ScanLimit = 2048;
        sealed class UiNode { public GameObject Object; public string Name; }
        static readonly Dictionary<string, UiNode> uiNodes = new Dictionary<string, UiNode>();
        static readonly Queue<string> uiOrder = new Queue<string>();
        public static void ClearUiCache() { uiNodes.Clear(); uiOrder.Clear(); }
        static string PathOf(GameObject go) => "runtime:" + go.scene.handle + ":" + go.GetInstanceID();
        static double Finite(double n)
        {
            if (double.IsNaN(n) || double.IsInfinity(n)) throw new InvalidOperationException("Nonfinite Unity observation");
            return n;
        }
        static double Wrap(double n) => (Finite(n) + 540) % 360 - 180;
        static Dictionary<string, object> Point(Vector3 p) => new Dictionary<string, object> { ["X"] = Finite(p.z) * 100.0, ["Y"] = Finite(p.x) * 100.0, ["Z"] = Finite(p.y) * 100.0 };
        static CharacterController Candidate(Camera camera) => camera != null ? camera.GetComponentInParent<CharacterController>() : null;

        public static Dictionary<string, object> Context(bool control, bool active = false)
        {
            var camera = Camera.main;
            var cc = Candidate(camera);
            return new Dictionary<string, object> { ["engine"] = "schedule_i", ["project"] = "Schedule I", ["host"] = "game",
                ["adapter"] = "melonloader", ["source_export"] = false, ["game_time"] = Time.timeAsDouble,
                ["world"] = SceneManager.GetActiveScene().name, ["character"] = cc != null ? PathOf(cc.gameObject) : "",
                ["camera"] = camera != null ? PathOf(camera.gameObject) : "", ["capabilities"] = RuntimeBridge.Ops,
                ["control_enabled"] = control, ["control_active"] = active, ["pose_source"] = cc != null ? "camera_ancestor_character_controller_candidate" : "camera_only",
                ["player_identity_verified"] = false, ["project_dir"] = "", ["input"] = new Dictionary<string, object> {
                    ["keys"] = control, ["handling"] = "os", ["actions"] = false, ["move"] = "input" } };
        }

        public static Dictionary<string, object> Pose()
        {
            var camera = Camera.main;
            if (camera == null) throw new InvalidOperationException("No tagged main camera");
            var cc = Candidate(camera);
            var t = camera.transform;
            var p = new Dictionary<string, object> { ["eye"] = Point(t.position), ["yaw"] = Wrap(t.eulerAngles.y),
                ["pitch"] = Wrap(-t.eulerAngles.x), ["camera"] = PathOf(camera.gameObject),
                ["pose_source"] = cc != null ? "camera_ancestor_character_controller_candidate" : "camera_only",
                ["player_identity_verified"] = false };
            if (cc != null)
            {
                p["at"] = Point(cc.transform.position); p["velocity"] = Point(cc.velocity);
                p["on_ground"] = cc.isGrounded; p["character"] = PathOf(cc.gameObject);
            }
            return p;
        }

        public static Dictionary<string, object> Movement()
        {
            var cc = Candidate(Camera.main);
            var p = new Dictionary<string, object> { ["walk_speed"] = null, ["jump_z"] = null, ["gravity"] = null,
                ["air_control"] = null, ["player_identity_verified"] = false };
            if (cc != null)
            {
                p["half_height"] = cc.bounds.extents.y * 100.0;
                p["radius"] = cc.radius * Math.Max(Math.Abs(cc.transform.lossyScale.x), Math.Abs(cc.transform.lossyScale.z)) * 100.0;
                p["step"] = cc.stepOffset * Math.Abs(cc.transform.lossyScale.y) * 100.0;
                p["max_slope"] = cc.slopeLimit;
                p["pose_source"] = "camera_ancestor_character_controller_candidate";
            }
            return p;
        }

        static List<GameObject> Objects(bool inactive = false)
        {
            var found = new List<GameObject>();
            var queue = new Queue<Transform>();
            for (var s = 0; s < Math.Min(SceneManager.sceneCount, 8); s++)
            {
                var scene = SceneManager.GetSceneAt(s);
                if (!scene.isLoaded) continue;
                var roots = scene.GetRootGameObjects();
                for (var i = 0; i < roots.Length && queue.Count < ScanLimit; i++) queue.Enqueue(roots[i].transform);
            }
            var scanned = 0;
            while (queue.Count > 0 && scanned++ < ScanLimit)
            {
                var t = queue.Dequeue();
                if (t == null) continue;
                if (inactive || t.gameObject.activeInHierarchy) found.Add(t.gameObject);
                for (var c = 0; c < t.childCount && queue.Count + scanned < ScanLimit; c++) queue.Enqueue(t.GetChild(c));
            }
            return found;
        }

        static bool Class(GameObject go, string cls)
        {
            switch (cls)
            {
                case "": case "Object": case "GameObject": case "Actor": return true;
                case "Camera": return go.GetComponent<Camera>() != null;
                case "CharacterController": return go.GetComponent<CharacterController>() != null;
                case "Renderer": return go.GetComponent<Renderer>() != null;
                default: throw new ArgumentException("Only GameObject/Camera/CharacterController/Renderer class filters are supported");
            }
        }

        public static object Find(Dictionary<string, object> args)
        {
            var objects = new List<object>();
            var limit = Math.Min(128, Math.Max(1, (int)Json.Num(args, "limit", 128)));
            var name = Json.Str(args, "name");
            foreach (var go in Objects(Json.Bool(args, "inactive", false)))
            {
                if (go.name.IndexOf(name, StringComparison.OrdinalIgnoreCase) < 0 || !Class(go, Json.Str(args, "class"))) continue;
                objects.Add(PathOf(go));
                if (objects.Count >= limit) break;
            }
            return new Dictionary<string, object> { ["objects"] = objects, ["scan_limit"] = ScanLimit, ["truncated_possible"] = true };
        }

        static Dictionary<string, object> TransformOf(GameObject go)
        {
            var t = go.transform;
            var p = new Dictionary<string, object> { ["location"] = Point(t.position), ["rotation"] = new Dictionary<string, object> {
                ["Yaw"] = Wrap(t.eulerAngles.y), ["Pitch"] = Wrap(-t.eulerAngles.x), ["Roll"] = Wrap(-t.eulerAngles.z) } };
            var renderer = go.GetComponent<Renderer>();
            var cc = go.GetComponent<CharacterController>();
            if (renderer != null || cc != null)
            {
                var bounds = renderer != null ? renderer.bounds : cc.bounds;
                p["center"] = Point(bounds.center); p["extent"] = Point(bounds.extents);
            }
            if (cc != null) p["velocity"] = Point(cc.velocity);
            return p;
        }

        public static object Transforms(Dictionary<string, object> args)
        {
            var names = Json.List(args, "objects") ?? new List<object>();
            if (names.Count > 128) throw new ArgumentException("At most 128 transforms per request");
            var wanted = new HashSet<string>();
            foreach (var name in names) if (name is string s) wanted.Add(s); else throw new ArgumentException("Object paths must be strings");
            var result = new Dictionary<string, object>();
            // Context identities stay resolvable even when a large hierarchy hits the scan cap.
            var camera = Camera.main;
            var cc = Candidate(camera);
            if (camera != null && wanted.Contains(PathOf(camera.gameObject))) result[PathOf(camera.gameObject)] = TransformOf(camera.gameObject);
            if (cc != null && wanted.Contains(PathOf(cc.gameObject))) result[PathOf(cc.gameObject)] = TransformOf(cc.gameObject);
            foreach (var go in Objects(true)) if (wanted.Contains(PathOf(go))) result[PathOf(go)] = TransformOf(go);
            return new Dictionary<string, object> { ["objects"] = result, ["scan_limit"] = ScanLimit };
        }

        public static object View(Dictionary<string, object> args)
        {
            var camera = Camera.main;
            if (camera == null) throw new InvalidOperationException("No tagged main camera");
            var limit = Math.Min(64, Math.Max(1, (int)Json.Num(args, "max_count", 24)));
            var maxDistance = Math.Min(100000, Math.Max(1, Json.Num(args, "max_distance", 5000))) / 100.0;
            var only = Json.List(args, "only");
            var visible = new List<object>();
            foreach (var go in Objects())
            {
                if (only != null && !only.Contains(PathOf(go)) && !only.Contains(go.name)) continue;
                var renderer = go.GetComponent<Renderer>();
                if (renderer == null || !renderer.enabled) continue;
                var point = renderer.bounds.center;
                var vp = camera.WorldToViewportPoint(point);
                var distance = Vector3.Distance(point, camera.transform.position);
                if (vp.z <= 0 || vp.x < 0 || vp.x > 1 || vp.y < 0 || vp.y > 1 || distance > maxDistance) continue;
                visible.Add(new Dictionary<string, object> { ["name"] = PathOf(go), ["display_name"] = go.name, ["path"] = PathOf(go), ["class"] = "Renderer",
                    ["center"] = Point(point), ["size"] = Point(renderer.bounds.size), ["distance"] = distance * 100.0,
                    ["screen_x"] = (vp.x - 0.5) * 2, ["screen_y"] = (vp.y - 0.5) * 2,
                    ["visibility_source"] = "projected_renderer_center_no_occlusion_test" });
                if (visible.Count >= limit) break;
            }
            var pose = Pose();
            return new Dictionary<string, object> { ["visible"] = visible, ["scene"] = new Dictionary<string, object>(),
                ["camera"] = new Dictionary<string, object> { ["location"] = pose["eye"], ["yaw"] = pose["yaw"],
                    ["pitch"] = pose["pitch"], ["fov"] = Camera.VerticalToHorizontalFieldOfView(camera.fieldOfView, camera.aspect > 0 ? camera.aspect : 16f / 9f) },
                ["body"] = new Dictionary<string, object>(), ["crosshair_tested"] = false, ["oriented"] = false,
                ["omitted"] = new[] { "occlusion", "crosshair", "touching", "sonar", "ui", "inventory", "gameplay_state" },
                ["scan_limit"] = ScanLimit, ["truncated_possible"] = true };
        }

        // Read-only active uGUI hierarchy, including DontDestroyOnLoad canvases. No click/event/reflection calls.
        // Names/rectangles are evidence of an active UI object, not inferred text, item contents or widget semantics.
        public static object Ui(Dictionary<string, object> args)
        {
            var requested = Json.List(args, "objects");
            if (requested != null)
            {
                if (requested.Count > 64) throw new ArgumentException("At most 64 tracked UI objects");
                var exact = new List<object>();
                var unknown = new List<object>();
                foreach (var item in requested)
                {
                    if (!(item is string id)) throw new ArgumentException("UI IDs must be strings");
                    if (uiNodes.TryGetValue(id, out var node)) exact.Add(UiElement(id, node));
                    else unknown.Add(id);
                }
                return new Dictionary<string, object> { ["elements"] = exact, ["unknown_ids"] = unknown,
                    ["exact_tracked_query"] = true, ["truncated_possible"] = false, ["actions_supported"] = false };
            }
            var filter = Json.Str(args, "name");
            if (filter.Length > 128) throw new ArgumentException("UI name filter too long");
            var limit = Math.Min(256, Math.Max(1, (int)Json.Num(args, "limit", 128)));
            var canvases = UnityEngine.Object.FindObjectsOfType<Canvas>();
            var queue = new Queue<Transform>();
            var seen = new HashSet<int>();
            for (var i = 0; i < canvases.Length && i < 32; i++)
            {
                var canvas = canvases[i];
                if (canvas != null && canvas.enabled && canvas.gameObject.activeInHierarchy)
                    queue.Enqueue(canvas.transform);
            }
            var elements = new List<object>();
            var scanned = 0;
            while (queue.Count > 0 && scanned++ < 4096 && elements.Count < limit)
            {
                var t = queue.Dequeue();
                if (t == null || !seen.Add(t.GetInstanceID()) || !t.gameObject.activeInHierarchy) continue;
                if (filter.Length == 0 || t.name.IndexOf(filter, StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    var id = PathOf(t.gameObject);
                    if (!uiNodes.ContainsKey(id))
                    {
                        if (uiNodes.Count >= 256) uiNodes.Remove(uiOrder.Dequeue());
                        uiOrder.Enqueue(id);
                    }
                    var node = new UiNode { Object = t.gameObject, Name = t.name };
                    uiNodes[id] = node;
                    elements.Add(UiElement(id, node));
                }
                for (var c = 0; c < t.childCount && queue.Count + scanned < 4096; c++) queue.Enqueue(t.GetChild(c));
            }
            return new Dictionary<string, object> { ["elements"] = elements, ["scan_limit"] = 4096,
                ["truncated_possible"] = true, ["texts_read"] = false, ["actions_supported"] = false };
        }

        static object UiElement(string id, UiNode node)
        {
            var go = node.Object;
            if (go == null) return new Dictionary<string, object> { ["id"] = id, ["name"] = node.Name,
                ["active"] = false, ["destroyed"] = true, ["source"] = "tracked_ui_destroyed" };
            var alpha = 1.0;
            var ancestor = go.transform;
            for (var a = 0; ancestor != null && a < 32; a++, ancestor = ancestor.parent)
            {
                var group = ancestor.GetComponent<CanvasGroup>();
                if (group == null) continue;
                alpha *= group.alpha;
                if (group.ignoreParentGroups) { ancestor = null; break; }
            }
            return new Dictionary<string, object> { ["id"] = id, ["name"] = go.name,
                ["active"] = go.activeInHierarchy, ["destroyed"] = false, ["ancestor_group_alpha"] = alpha,
                ["alpha_chain_truncated"] = ancestor != null, ["occlusion_tested"] = false,
                ["source"] = "tracked_ugui_hierarchy", ["actions_supported"] = false };
        }
    }
}
