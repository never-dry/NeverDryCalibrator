# Whether the probe is still alive

> Language: English. Audience: anyone deciding whether to trust a calibration, or
> wondering why their probe was refused. Companion to
> [How the calibration works](calibration-method.md), section 5.

## 1. The problem

A cheap soil probe that stops working does not disappear. Home Assistant keeps
the last state it published for as long as the entity exists, so a probe with a
flat battery, a probe out of radio range and a probe whose electrode has corroded
all look identical to a probe sitting in soil that has not changed: one number,
unchanging, indefinitely.

That matters more here than in most places, because this integration pairs that
number with an independent reference and stores the pair. The reference keeps
moving. So a dead probe does not produce *no* data, it produces a steady supply
of pairs that say "the soil dried by a millimetre and the probe did not notice",
and it produces them on a timer for as long as nobody looks. Those pairs then
reach an estimator, and the estimator publishes a line that other software
irrigates a garden with.

The cost is asymmetric, and everything below follows from the asymmetry:

| | refusing a live probe | believing a dead one |
|---|---|---|
| what happens | samples are refused | samples enter the fit |
| how visible | a named reason on a diagnostic entity built to be read | nothing |
| what it costs | the calibration takes longer | the published line is wrong, and stays wrong after the probe is fixed |

So this project prefers to refuse. That is the opposite of the choice the sibling
project NeverDry makes with the same mechanism, for a good reason: there, a
refused probe hands its zone to a weather estimate on a different scale, silently,
every night, which is worse than a day of delay. Same machinery, opposite tuning.
Whenever a threshold below looks stricter than its NeverDry counterpart, this
table is why.

## 2. Two failures, two channels

Two things go wrong, and neither check sees the other's fault.

**The device stops talking.** Battery, radio, coordinator. There is no reading to
inspect, only an age: how long since anything was heard. Section 3.

**The device keeps talking and the electrode stops.** The radio is fine, the
battery reports 80%, the temperature channel follows the day, and the moisture
electrode returns the same number whatever the soil does. No silence exists, so
no measure of silence can find it. What finds it is the reference: a stretch where
the deficit climbs and the index does not. Section 4.

The second is the one NeverDry states as an open residual, and it is the one that
matters most here, for the reason in section 1: NeverDry spends a frozen reading
on one irrigation decision, this project bakes it into a published line.

## 3. The freshness bar

The question is "how long may this device be quiet before the quiet means
something", and the answer cannot be a constant. One probe publishes every thirty
seconds, another twice a day, and any number chosen here calls one of them dead.

### 3.1 The silence is the device's, not the reading's

Home Assistant writes a sensor's state when its value changes. A probe on ground
that is not moving therefore publishes nothing on its moisture entity, and a
reading that stands still is the commonest thing a working soil probe does. Judged
on the moisture entity alone, a live probe is declared dead for reporting the same
number twice.

So the age is taken across **every entity of the probe's device** - moisture,
temperature, battery, link quality, whatever the integration exposes - and the
newest word from any of them is the answer. No entity is inspected for *what* it
says; that it spoke at all is the evidence. `last_reported` and not
`last_updated`, for the same reason: a probe republishing an unchanged value is
alive.

Measured next door, on a real device: a probe published its temperature nineteen
times across one night, every 55 minutes, while its moisture entity published
once. A check reading one channel spent that night calling it dead.

Where the entity belongs to no device - a template sensor, a hand-made helper -
the moisture entity is the only witness there is, and it is used. A registry that
will not answer must never turn every probe into a dead one.

### 3.2 The bar is the device's own longest silence

Not a typical gap, not an average, not a high quantile: the **maximum** ended
silence inside a trailing seven-day window, doubled.

A quantile is the right estimator for a fleet of devices reporting at a steady
rate, and the wrong one for a single device reporting on change. Such a device
produces two populations of gaps: many short ones while its readings move, a few
long ones that are the only evidence of its heartbeat. Any statistic that lets the
first outvote the second sets the bar below the heartbeat. The field case, again
from next door: an evening of 30-second readings filled a forty-sample window, the
0.95 quantile discarded the single 55-minute gap, the bar came out at 33 minutes,
and the probe was declared stopped at 34.

Only **ended** silences count. Quiet that is still going on is the thing being
judged, and admitting it as evidence would make every silence normal by the act of
lasting.

The doubling is slack for the honest silence that has not happened yet. Without
it, the first gap longer than every previous gap marks the probe stale for one
poll and then widens the bar by having happened, which costs a refused sample and
a visible status flap to learn something the device was entitled to do.

### 3.3 Before the bar is believed

Two conditions, both required:

* **A full day of watching.** Not a few hours. A probe's longest honest silence is
  the one that happens while nothing moves, and in a garden that is the night. A
  bar learned from six daylight hours contains only the gaps of a drying soil,
  comes out far below the night gap, and declares the probe dead at two in the
  morning of the first night.
* **Four times the longest silence seen.** A silence of length *L* cannot be known
  to be the longest until the device has had several further chances to beat it.
  Without this, a first day containing one long outage would have that outage as
  its bar, which is the direction that hides a dead probe.

Note what is *not* the condition: a count of observed silences. The bar is held as
a decreasing sequence, which is what makes it cheap, and in that structure every
silence of a regular device is dominated by the next equal one. A device with a
perfectly steady heartbeat therefore holds exactly one entry however long it runs,
and a count-based gate would never open for the commonest device there is. This
was not reasoned out in advance; it was a test failing.

Until both conditions hold, the bar is the configured **Probe considered offline
after** value, which is what this project used before any of this existed. So a
new installation behaves exactly as it used to, and an installation that tuned
that number keeps what it tuned.

### 3.4 The ceiling

Six hours, whatever cadence the probe demonstrates. NeverDry's equivalent backstop
is twenty-four, and the gap is the table in section 1.

It is also the point past which the probe cannot do this job anyway. A calibration
needs the shape of a dry-down resolved into samples; a device managing one reading
every six hours cannot describe that shape whatever its readings say.

### 3.5 Persistence

The bar and the span it was learned over are written to the sample store, because
the other half of the judgement is rebuilt for free: after a restart the ages come
straight from the states. Restoring one half and not the other leaves an age
compared against no bar at all, which means falling back to the configured
timeout - next door, the same omission accepted a probe silent for fifteen hours
as fresh, and only a backstop stood between that and an unwatered garden.

One residual, stated rather than hidden: immediately after a restart every
restored state carries a fresh timestamp, so for one poll a dead device looks
alive here. The window is one cycle wide, and the channel in section 4 does not
depend on timestamps at all, so it still covers that window.

## 4. The stalled electrode

The signature is a reference that travels while the index does not: the deficit
climbs by **half the reservoir** and the index does not move by **half an index
point**.

Four decisions in that sentence.

**A share of the reservoir, not millimetres.** Half of total available water is
about twenty millimetres for a loam at thirty centimetres and eighty for a deep
clay. No absolute figure means the same thing in both.

**Half, and the size of that fraction is what keeps this channel from stealing the
placement diagnostics' work.** A probe in a gravel void or outside the wetted
volume also answers weakly, and that finding belongs to the placement module,
which reports it with advice about where the probe sits. The two have to be
separated by more than measurement error. Half a point across half the reservoir
puts this threshold at one index point per reservoir, against the two points per
cycle below which placement calls a probe unresponsive and the ten points per
reservoir below which it calls one coarse: a factor of two below the weakest probe
placement wants to discuss, and a factor of ten below the weakest one this project
will publish a calibration for. A probe that moves at all lands in placement's
hands; only one that does not move lands here.

**Drying only, and water resets the stretch.** Accumulating the reference's
absolute travel would be wrong in a way that fires constantly: a delivery drops
the deficit by most of the reservoir in one step, so a single irrigation would look
like a full excursion and condemn any probe that had not refreshed inside the poll
interval. Water also arrives as a discontinuity this cannot interpret. So a fall in
the deficit starts a fresh stretch, and only drying accumulates.

**Half a point as the noise floor, not a tuning knob.** Devices publish integers,
so any real movement clears it with room to spare.

### 4.1 Why it is not a placement suspicion

A stalled electrode produces the same signature as a probe that is not in the
water: the index does not move. It does not have the same repair. Every placement
message ends in some form of "consider moving the probe", and a user who digs one
up will find it just as motionless in the new hole, which reads as confirmation.

So the placement module's **no response** suspicion is withheld while the
electrode is stalled - and nothing is put in its place there, because a dead
electrode is not a placement fault. It is named where it belongs: as its own
refusal reason, and as its own repair, which says the device is talking and the
reading is not, and sends the user to the battery and the contacts.

The withholding is recorded in the placement entity's evidence
(`sensing_stalled`), so a placement verdict that raised nothing can be told apart
from one that was silenced.

## 5. The battery

The earliest indicator of the failure both channels above are built for. Below
**5%** the readings stop being evidence and samples are refused with their own
reason.

Five rather than ten or zero: at five percent these devices are within days of
silence and their excitation voltage is already sagging, which shifts the index
without shifting the soil; while a floor high enough to be comfortable would stop
a calibration on a probe that still has a month of honest readings in it. It is
configurable, and zero turns it off.

A reported **exact zero is treated as no information**, not as an empty battery. A
device that is talking cannot truthfully be at zero percent, so zero is a device
reporting its battery badly, and refusing every sample from one of those would
cost the calibration for a bug in a channel the calibration does not use.

## 6. What a user sees

* **Probe online** (binary sensor) - the device-level verdict, with the silence and
  the bar it was judged against side by side. Both or neither: an age without its
  bar invites the reader to compare it with a number they invented. Deliberately
  still `on` for a stalled electrode, because that probe *is* online.
* **Calibration problem** (binary sensor) - carries `sensing_stalled` and the
  travel the index failed to respond to. This is where a consumer looks to find out
  the reading cannot be used.
* **Calibration status** - `probe_offline` for both faults. One status, because a
  consumer of this integration only needs to know the probe is not supplying
  evidence; which way it failed is advice for a human, and advice belongs in a
  repair, where it can carry a sentence.
* **Repairs** - one per fault, each with the numbers behind it.
* **Diagnostics download** - the cadence evidence and the witness state travel with
  the samples, because the first question about a suspect calibration is whether
  the probe was alive while it was collected, and that cannot be reconstructed from
  the samples afterwards.

## 7. What remains wrong

* **A probe whose electrode drifts rather than freezes** is caught by neither
  channel here. It is the drift monitor's job, and a slow drift inside the
  residual budget is caught by nothing at all.
* **The five percent battery floor is argued, not measured.** It comes from how
  these devices behave near the end, not from a bench measurement of index against
  supply voltage on the specific hardware in the ground.
* **Half a reservoir of stalled travel is argued from the resolution budget**, with
  the separation from the placement thresholds as its main constraint. It has not
  been measured against probes whose electrodes are independently known to be dead.
* **The first poll after a restart** believes a dead device, as section 3.5 says.
