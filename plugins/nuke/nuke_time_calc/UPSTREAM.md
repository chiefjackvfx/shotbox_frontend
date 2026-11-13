# Upstream provenance

- Source: `https://github.com/AlesUshakou/TimeTrackerForNuke.git`
- Inspected commit: `c15f220568e4925f9f89c11050fcf81d7f9cdc2a`
- Inspected implementation: `TimeTracker/src/timeTracker.py`,
  `TimeTracker/src/settings.py`, and `TimeTracker/init.py`.
- Upstream uses the MIT license; consult the source repository for its notice.

This is a small **new experimental implementation**, informed by upstream's
callback-based activity estimator, 20-second idle policy, Qt focus/timer wiring,
and lifecycle/render hooks. It is not a renamed copy of the upstream plugin and
is not a claim that upstream's timing or persistence behavior was retained.

Replaced wall-clock duration accounting with injected monotonic clocks, settled
state transitions, exact idle overlap, explicit context, and plain interval
records delivered to an injected sink. Removed the upstream UI, settings UI,
encryption/obfuscation, reader, file persistence, guessed shot-name parsing,
and hidden `ales` activity override entirely. There are no dependencies,
screenshots, nested repository, menu startup files, or vendored Qt/Nuke modules.
