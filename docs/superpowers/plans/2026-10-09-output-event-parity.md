# Direct output events from Refrain

Milestone: let a protocol bind `dwell(...)` directly to an `output` event
channel. This is a prerequisite for moving sustained theta cue timing out of
Recorder. It does not change reward dwell or existing output bindings.

1. Add an authored-stream test that checks one event after a continuous hold,
   no repeat within that hold, and a new event after a condition break. Run it
   against Python and Rust.
2. Make both evaluators classify the resolved event stream as an event output,
   evaluate its pulse array, and reset its state on held-output reset.
3. Run the focused test and existing hold-for tests, then commit. Stop here;
   a separate step adds an explicit rearm interval and Recorder cue wiring.

The authored stream establishes software semantics only, not an EEG or
clinical outcome.
