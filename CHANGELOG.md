# Changelog

All notable changes to this integration are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

## 0.4.0 - 2026-09-27

Credit is no longer given to a probe that has stopped working. The mechanism comes
from the sibling project NeverDry, which already judged a probe's silence against
the probe's own habits rather than against a constant, and it arrives here tuned
the other way round and with a second channel NeverDry does not have.

The tuning is reversed because the cost is. There, refusing a live probe hands a
zone to a weather estimate on a different scale, silently, every night, which is
worse than a day of delay. Here, a refusal is named on a diagnostic built to be
read, while believing a dead probe puts its readings into the published line and
leaves them there after the probe is fixed. So this side errs towards refusing:
the ceiling on the freshness bar is six hours where NeverDry's backstop is
twenty-four, and a probe that has not yet demonstrated a cadence is judged by the
configured timeout rather than by the ceiling.

The warning of 0.1.0 and 0.3.0 still stands: the error budget is argued from soil
physics and not yet measured against a probe in the ground, and the placement
thresholds are argued rather than measured. The three thresholds added here are
argued too, and `docs/design/probe-liveness.md` says from what.

### Added

- **Liveness is now the device's, not one channel's.** The silence that decides
  whether a probe may be believed is measured across every entity of the probe's
  device, and no entity is inspected for what it says: that it spoke at all is the
  evidence. Home Assistant writes a sensor's state only when its value changes, so
  a probe on ground that is not moving publishes nothing on its moisture entity
  while its temperature, battery and link quality carry on. Judged on one channel,
  a live probe is declared dead for reporting the same number twice. A probe whose
  entity belongs to no device keeps the temperature channel it always had.
- **The bar a silence is judged against is learned from the probe.** The longest
  quiet the device has come back from inside a trailing week, doubled, capped at
  six hours. A maximum and not a quantile: a device reporting on change produces
  many short gaps while its readings move and a few long ones that are the only
  evidence of its heartbeat, and any statistic that lets the first outvote the
  second sets the bar below the heartbeat. The learned bar takes over only after a
  full day of watching and four times the longest silence seen; until then the
  configured **Probe considered offline after** value applies, exactly as before,
  so nothing changes for a new installation or for one that tuned that number. The
  evidence is persisted, because the other half of the judgement is rebuilt from
  the states for free and restoring half a judgement is worse than restoring none.
- **A second channel for the electrode that stops while the device keeps talking.**
  The radio is fine, the battery reports eighty percent, the temperature follows
  the day, and the moisture reading is the same whatever the soil does. No silence
  exists, so no measure of silence can find it. What finds it is the reference: half
  the reservoir of drying with the index not moving half a point. Samples are
  refused with their own reason, a repair says the device is talking and the reading
  is not, and the fall of a deficit resets the stretch rather than completing it,
  because water arrives as a discontinuity and a delivery would otherwise condemn
  any probe that had not refreshed inside the poll interval.
- **A battery floor**, five percent by default and configurable, zero turning it
  off. The earliest indicator of both failures above, and a sagging supply shifts
  the index without the soil shifting. An exact zero is treated as no information
  rather than as an empty battery: a device that is talking cannot truthfully be at
  zero, so zero is a device reporting its battery badly, and refusing every sample
  from one of those would cost the calibration for a channel the calibration does
  not use.
- `docs/design/probe-liveness.md`: the cost asymmetry that sets every threshold,
  the alternatives rejected and the numbers behind them, and what remains wrong.

### Changed

- **A probe that has stopped measuring is no longer diagnosed as a probe in the
  wrong place.** The two produce the same signature, an index that does not move,
  and they do not have the same repair: every placement message ends in some form
  of "consider moving the probe", and a user who digs one up finds it just as
  motionless in the new hole, which reads as confirmation. The placement module's
  **no response** suspicion is therefore withheld while the electrode is stalled,
  and the reason for withholding it is recorded in the placement entity's evidence
  so a verdict that raised nothing can be told from one that was silenced. Nothing
  takes its place there: a dead electrode is not a placement fault.
- **Probe online** publishes the silence and the bar it was judged against
  together, plus the entities enrolled as witnesses. Both numbers or neither: an
  age without its bar invites the reader to compare it with a number they invented.
  It stays `on` for a stalled electrode, because that probe is online; the stall is
  carried by **Calibration problem**, with the drying travel the index failed to
  respond to.
- The diagnostics download carries the cadence evidence and the witness state
  alongside the samples. The first question about a suspect calibration is whether
  the probe was alive while it was collected, and that cannot be reconstructed from
  the samples afterwards.

## 0.3.1 - 2026-09-18

A repair to what the integration says, not to what it does. It already spoke
English and Italian, and the two files had been in step since the first release,
which is exactly why this went unseen: every check that looked at the
translations came back clean, and none of them could see the text that never
reached a translation file at all.

The warning of 0.1.0 and 0.3.0 still stands: the error budget is argued from soil
physics and not yet measured against a probe in the ground, and the placement
thresholds are argued rather than measured.

### Fixed

- **Nothing the user reads is written in Python any more.** The integration
  already shipped in English and Italian, but three dropdowns and nine error
  messages were spelled out in the code and never reached a translation file, so
  an Italian install met them in English. The soil texture menu, the root depth
  unit and the rain gauge type now take their labels from the translations like
  everything else, and every refusal a service can answer with is a translated
  sentence rather than a literal. The rain gauge dropdown is the plainest case:
  it already carried a translation key and both translations, and an inline
  label passed next to them quietly won, so the menu offered `event` and
  `accumulator` in every language.
- `services.yaml` no longer repeats the name and description of each service and
  field. Home Assistant reads those from the translations, which means the copy
  in the YAML was a second English original that could drift from the translated
  one with nothing to catch it. The file now carries only what has no language:
  which fields exist, whether they are required, their selector and an example.
- `pyproject.toml` declared version 0.1.0 while the integration was at 0.3.0.
  The release pipeline reads the manifest and was never affected.

### Added

- Four guards against the ways a translation goes missing without a sound, and a
  fifth widened from English to every language. A value a dropdown can show with
  no label, an error raised with no message, an entity whose name is absent,
  translatable text creeping back into `services.yaml`, and a referenced
  selector key missing from any of the languages. They parse the modules rather
  than importing them, so they run in the environment that has no Home
  Assistant.

## 0.3.0 - 2026-09-17

Two features, and the second exists because the first made it possible. The
calibration now has a witness for rain, and rain turns out to answer a question
about the probe that no amount of irrigation ever could.

**Validation on real hardware is still in progress**, as in 0.1.0. The error
budget in `docs/design/calibration-method.md` is argued from soil physics and
not yet measured against a probe in the ground, and the placement thresholds are
argued rather than measured. Treat this version as field-testable rather than
proven.

There is no 0.2.0 release. That version number was built and installed for field
testing only, and its contents ship here.

### Added

- **A rain gauge can now be configured**, optionally, for the installation. A
  shower that refills the profile counts as an irrigation: it opens a drainage
  window, closes the dry-down cycle and starts the next one, exactly as a valve
  would. Per-event gauges and running totals are both supported, credited by the
  rule that a fall in a counter is a reset and never precipitation, and the
  gauge can be added later to an entry that is already collecting.
- Rain too small to count as a wetting no longer enters the fit unnoticed.
  Readings taken in the rain, or in the drainage window after it, are refused
  under their own name, `rain_wetting`, instead of being admitted as ordinary
  dry-down points and biasing the slope wet.
- Every cycle now records which water opened it: irrigation, rain, both, or
  unknown. Published on the calibration status entity and in the diagnostics.
- A sixth placement signature, `outside_wetted_bulb`, which needs the gauge and
  answers a question the other five could not: whether the probe is in a bad
  spot or in a spot the dripper never reaches. Rain wets the whole surface, a
  dripper wets a bulb, and a probe that fills up only when it rains is outside
  that bulb. It needs at least two complete cycles of each kind of water and
  stays silent otherwise.
- A per-probe *sheltered from rain* option, for a pot under a roof that the
  gauge on the lawn says nothing about.
- A placement diagnostic. The collected cycles are read for five signs that the
  probe is in the wrong place: no response while the reservoir empties, a range
  too coarse to meet the error budget, cycles that disagree with each other, a
  wet anchor that drifts, and an index that jumps while the soil stands still.
  Published on a new `probe_placement` entity per probe, which carries every
  suspicion with the number behind it, and summarised per probe on the hub.
- A repair for the most severe placement suspicion, one per probe, naming what
  to check and what to do about it, clearing itself when the signature clears.
- The placement diagnostic never blocks a calibration: it sets no status, gates
  nothing and withholds no reading. All but one of its thresholds are argued
  rather than measured, and an unmeasured threshold may advise a user, not
  overrule one. The exception follows from the error budget.
- The rain gauge is treated as a witness that water arrived, never as a
  measurement of how much reached the root zone: heavy rain on dry soil runs
  off, and nothing downstream multiplies by the depth the funnel caught.

## 0.1.0 - 2026-09-16

First release. The calibration is complete and covered by tests; **validation on
real hardware is still in progress**, so treat this version as field-testable
rather than proven. The error budget in `docs/design/calibration-method.md` is
argued from soil physics, not measured on a probe in the ground.

### Added

- Calibration of a cheap capacitive soil probe against a water deficit produced
  by another integration, over repeated irrigation-to-dry-down cycles. Five
  complete cycles by default before anything is published.
- A single integration entry holding every probe of the installation, with
  probes added, edited and removed from its options, one device each.
- Automatic discovery of the companion entities on the probe's device: the
  temperature channel used as liveness sentinel, the battery, and the
  device-side calibration knobs, which are read and monitored but never written
  unless write-back is enabled explicitly.
- Seven entities per probe: calibrated soil moisture, calibration status,
  progress, complete cycles, drift, required depletion, probe online and
  calibration problem. One summary entity on the hub.
- Five services: `calibrate_now`, `reset_calibration`, `mark_field_capacity`,
  `apply_device_offset` and `export_samples`, addressed by probe name.
- A repair that fires when the irrigation regime waters too often for any cycle
  to count, naming the depletion configured, the depletion required, and the
  value to set.
- Metric and imperial units for the deficit and the root depth, English and
  Italian translations, and a diagnostics download.

### Notes

- The calibration domain imports nothing from Home Assistant and is verified by
  a dedicated job that installs pytest alone.
- The integration declares no Python requirements: the robust estimator is
  standard library only.
