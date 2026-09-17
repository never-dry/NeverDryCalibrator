# How the calibration works

> Language: English. Audience: anyone who wants to know why the number this
> integration publishes should be believed, and where it stops being true.

## 1. What a cheap probe actually measures

A capacitive soil probe does not measure water. It measures the apparent
dielectric permittivity of the material around its electrodes, through the
frequency or the charge time of an oscillator whose fringing field extends a
couple of centimetres into the soil. Water has a permittivity of about 80, soil
minerals about 4, air 1, so the reading moves with water content, strongly and
monotonically. That much is physics and is reliable.

What is not reliable is everything between that fact and the number on the
screen:

* **The scale is arbitrary.** The 0 to 100 index is a linear rescaling of the
  oscillator output between two factory endpoints, typically air and water, or
  air and a wet reference soil. Neither endpoint is your soil.
* **The relation is soil-dependent.** Bulk density, texture, mineralogy and
  organic content all shift the permittivity for the same water content. The
  laboratory relation between permittivity and water content (Topp's equation) is
  itself an empirical average with a known spread across soils.
* **It is sensitive to salinity and temperature.** Dissolved salts, including
  fertiliser, raise the apparent permittivity at the low excitation frequencies
  cheap probes use, and the reading drifts a few tenths of a percent per degree.
* **Installation dominates.** An air gap along the electrode, a stone, or a probe
  that sits above the root zone changes the answer more than any of the above.

The honest summary is that the index is a **monotone, stable-in-the-short-term,
site-specific ordinal signal**. That is exactly enough to calibrate, and nowhere
near enough to read as a percentage.

## 2. What the reference is

The reference is a **soil water deficit** `D`, in millimetres, produced by
another integration from a water balance: evapotranspiration removes water,
irrigation and rain return it, and `D` counts the millimetres missing from field
capacity over the root zone. This is the standard irrigation-scheduling frame of
FAO-56.

The deficit is not a perfect reference. It accumulates model error, it depends on
crop coefficients, and it must be reset by events it can only estimate. It has
two properties the probe lacks, and they are the two that matter here:

1. It is expressed in **physical units on a known reservoir**, so it can be
   converted to a water content without any instrument-specific constant.
2. It has a **known zero**. After enough water and enough drainage time, the root
   zone is at field capacity by definition, and the deficit is zero whatever the
   model believed an hour earlier.

## 3. The bridge between the two

One reservoir, three parameters, and one conversion used everywhere:

```
TAW   = (theta_fc - theta_wp) * Zr * 1000        [mm]
theta = theta_fc - D / (1000 * Zr)               [m3/m3]
AW    = 1 - D / TAW                              [0 to 1]
```

with `theta_fc` field capacity, `theta_wp` wilting point, `Zr` root depth. The
integration holds these in a single object, and every sample carries the
fingerprint of the reservoir it was derived through. Changing the soil re-derives
the history rather than mixing two frames, which is the failure this design
exists to prevent.

## 4. Why cycles, and why five of them

The naive approach is to collect pairs `(R, theta)` and regress. It produces a
confident line from almost no information, for three reasons.

**Samples are not independent.** A probe read every ten minutes during one
dry-down gives hundreds of points that all describe the same slow trajectory. The
effective sample size is closer to the number of *dry-downs* than to the number
of readings, and a regression that counts readings reports a precision it does
not have.

**One dry-down cannot separate the probe from the model.** Within a single
drying curve, a systematic drift of the deficit model, an evapotranspiration
coefficient that is ten percent off, is perfectly confounded with a slope error
of the probe. Only the repetition of independent cycles, with different weather
and different irrigation amounts, breaks that confounding: the model's error
changes sign and magnitude between cycles while the probe's response does not.

**The wet end has to be visited repeatedly.** Field capacity after drainage is
the one soil state whose water content is known without trusting any model. Each
cycle produces one such anchor. Several anchors at different times measure
something no single one can: the **repeatability** of the probe at a known state,
which is the earliest visible symptom of a probe that is drifting, badly
installed, or losing its battery.

So the unit of evidence is the cycle, not the sample. A cycle counts only if it
has a wet anchor, a dry anchor, enough samples, and a deficit span covering at
least thirty percent of the reservoir, because a cycle where the soil never dried
is a flat line dressed up as data. The default is **five complete cycles**, and
it is configurable for sites that have reasons to trade evidence for speed. Note
that five complete cycles means six irrigations: a cycle is only complete once
the next irrigation closes it.

**Rain counts.** A storm that refills the profile is a wetting like any other,
and with a rain gauge configured it closes the cycle it fell on and opens the
next one, exactly as a valve would. This is worth saying plainly because it is
free evidence: in a wet fortnight the campaign keeps advancing instead of waiting
for an irrigation that the water balance has no reason to call for. Each cycle
records which water opened it, which is what makes the rain comparison of section
9 possible.

## 5. Which samples are allowed in

Every admission rule below removes a specific way of learning something false.

| Rule | What it removes |
|---|---|
| No sample while irrigating | The reading during delivery is about water in transit near the electrode. |
| No sample while it rains, or for the drainage window after rain too small to count as a wetting | Rain the reservoir barely notices still wets the centimetres the probe reads. Without a gauge those readings enter the fit as ordinary dry-down points and bias it wet, silently. |
| No sample for the drainage window (3 h default) | The wetting front redistributes for hours. A reading taken at minute twenty describes a profile that will not exist at hour three. |
| Deficit must be fresh (30 min default) | A stale deficit pairs today's probe reading with yesterday's soil. |
| Probe temperature must be fresh (2 h default) | The liveness sentinel. A flat battery keeps publishing the last moisture value forever; the temperature channel is what stops arriving. |
| Soil above 2 degrees | Frozen water is not liquid water dielectrically. The reading collapses and means nothing about available water. |
| At most one sample every 10 minutes | Autocorrelation control, and it keeps the store bounded. |
| Index inside 0 to 100 | A sensor outside that range is not the instrument this integration was told about. |

Refusals are counted and named. A probe that never calibrates can say which rule
it keeps failing, which is almost always the diagnosis.

## 6. The estimator

The model fitted is deliberately the simplest one the data supports:

```
theta_hat = a * R + b + c * (T - T_ref)
```

**Robust, not least squares.** The sample stream contains outliers no admission
rule can catch: a watering can nobody reported, a dog that moved the probe, rain
credited an hour late by the deficit model. Ordinary least squares pulls its line
towards every one of them, and a single bad point at the wet end rotates the
whole calibration. The fit uses the **Theil-Sen** estimator, the median of the
pairwise slopes, which keeps the line the majority of the data agrees with until
roughly twenty-nine percent of the points are corrupted.

**Linear, for now.** The true response of a capacitive probe over a full range is
mildly curved. Over the span between wilting point and field capacity, on one
soil, at one depth, the curvature is smaller than the noise the field imposes,
and a curved model fitted to five cycles buys precision it cannot justify with
degrees of freedom it does not have. The domain is shaped so that a monotone
non-linear form can replace the estimator without touching anything else.

**The temperature term has to earn its place.** It is fitted by a single
backfitting pass, against the probe's own temperature when the device has one and
the ambient sensor otherwise, and it is kept only if it reduces the residual and
stays within a physically plausible magnitude. A large thermal coefficient is not
a thermal effect, it is the fit absorbing a seasonal trend through the only free
parameter available.

**The anchors are kept separate.** The wet and dry anchors of each cycle also
produce a two-point line, which is pure physics and no regression. It is never
published as the calibrated value, because two points cannot tell a good probe
from a stuck one, but it is available as a provisional estimate while the gates
are unmet, and it is what a suspicious user should compare the regression
against.

## 7. When a calibration may be published

| Gate | Default | What it prevents |
|---|---|---|
| Complete cycles | 5 | A calibration built on one wetting event. |
| Samples | 40 | A line placed through a handful of points. |
| Probe range covered | 8 index points | A probe that barely moved was never asked a question. |
| Fit quality, R squared | 0.6 | A probe that is not monotone in this soil: gravel pocket, above the root zone, failing electronics. |
| Residual, share of the available span | 0.25 | A line that fits the trend but not the data. |
| Slope sign | positive | A probe wired or sited backwards. Refused, never inverted, because inverting it would hide a real fault. |

Until all of them pass, the calibrated entity stays **unknown**. This is the
central design decision of the integration: an uncalibrated cheap probe has
nothing to say about the soil, and a plausible-looking number is precisely what
it already produces on its own.

## 8. Staying honest afterwards

A calibration is a statement about one probe, in one soil, at one moment, and all
three expire.

* **Drift.** The residual of the most recent samples against the published line
  is recomputed continuously. When it exceeds twice the residual the fit was
  accepted with, the status becomes `drifting` and the dedicated entity turns on.
  Corroding electrodes, roots growing past the sensing volume and soil settling
  all show up here first.
* **Invalidation.** Turning a calibration knob on the device changes the
  instrument: every earlier sample describes something else, so samples and
  cycles are dropped along with the fit. Changing the soil or the root depth only
  changes the interpretation: the raw readings and deficits survive and are
  re-derived, and the fit has to be earned again on the same data.
* **Extrapolation.** Each published reading says whether it falls outside the
  index range the line was ever tested over.

## 9. Whether the probe is anywhere useful

Everything above assumes the probe is somewhere worth reading. That assumption
does a great deal of work, and it is the one most likely to be false: a probe
pushed in thirty centimetres from an emitter instead of ten may sit outside the
wetted soil entirely, and nothing about its readings looks broken. It publishes a
number all day.

The same cycles that feed the estimator can be read for this, and six signatures
come out of them.

* **The probe does not move.** The reservoir went from full to nearly empty and
  the index travelled a point or two. A probe in wet soil cannot do that. It is
  outside the volume the irrigation wets, above or below the root zone the
  deficit describes, or in a void.
* **The probe moves too little to be worth calibrating.** This one has a number
  behind it rather than a judgement. Over the plant-available range a loam at
  thirty centimetres spans about thirteen percentage points of water content, so
  a probe covering `R` index points over that range makes one index point worth
  `13/R` points of moisture. Below ten points the probe's own quantisation costs
  more than a point of VWC, before any calibration error: a large part of the
  budget in section 10, spent on rounding.
* **The cycles disagree with each other.** Each cycle is clean on its own and
  they do not line up. The probe is reading correctly and reading a spot that is
  not representative, usually because the water arrives differently each time: an
  emitter that clogs and clears, or a probe sitting on the edge of the wetted
  bulb where a small change in flow moves the boundary past it.
* **Field capacity keeps moving.** After drainage the soil is at field capacity
  by definition, so that reading should repeat. When it slides one way cycle
  after cycle, what changed is the probe and the soil against it: settling,
  roots, corrosion.
* **The index jumps while the soil stands still.** Between two readings taken
  minutes apart at an unchanged deficit, soil water does not move. An air gap
  around the shaft does, and so does a failing probe.
* **The probe fills up when it rains and not when the zone is watered.** This one
  needs a rain gauge, and it is the only signature that answers the question the
  other five can only circle around. Rain falls on every square centimetre; a
  dripper wets a bulb around itself. If field capacity reads high after a storm
  and low after an irrigation, the soil is the same in both cases, and what
  differs is whether the water ever reached the probe. The comparison is fair
  because a cycle only opens once the water balance says the profile is nearly
  full, so both readings describe the same soil state. It runs only with at
  least two cycles of each kind, and it is one-sided: a dripper that wets more
  than a shower is a dripper doing its job.

Two things this cannot do, which matter more than the six it can.

It cannot tell a badly placed probe from a wrong water balance. Both produce the
same disagreement between the two series, and no amount of data separates them by
magnitude alone. What separates them is shape: a water balance is smooth by
construction, so a jump at constant deficit, a lag or a hysteresis loop can only
have come from the probe. Every signature above is one of those, or a comparison
of cycles against each other rather than against the model. The rain comparison
is the partial exception and the reason it was worth building: rain and
irrigation are read against the *same* water balance, so a model error affects
both alike and cancels, while a probe outside the wetted bulb does not. Two
probes sharing one deficit source would settle the rest outright, since a
disagreement common to both is the model's and one peculiar to either is that
probe's. That comparison is not built yet.

And it cannot give you a probability. Five cycles are five observations. That is
enough to say "this number is out of range, here it is", and not enough to say
"there is a seventy-three percent chance your probe is misplaced". A calibrated
probability would need a set of probes whose placement was independently known,
which does not exist. So the integration names the suspicion, shows the number
that raised it, and leaves the judgement where the evidence actually is, with the
person who can go and look.

For the same reason none of this blocks a calibration. All but one of these
thresholds are argued rather than measured. An unmeasured threshold is allowed to
advise you and not to overrule you: the gates in section 7 decide what gets
published, these six only describe what they noticed.

## 10. What remains wrong, and by how much

Stated plainly, because a calibration that hides its error budget is worse than
none:

* **Hysteresis.** Wetting and drying curves of real soil do not coincide. The
  samples are dominated by drying, so the calibration is a drying-branch
  calibration, and readings during a wetting phase carry a bias the drainage
  window only partly removes.
* **Salinity.** Fertilisation events raise the apparent permittivity. They appear
  as outliers, which the robust estimator survives, and as a slow seasonal drift,
  which it does not fully.
* **Stratification and root growth.** The probe senses a few centimetres; the
  deficit is defined over the whole root zone. The two agree while the profile
  dries uniformly, and diverge when it does not, typically late in the season.
* **The reference is a model.** Systematic error in the deficit propagates into
  the calibration as a slope error. Multiple cycles reduce it, they do not remove
  it. A site with a soil moisture reference of its own should use that instead.

Expect, on a well-installed probe in reasonably uniform soil, agreement of a few
percentage points of volumetric water content, which is enough to schedule
irrigation and not enough to publish a soil physics paper.

## 11. Checking it yourself

The cheapest independent check is gravimetric. Take a core of known volume near
the probe, weigh it, dry it at 105 degrees to constant mass, weigh again: the
water mass over the core volume is the volumetric water content, and it should
agree with the calibrated entity within the budget above. Failing that, the
comparison worth making is between the published regression and the anchor-only
estimate that appears in the attributes: they are built from different
assumptions, and a large disagreement is a real signal.

## 12. References

* Topp, G. C., Davis, J. L., Annan, A. P. (1980). Electromagnetic determination
  of soil water content. *Water Resources Research* 16(3).
* Allen, R. G., Pereira, L. S., Raes, D., Smith, M. (1998). Crop
  evapotranspiration. *FAO Irrigation and Drainage Paper 56*.
* Theil, H. (1950). A rank-invariant method of linear and polynomial regression
  analysis. *Proceedings KNAW* 53.
* Sen, P. K. (1968). Estimates of the regression coefficient based on Kendall's
  tau. *Journal of the American Statistical Association* 63(324).
* Bogena, H. R., Huisman, J. A., Oberdoerster, C., Vereecken, H. (2007).
  Evaluation of a low-cost soil water content sensor for wireless network
  applications. *Journal of Hydrology* 344(1-2).
* Kizito, F. et al. (2008). Frequency, electrical conductivity and temperature
  analysis of a low-cost capacitance soil moisture sensor. *Journal of Hydrology*
  352(3-4).
* Rosenbaum, U. et al. (2011). Correction of temperature and electrical
  conductivity effects on dielectric permittivity measurements with ECH2O
  sensors. *Vadose Zone Journal* 10(2).
* Thien, S. J. (1979). A flow diagram for teaching texture-by-feel analysis.
  *Journal of Agronomic Education* 8. The field method the README gives for
  choosing the reservoir without a laboratory.
* Saxton, K. E., Rawls, W. J. (2006). Soil water characteristic estimates by
  texture and organic matter for hydrologic solutions. *Soil Science Society of
  America Journal* 70(5). Where field capacity and wilting point per texture
  come from.
