// Personal Schedule I fork, Apache-2.0: explicit opt-in for upstream agent/service behavior.
using System;

namespace Autopilot.Playtest.Editor
{
    public static class PersonalForkPolicy
    {
        // Read each time so a domain reload cannot restore a previously connected session by accident.
        public static bool EditorAgentEnabled => Environment.GetEnvironmentVariable("AUTOPILOT_ENABLE_EDITOR_AGENT") == "1";
        public const string Notice = "Schedule I personal fork: the upstream editor agent is disabled. "
            + "Use the local runtime adapter in plugin/schedule-i for the retail game. "
            + "To explicitly enable upstream editor/service features, set AUTOPILOT_ENABLE_EDITOR_AGENT=1 before "
            + "starting Unity. Those features may use external model services and account settings.";
    }
}
