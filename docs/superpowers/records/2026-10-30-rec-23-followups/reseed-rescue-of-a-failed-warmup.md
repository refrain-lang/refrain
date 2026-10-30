# Follow-up: should a successful re-seed rescue a session whose warm-up measurement failed?

**Found:** Task 10's review (defect I2). The reporting half is fixed in this release by Ruling R53. The
question underneath it is a product decision and is deliberately left alone.

## What happens today, after R53

A seeded control measures the person during warm-up. If that measurement cannot fill its window — too
short a warm-up, or an electrode off for part of it — the seed **fails closed**: the control keeps no
measured value, a separate internal flag mutes the output, and the trainee hears nothing for the rest of
the session. That is deliberate, and Task 8 pinned it with a test so the harshness is on the record
rather than discovered in a clinic.

Stage 2 adds block-boundary re-seeding, which re-measures the same control later in the session from a
trailing window of the signal that just ran. So a session now contains, for the first time, a moment
where the engine **could** measure successfully after having failed.

Before R53 it did measure, wrote a real value, reported `status: "seeded"` — and the output stayed muted
anyway, because the mute is keyed on the failure flag rather than on the status. The reported status and
the audible reality disagreed. R53 makes the failure terminal: the status stays `insufficient_samples`,
the control is not re-measured, and the latch stops buffering. The report now tells the truth.

## The question R53 does not answer

**Should the later measurement rescue the session instead?**

The clinical picture is concrete. A trainee's first three minutes are lost to a loose electrode. Under
both the old and the new behaviour they sit through a 40-minute session in silence. Under a rescue rule
they would get nothing for block one and ordinary training from block two onward.

What makes it a real question rather than an obvious yes:

- **Fail-closed exists for a reason.** A protocol that trains against a number nobody measured is worse
  than one that stays quiet, and the quiet is legible — a practitioner notices silence. A session that
  starts working halfway through is less legible, not more.
- **The rescued session is not the protocol that was written.** Block one trained nothing. Whether the
  rest is still the protocol — and still comparable to this person's other sessions — is a question for
  whoever reads the session record, not for the engine.
- **It changes what `insufficient_samples` means.** Today it means "this control never got a number."
  Under a rescue rule it would mean "not yet", and every host that branches on it would need revisiting.
- **Only protocols that declare a re-seed could ever be rescued.** A rescue rule would make the
  fail-closed outcome depend on whether a block elsewhere in the protocol happens to declare `reseed`,
  which is a strange coupling to introduce by accident.

## If it is wanted, the shape it probably takes

A declared opt-in rather than a silent behaviour change — something a protocol author writes on the seed
rule, so the decision is in the protocol where a reader can see it, and absence keeps today's behaviour.
It belongs with the Stage 3 seeding work rather than here, because it needs its own argument about what
the session record should say about a session that started late.

## Reachability: settled, and it is a live-session defect

The original review placed this on the offline replay path. It is not limited to it, and the counting
path is why. Both engines append and count only **finite** samples, and the fire compares
`min(seen, window)` against the window — so the decision is "how many finite samples arrived", not "how
long the phase was". Measured on the shipped primitive: a 3 s warm-up at 256 Hz with 2 s of non-finite
signal gives 256 effective samples against a 512-sample window, and fails closed.

So the ordinary clinical event — a correctly sized warm-up with the electrode off for part of it — is
enough. The test for the fix is written that way: a full-length warm-up with the electrode off for two
of its three seconds, then a perfectly clean block one.

That raises how often the question at the top of this document actually comes up. It is not an exotic
replay artefact. It is a loose electrode during the first three minutes.
