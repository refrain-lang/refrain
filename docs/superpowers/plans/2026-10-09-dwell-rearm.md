# Once-per-hold events with rearm

Milestone: add `dwell_rearm(condition, duration, rearm)` as a generic Refrain
event primitive. It emits on the first clean dwell, then needs `rearm` of
continuous false input before another emission. A brief break cannot retrigger
the event. External held-output reset invalidates the active streak.

Write one authored-stream test covering a qualified hold, brief break,
long enough break, and a reset. Implement the same state machine in Python
and Rust, bake both sample counts in IR-JSON, and compare emitted event times
from a shared fixture. Stop when focused Python and Rust tests pass. This is
software semantics; it does not establish a clinical effect.
