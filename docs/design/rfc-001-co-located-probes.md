# RFC-001: co-located probes, and calibrating against a probe instead of a model

> Status: **draft, nothing implemented**. Language: English.
> Companion to [How the calibration works](calibration-method.md).
> Comments: [Discussions](https://github.com/never-dry/NeverDryCalibrator/discussions).

## 1. The problem this is about

Everything this integration publishes rests on one substitution: it cannot measure
the water in the soil, so it uses a modelled water deficit as a stand-in and
teaches the probe against that. Section 2 of the method document says so plainly,
and the consequence is stated in the error budget: **the calibration can only be
as good as the deficit it was taught against**, and the deficit is a model.

Three costs follow, and all three are paid by every installation:

* **Weeks.** The unit of evidence is an irrigation cycle because the deficit only
  moves in ways worth learning from across a full wetting and drying. Five cycles
  is about three weeks on a garden bed, and there is no honest way to shorten it.
* **A reservoir model in the middle.** The deficit is in millimetres and the probe
  reads an index, so the two are bridged by a soil profile: texture, root depth,
  field capacity, wilting point. Every one of those is a number the user supplied
  and nobody checked. A wrong texture is a wrong calibration that passes every
  gate.
* **An error floor nobody has measured.** The declared budget is argued from
  physics. Nothing in the project has ever established how much of the residual is
  the soil, how much is the model, and how much is simply this particular piece of
  hardware.

This RFC is about a cheap experiment that attacks the third, and a design change
that would attack all three.

## 2. Part A: several cheap probes in one place

**The experiment.** Put N identical cheap probes into the same soil, close
together, at the same depth. Read them at the same instant. Look at the spread.

Ten minutes gives one number: **how much two nominally identical instruments
disagree about the same water**. That is one point on the curve and not a
calibration, and it is still worth having, for a reason that is not obvious: it
puts a **measured floor** under the declared error budget. If four co-located
probes disagree by the equivalent of X percent volumetric water content, then no
calibration of any single one of them can honestly promise better than about X,
whatever the fit statistics say. One argued number becomes one measured bound, in
an afternoon.

It also separates two causes that the placement diagnostic currently cannot. A
probe that reads oddly is either a bad instrument or a bad position, and today
there is no way to tell. Co-located probes share the position by construction, so
whatever spread remains is the instrument.

**What it does not give.** A single reading is one moisture level. Two probes can
agree at field capacity and diverge badly when dry, which is exactly the failure a
one-point test cannot see. Extending the same setup through a full dry-down costs
days instead of minutes and yields the whole transfer function between the probes,
which is worth far more.

**The confound that has to be designed out.** In a garden bed, probes ten
centimetres apart are in measurably different soil: the spread you measure is
instrument variation *plus* soil heterogeneity, and one reading cannot separate
them. Two ways out, in increasing order of cost and conclusiveness:

1. **Swap and repeat.** Move each probe to another probe's hole and read again.
   What follows the probe is the instrument; what stays with the hole is the soil.
2. **A bucket of homogenised soil.** Mix it, insert every probe, let it dry over
   days. Heterogeneity is close to eliminated and the spread is the instrument,
   across the whole range rather than at one point. This is the version worth
   publishing.

## 3. Part B: calibrate against a probe, not against a model

This is the larger idea, and it changes the shape of the product rather than
adding a test to it.

If one probe in the cluster is **already trusted** to report volumetric water
content, then the cheap probes beside it can be taught against *that*, and the
water balance drops out of the loop entirely.

The consequences are not small:

* **The reservoir model disappears from the fit.** Soil texture, root depth, field
  capacity and wilting point exist only to convert a deficit in millimetres into a
  water content. A probe that already reports water content removes the conversion
  and with it a whole class of error: the user who picked the wrong texture from
  the dropdown.
* **Cycles stop being the unit of evidence.** They exist because the deficit is
  slow, indirect and only interpretable between a wetting and a drying. Two probes
  in the same soil can be compared at any instant, so what the fit needs is not
  five cycles but **coverage of the range**: enough samples spread widely enough
  across the probe's index. One dry-down gives that.
* **Weeks become days**, and in a bucket, less.
* **The drainage window, the wet anchor and the rain machinery all become
  irrelevant** in this mode. They are all devices for making a slow model
  interpretable.

**Where the trust comes from.** Two sources, and they are very different:

* *A reference instrument.* A laboratory-grade probe, or a gravimetric sample
  (core of known volume, dried at 105 degrees). Accurate and either expensive or
  laborious.
* *A probe this integration already calibrated*, the slow way, against the water
  balance. This is the interesting one, because it costs nothing anybody does not
  already have. Calibrate one probe properly over three weeks, then use it as the
  local reference for every other probe in the same garden, in an afternoon each.

**And the danger in exactly that.** A calibration derived from a calibrated probe
is second-generation: it inherits the reference's error and adds its own. A third
generation would inherit both. This is how a measurement chain quietly becomes
fiction, and any implementation has to refuse to let it happen silently:

* a fit derived from a proxy reference must be **marked as such**, with the
  provenance of its reference recorded alongside it;
* a probe calibrated from a proxy must **never be usable as a proxy itself**,
  which makes the chain exactly one link long by construction;
* the error budget of a proxy calibration must **compose** the reference's
  declared error rather than reporting only its own residual, or the derived
  calibration will look better than the thing it was derived from, which is
  impossible.

The honesty rule extends rather than bends: a calibration is never better than its
reference, and the published figure has to say which reference it had.

## 4. What this would mean for the integration

Sketch only; nothing here is decided.

A second reference mode, chosen per probe:

```
reference_source = water_balance   (today, the only one)
                 | reference_probe (this RFC)
```

In `reference_probe` mode the samples are pairs of (raw index, reference VWC)
taken at the same instant, the soil profile is not consulted for the fit, the
cycle tracker is not used, and the publication gates change from counting cycles
to demanding coverage of the index range plus the usual monotonicity and residual
bounds. The liveness rules of
[probe-liveness.md](probe-liveness.md) apply to **both** probes, and a reference
that has gone quiet or whose electrode has stalled must invalidate the fit it is
supporting rather than merely stop extending it.

Most of the existing machinery is untouched: admission, the estimator, the drift
monitor, the placement diagnostic.

## 5. Open questions

1. **How big is the spread, really?** Part A answers this and costs an afternoon.
   Nothing in Part B is worth building if co-located cheap probes turn out to agree
   to within a fraction of a percent, and nothing else is worth building if they
   disagree by twenty.
2. **Does the spread hold across the range**, or do probes converge when wet and
   diverge when dry? This decides whether a one-point proxy transfer is ever
   defensible or whether the full dry-down is mandatory.
3. **How close is close enough?** There is a distance below which two probes
   disturb each other's fringing field and above which they are in different soil.
   Nobody here has measured either bound.
4. **Does a proxy calibration beat a slow one?** A second-generation fit against a
   good local reference may well be more accurate than a first-generation fit
   against a modelled deficit with a guessed soil texture. It may also be worse.
   This is an empirical question and it is the one that decides whether Part B is
   a feature or a footnote.
5. **What does the user interface call this** without inviting the chain that
   section 3 forbids?

## 6. How to help

Question 1 needs no permission, no code and no hardware anybody interested does
not already own: several cheap probes, one pot of soil, one afternoon. Results,
including boring ones, are wanted in
[Discussions](https://github.com/never-dry/NeverDryCalibrator/discussions/new?category=show-and-tell).
Say which probes, which soil, how far apart, at what depth, and what each of them
read.

A result showing that the probes agree closely would be the most useful outcome
of all, and would retire most of this document.
