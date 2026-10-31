// Modified for the personal Schedule I fork: Unity 2022.3+ object/physics compatibility.
// The loaded objects of a type. FindObjectsByType<T>(FindObjectsInactive) came
// with 6000.4, which also made the overloads with a FindObjectsSortMode obsolete (a warning); before it only those
// exist. Neither sorts here: no caller depends on the order.

using UnityEngine;

namespace Autopilot.Playtest
{
    public static class Loaded
    {
#if AUTOPILOT_PHYSICS
        public static Vector3 Velocity(Rigidbody body)
        {
#if UNITY_6000_0_OR_NEWER
            return body.linearVelocity;
#else
            return body.velocity;
#endif
        }
#endif
        public static T[] All<T>(FindObjectsInactive inactive) where T : Object
        {
#if UNITY_6000_4_OR_NEWER
            return Object.FindObjectsByType<T>(inactive);
#else
            return Object.FindObjectsByType<T>(inactive, FindObjectsSortMode.None);
#endif
        }
    }
}
