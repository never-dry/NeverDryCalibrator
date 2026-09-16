# NeverDry Calibrator

Turn a cheap capacitive soil probe into a soil moisture sensor you can actually
use, by calibrating it in place against a water deficit computed by a scientific
model.

A probe that costs a few euros publishes an index from 0 to 100. That index is
monotone in soil water and otherwise arbitrary: 40 on one probe in sandy soil and
40 on the same model pushed into clay are different amounts of water, and neither
is 40 percent of anything. This integration pairs such a probe with a soil water
deficit in millimetres produced by another integration, watches several
irrigation-to-dry-down cycles, and learns the map between the two.

Until it has enough evidence, it publishes nothing. That is the point.

## What you need

| Ingredient | Why |
|---|---|
| A soil probe publishing 0 to 100 | The instrument being calibrated. |
| A water deficit sensor in mm or inches | The reference. Any water balance model will do, for example the deficit produced by [NeverDry](https://github.com/never-dry/NeverDry). |
| Several irrigation cycles | Five by default. One wetting event is the most common way to get a confident and wrong calibration. |

Optional, and used when present: an ambient temperature sensor (frost guard and
fallback covariate), and an irrigation valve or switch (sharpens the timing of
the drainage window).

Everything else is discovered. From the probe's own device the integration picks
up its temperature channel, its battery, and any calibration knobs the device
exposes.

## Installation

HACS, as a custom repository of category *Integration*, then
**Settings, Devices and services, Add integration, NeverDry Calibrator**. Or copy
`custom_components/neverdry_calibrator` into your `config/custom_components/` and
restart.

## One integration, all your probes

The installation is a **single integration entry**, the way NeverDry is a single
entry holding its zones. Probes are added, edited and removed from its options,
so there is one place to look and one place to change things. Each probe still
gets its own device, its own entities and its own calibration history.

1. **First probe.** Pick the raw probe sensor and the deficit sensor, and give
   the probe a name: it names the device and its entities. The integration
   refuses a probe that does not publish 0 to 100, and a deficit in a unit that
   is not a water depth.
2. **Soil reservoir.** Pick a texture preset and a root depth, or type your own
   field capacity and wilting point. If the deficit comes from NeverDry, use the
   same soil you configured there for that zone: the two numbers have to describe
   the same reservoir or they cannot be compared.
3. **Confirm.** The screen lists what was found on the probe's device.
4. **Another probe?** Tick the box to configure the next one straight away, or
   finish and add the rest later from **Options, Add a probe**.

Options also hold **Edit a probe** (renaming keeps its history), **Remove a
probe** (asks first, because it throws away the collected cycles), and the two
tuning screens that apply to every probe of the installation.

## What you get

One device per probe, named as you named it, plus one hub device for the
installation itself.

| Entity | What it says |
|---|---|
| `sensor.neverdry_calibrator_calibrated_probes` | On the hub: how many probes are calibrated, with the status and progress of each in its attributes. |
| `sensor.<probe>_calibrated_soil_moisture` | Volumetric water content in percent. Unknown until the calibration is earned. Attributes carry the available water share, the raw index, and whether the reading is an extrapolation. |
| `sensor.<probe>_calibration_status` | `collecting`, `calibrated`, `drifting`, `probe_offline` or `invalidated`, with the full evidence in its attributes: which gates are missing, how many cycles are complete, why the last observations were refused. |
| `sensor.<probe>_calibration_progress` | Percentage towards the least satisfied gate. |
| `sensor.<probe>_complete_cycles` | Cycles that counted as evidence, with the last ten described in the attributes. |
| `sensor.<probe>_required_depletion` | How dry the soil must get between two irrigations for a cycle to count, in millimetres, with the other derived thresholds and the irrigation threshold of the zone in its attributes. |
| `sensor.<probe>_calibration_drift` | Recent disagreement between the published line and the reference. The early warning that a probe is ageing. |
| `binary_sensor.<probe>_probe_online` | The temperature sentinel: a flat battery keeps publishing the last moisture value forever, but the temperature stops arriving. |
| `binary_sensor.<probe>_calibration_problem` | On when the calibration is drifting, invalidated, or the probe is offline. |

## Services

Probes are addressed by name, the one you typed and the one the device shows.

| Service | Probe | Use |
|---|---|---|
| `neverdry_calibrator.calibrate_now` | optional | Refit immediately from what has been collected. Does not lower the gates. Omit the probe to refit all of them. |
| `neverdry_calibrator.reset_calibration` | required | Forget everything about one probe. Use after moving it. Never acts on every probe at once. |
| `neverdry_calibrator.mark_field_capacity` | required | Declare that the soil around one probe is at field capacity right now, after a thorough watering and a few hours of drainage. |
| `neverdry_calibrator.apply_device_offset` | required | Write an offset into that probe's own calibration entity. Opt-in, and it drops every collected sample. |
| `neverdry_calibrator.export_samples` | optional | Return every sample, cycle and fit, for inspection or a bug report. |

## How the calibration works

Short version: the deficit says how many millimetres of water the root zone is
missing, the soil reservoir turns that into a water content, and the probe index
is regressed against it with a robust estimator. Each irrigation cycle
contributes two anchors that need no regression at all, the wet one at field
capacity after drainage and the dry one at the deepest deficit reached. Five
complete cycles, forty samples and a real span of the probe range are required
before anything is published.

Long version, with the physics, the failure modes and the reasoning behind every
threshold: [`docs/design/calibration-method.md`](docs/design/calibration-method.md).
The objects that implement it are described in
[`docs/design/domain-model.md`](docs/design/domain-model.md).

## Will your irrigation regime ever calibrate?

The gates are expressed as fractions of the soil reservoir, because ten
millimetres is a whole reservoir on sand at fifteen centimetres of root depth and
a rounding error on clay at a metre. Fractions are right and unreadable, so the
integration publishes them in millimetres too:

| Derived threshold | Fraction | Meaning |
|---|---|---|
| Irrigation drop | 20% of available water | A fall of the deficit this large is read as water reaching the soil. |
| Wet anchor | 10% | A settled reading below this deficit is taken as field capacity. |
| **Required depletion** | **30%** | **The deficit span a cycle must cover before it counts as evidence.** |

The last one is the constraint that decides whether the calibration can ever
finish. If the irrigation waters at a smaller depletion than that, the soil never
dries enough between two cycles, no cycle ever counts, and the calibration would
sit at `collecting` forever.

The integration will not let that happen silently. It looks for the irrigation
threshold published alongside the deficit, compares the two, and raises a repair
naming both numbers and the value to set. On clay at 30 cm of root depth, for
instance, the reservoir holds 42 mm, a cycle must cover 12.6 mm, and a zone set
to irrigate at 3 mm of depletion can never produce one.

## Limits worth knowing

* The calibration describes **one point of measurement at one depth**. Moving the
  probe, even by a metre, invalidates it, and nothing can detect that for you:
  call `reset_calibration` when you move one.
* Wetting and drying are not symmetric in real soil. The residual left by that
  hysteresis is real and is reported in the drift sensor rather than hidden.
* A probe sitting above the root zone, in a gravel pocket, or in a pot that dries
  from the outside in, will never pass the fit-quality gate. That is a correct
  answer about the installation, not a failure of the integration.
* Writing an offset back into the device can remove a bias, never a wrong gain.
  The service refuses to do it when the fitted gain is too far off.

## Development

```bash
pip install -r requirements_test.txt
ruff check . && ruff format --check .
pytest -q
```

The calibration domain in `custom_components/neverdry_calibrator/model/` imports
nothing from Home Assistant and is tested without it. A CI job installs pytest
alone to keep that true.

## License

MIT. See [LICENSE](LICENSE).
