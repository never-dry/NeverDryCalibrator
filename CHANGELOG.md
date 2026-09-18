# Changelog

All notable changes to this integration are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Changed

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
- Five guards were added for the ways a translation goes missing silently: a
  value a dropdown can show with no label, an error raised with no message, an
  entity whose name is absent, a language that carries fewer keys than the
  source, and translatable text creeping back into `services.yaml`. They parse
  the modules rather than importing them, so they run in the environment that
  has no Home Assistant.
- `pyproject.toml` declared version 0.1.0 while the integration was at 0.3.0.
  The release pipeline reads the manifest and was never affected.

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
