# Changelog

All notable changes to this integration are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Added

- A placement diagnostic. The collected cycles are read for five signs that the
  probe is in the wrong place: no response while the reservoir empties, a range
  too coarse to meet the error budget, cycles that disagree with each other, a
  wet anchor that drifts, and an index that jumps while the soil stands still.
  Published on a new `probe_placement` entity per probe, which carries every
  suspicion with the number behind it, and summarised per probe on the hub.
- A repair for the most severe placement suspicion, one per probe, naming what
  to check and what to do about it, clearing itself when the signature clears.
- The placement diagnostic never blocks a calibration: it sets no status, gates
  nothing and withholds no reading. Four of its five thresholds are argued
  rather than measured, and an unmeasured threshold may advise a user, not
  overrule one. The fifth follows from the error budget.

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
