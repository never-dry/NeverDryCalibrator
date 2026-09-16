# NeverDry Calibrator

[![Release](https://img.shields.io/github/v/release/never-dry/NeverDryCalibrator?sort=semver)](https://github.com/never-dry/NeverDryCalibrator/releases)
[![Downloads](https://img.shields.io/github/downloads/never-dry/NeverDryCalibrator/total?label=archive%20downloads)](https://github.com/never-dry/NeverDryCalibrator/releases)
[![Tests](https://github.com/never-dry/NeverDryCalibrator/actions/workflows/tests.yml/badge.svg)](https://github.com/never-dry/NeverDryCalibrator/actions/workflows/tests.yml)
[![HACS](https://img.shields.io/badge/HACS-custom%20repository-41BDF5)](https://hacs.xyz/docs/faq/custom_repositories/)

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

> **Status: field validation in progress.** The calibration domain is covered by
> 152 tests and reproduces synthetic probes to within a fraction of a percent of
> water content, but no result yet comes from a real probe in real soil. The
> error budget in the method document is argued, not measured, and version 0.1.0
> should be read as field-testable rather than proven.

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

In HACS, three dot menu, **Custom repositories**, paste
`https://github.com/never-dry/NeverDryCalibrator` with category *Integration*,
then download it from the card that appears and restart Home Assistant. HACS
fetches the `neverdry_calibrator.zip` attached to the latest release, which is
also what the download badge above counts.

Manual installation works too: copy `custom_components/neverdry_calibrator` into
your `config/custom_components/` and restart. You then update it by hand.

After the restart, add it from **Settings, Devices and services, Add
integration, NeverDry Calibrator**.

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

## Where to put the probe

Two things decide whether a cheap probe is worth calibrating at all, and neither
is the probe. The first is where it sits. The calibration needs the probe to
travel its range every cycle: to reach field capacity after irrigation, and to
dry appreciably before the next one. A probe that never does both cannot be
calibrated by any method, and this integration will say so rather than pretend.

### Distance from the emitter

Under drip irrigation the water forms a **wetted bulb**: it sinks and spreads,
the finer the soil the wider. Put the probe at roughly **half the wetted
radius**, never directly under the emitter.

| Soil | Typical bulb radius | Distance from the emitter |
|---|---|---|
| Sandy, drains fast | 10 to 15 cm | 8 to 12 cm |
| Loam | 20 to 30 cm | 12 to 20 cm |
| Clay, holds water | 30 to 45 cm | 20 to 30 cm |

The two extremes fail in opposite and equally useless ways, and both are visible
in the data this integration collects:

* **Directly under the emitter** the probe sits in saturated water in transit. It
  reads at the top of its scale during and after every irrigation and barely
  falls, so the cycle has no span and the `raw_span` gate never passes.
* **Outside the bulb** the probe never wets. It reads low and flat, irrigation
  does not reach it, and it never produces a wet anchor.

### Depth

Bury the sensing blade completely, with the sensitive part centred around **a
third to a half of the root depth** you configured. For turf or a shrub rooting
at 30 cm, that is 10 to 15 cm down.

### Finding the radius on your own soil

Tables are a starting point; the bulb also depends on flow rate and run time. Run
one normal irrigation, wait two or three hours for the front to redistribute,
then dig a small exploratory hole radially outward from the emitter, twenty or
thirty centimetres away from where the probe will go. The wet front is a clear
colour change. Place the probe at half that radius, in undisturbed soil, and fill
the exploratory hole back in.

## How to insert it

The second thing that decides everything, and the one most often got wrong.

**Push it straight into undisturbed soil, in one movement, without rocking it.**
Rocking opens a funnel around the blade, and that funnel collects water at every
irrigation: the probe then spikes and falls back, measuring the event instead of
the soil.

If the ground is too hard, there are two honest ways in:

1. **Wet and wait.** One irrigation, an hour, and the blade goes in by itself.
   This is the better one.
2. **Cut a pilot slit of the same width** with a thin knife, as deep as the blade
   and no deeper, insert the probe and **press the soil at the sides**. You are
   closing a slit against the blade, not filling a hole.

**Do not dig a hole and backfill it.** For a blade pushed in from the surface it
is the worst option available, for two reasons. Replaced soil has a different
bulk density from the soil around it, and the relation between permittivity and
water content depends on that density, so the probe would be calibrated against
the backfill rather than against the bed. And the backfill becomes a preferential
path: water runs down the disturbed column instead of redistributing, so the
probe sees the irrigation arrive all at once and disappear, which is again the
event and not the store.

Air is the enemy: a film along the electrode costs more than twenty centimetres
of position. Never hammer the probe in, and if you hit a stone move a few
centimetres rather than forcing it, because forcing bends the blade and opens a
void exactly where it measures. Avoid the lowest point of the bed, where water
stands.

**Then leave it there.** Every removal and reinsertion is a new probe in new
soil, and the collected history has to be thrown away with
`neverdry_calibrator.reset_calibration`. Half an hour spent choosing the spot is
worth more than three moves in a month.

## Which soil should I pick?

The texture you choose sets the reservoir: field capacity, wilting point, and
therefore how many millimetres of deficit correspond to one point of water
content. You do not need a laboratory, you need two minutes and your hands.

### The ribbon test

Take a lump the size of a walnut **at the depth the probe sits**, ten to fifteen
centimetres, not from the surface. Remove stones and roots, wet it a little at a
time and knead it to the consistency of modelling clay: moist and mouldable, not
muddy.

1. **Roll a ball.** If it will not hold together, the soil is sandy and you are
   done.
2. **Squeeze the paste between thumb and forefinger**, pushing it upward into a
   ribbon that overhangs the finger. Let it extend under its own weight until it
   breaks, and measure how long it got.

| Ribbon before it breaks | Texture | What to select |
|---|---|---|
| None, it crumbles | Sand | `Sandy` |
| Under 2.5 cm, weak | Sandy loam | `Sandy` or `Automatic` |
| 2.5 to 5 cm | Loam | `Automatic` |
| Over 5 cm, strong and flexible | Clay | `Clay` |

3. **Check by feel**, rubbing the wet paste between your fingers: **gritty and
   scratchy** means sand, **smooth like flour or talc** means silt, **sticky,
   clinging to your fingers** means clay. A true clay makes a long ribbon *and*
   sticks.

### The jar test, if you want a number

Fill a glass jar one third with soil, add water almost to the top and a drop of
dish soap, shake for a minute and leave it still. Sand settles in about **one
minute**, silt in about **two hours**, clay takes **one to two days**. Measure
the three layers with a ruler: more than 40% clay is a clay soil, around 20% with
sand and silt in balance is a loam.

### Signs you have already seen

Water standing in puddles long after a storm, cracks opening in summer, heavy
clods sticking to your boots and spade: clay. Water vanishing in minutes, a spade
going in easily even when dry: not clay.

### Two warnings

Beds get amended over the years. If a hydrangea was planted with peat and acidic
compost, the soil around its roots can be far lighter than the native clay of the
same garden, and the probe reads that. Different beds can genuinely differ, so
test each one rather than deciding once for the whole garden.

### When in doubt, pick `Automatic` in both integrations

Getting the texture wrong is a small error. Declaring **two different soils**,
one in the water balance and another here, is the error this project is built to
make impossible: the deficit would be computed against one reservoir and read
against another. With the same choice on both sides the system stays
self-consistent, and the residual error shifts the absolute scale without
spoiling irrigation decisions, which follow the deficit anyway.

Correcting it later costs nothing: changing the soil re-derives the samples
already collected against the new reservoir and only the fit has to be earned
again, on the same data. That is the one case where `reset_calibration` is *not*
needed.

### Sources

* Thien, S. J. (1979). A flow diagram for teaching texture-by-feel analysis.
  *Journal of Agronomic Education* 8, 54-55. The ribbon flowchart above, and the
  method the USDA distributes as *Guide to Texture by Feel*.
* Soil Science Division Staff (2017). *Soil Survey Manual*, USDA Handbook 18.
  Texture classes and field description.
* Kettler, T. A., Doran, J. W., Gilbert, T. L. (2001). Simplified method for soil
  particle-size determination to accompany soil-quality analyses. *Soil Science
  Society of America Journal* 65(3), 849-852. The sedimentation test.
* Schwankl, L., Hanson, B., Prichard, T. (2008). *Maintaining Microirrigation
  Systems*, University of California ANR Publication 21637, and FAO (2002),
  *Localized irrigation systems*: wetted bulb geometry under drip, and why the
  sensing point belongs inside it but away from the emitter.
* Saxton, K. E., Rawls, W. J. (2006). Soil water characteristic estimates by
  texture and organic matter for hydrologic solutions. *Soil Science Society of
  America Journal* 70(5), 1569-1578. Where water-holding values per texture come
  from; the presets in this integration mirror the soil table of NeverDry, so
  that both describe the same reservoir.

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

Releases are cut by pushing a `v*` tag: the workflow refuses a red build, checks
that the manifest version matches the tag, packages the integration directory
into `neverdry_calibrator.zip` and publishes it with the changelog section as
release notes.

```bash
python3 scripts/download_counts.py          # per release, with the totals
python3 scripts/download_counts.py --json   # the same, machine readable
```

GitHub counts downloads of release **assets** only, never of the source archives
it generates for a tag, which is why the workflow attaches an explicit zip. Read
the numbers for what they are: HACS fetches that archive again at every update,
so the total measures activity rather than people, and none of these figures is
an install count.

## License

MIT. See [LICENSE](LICENSE).
