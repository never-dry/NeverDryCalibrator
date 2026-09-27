"""Comparing probes with each other, and refusing to compare the ones nobody paired.

Every test here defends the same boundary from a different side: this module may
only speak about probes a human has declared to share soil. A spread computed
across probes in different beds renders as a number and is noise, which is the
worst kind of wrong output because nothing about it looks wrong.
"""

from __future__ import annotations

from model import Excluded, ProbeReading, spreads_by_group, worst_spread


def _r(name: str, group: str, raw: float | None, **kw) -> ProbeReading:
    """A reading, with the identifier defaulted off the name."""
    return ProbeReading(probe_id=name.lower(), name=name, group=group, raw_percent=raw, **kw)


# ── What may be compared at all ─────────────────────────────────


def test_a_probe_in_no_group_is_compared_with_nothing():
    """The default has to be silence, or the feature lies on every installation.

    A probe nobody paired is not evidence about any other probe. Including it
    would compare two different beds and publish the difference as if it were the
    instrument.
    """
    readings = [_r("Alfa", "", 20.0), _r("Beta", "", 80.0)]
    assert spreads_by_group(readings) == []
    assert worst_spread(spreads_by_group(readings)) is None


def test_groups_are_compared_only_within_themselves():
    """Two pots are two experiments, and mixing them would measure the pots."""
    readings = [
        _r("Alfa", "pot", 40.0),
        _r("Beta", "pot", 43.0),
        _r("Gamma", "bed", 70.0),
        _r("Delta", "bed", 71.0),
    ]
    spreads = {s.group: s.spread for s in spreads_by_group(readings)}
    assert spreads == {"pot": 3.0, "bed": 1.0}


def test_a_group_of_one_has_no_spread_rather_than_a_spread_of_zero():
    """Zero is the most interesting result this can produce, so it must be earned.

    A lone probe agreeing with itself perfectly is the shape of good news, and it
    would appear on any installation that labelled one probe and forgot the rest.
    """
    assert spreads_by_group([_r("Alfa", "pot", 40.0)]) == []


def test_perfect_agreement_is_reported_and_is_not_absence():
    """The other side of the same rule: a real zero must reach the reader."""
    spreads = spreads_by_group([_r("Alfa", "pot", 40.0), _r("Beta", "pot", 40.0)])
    assert len(spreads) == 1
    assert spreads[0].spread == 0.0


# ── Who is left out, and why it is said out loud ────────────────


def test_a_silent_probe_is_excluded_by_name_and_reason():
    """Comparing a fresh reading with a stale one measures the delay, not the probes."""
    readings = [_r("Alfa", "pot", 40.0), _r("Beta", "pot", 90.0, fresh=False), _r("Gamma", "pot", 42.0)]
    spread = spreads_by_group(readings)[0]
    assert spread.spread == 2.0
    assert spread.excluded == {"Beta": Excluded.STALE}
    assert "Beta" not in spread.considered


def test_a_stalled_electrode_does_not_inflate_the_spread():
    """Its reading is not a measurement, so including it would report a known fault."""
    readings = [_r("Alfa", "pot", 40.0), _r("Beta", "pot", 41.0), _r("Gamma", "pot", 12.0, sensing_stalled=True)]
    spread = spreads_by_group(readings)[0]
    assert spread.spread == 1.0
    assert spread.excluded == {"Gamma": Excluded.SENSING_STALLED}


def test_a_missing_reading_is_named_rather_than_dropped():
    """A group that shrank must be distinguishable from one that did not."""
    readings = [_r("Alfa", "pot", 40.0), _r("Beta", "pot", 44.0), _r("Gamma", "pot", None)]
    spread = spreads_by_group(readings)[0]
    assert spread.excluded == {"Gamma": Excluded.NO_READING}
    assert spread.considered == ("Alfa", "Beta")


def test_silence_outranks_a_stall_in_the_reported_reason():
    """Silence is establishable without trusting the reading; the stall verdict is not."""
    reading = _r("Alfa", "pot", 40.0, fresh=False, sensing_stalled=True)
    assert reading.exclusion() is Excluded.STALE


def test_exclusions_can_empty_a_group_without_producing_a_zero():
    """Two probes both unusable is no evidence, not perfect agreement."""
    readings = [_r("Alfa", "pot", None), _r("Beta", "pot", 40.0, fresh=False)]
    assert spreads_by_group(readings) == []


# ── What the numbers say ────────────────────────────────────────


def test_the_spread_is_the_full_range_and_the_deviation_is_the_typical_gap():
    """Two questions, two numbers: the bound, and whether one probe stands apart."""
    readings = [_r("Alfa", "pot", 40.0), _r("Beta", "pot", 41.0), _r("Gamma", "pot", 60.0)]
    spread = spreads_by_group(readings)[0]
    assert spread.spread == 20.0
    assert spread.median == 41.0
    assert spread.deviation == 1.0


def test_one_probe_standing_apart_is_named():
    """Actionable: it says which one to pull out and look at, not merely that one is off."""
    readings = [_r("Alfa", "pot", 40.0), _r("Beta", "pot", 41.0), _r("Gamma", "pot", 60.0)]
    assert spreads_by_group(readings)[0].outlier == "Gamma"


def test_two_probes_never_produce_an_outlier():
    """With two there is no majority to be an outlier from; naming one would be a coin toss."""
    readings = [_r("Alfa", "pot", 20.0), _r("Beta", "pot", 60.0)]
    spread = spreads_by_group(readings)[0]
    assert spread.spread == 40.0
    assert spread.outlier is None


def test_a_group_that_merely_disagrees_has_no_outlier():
    """Three probes evenly spread is a bad batch, not a bad probe, and says so."""
    readings = [_r("Alfa", "pot", 30.0), _r("Beta", "pot", 40.0), _r("Gamma", "pot", 50.0)]
    spread = spreads_by_group(readings)[0]
    assert spread.spread == 20.0
    assert spread.outlier is None


def test_the_worst_group_is_the_one_reported():
    """A bound is only as good as the place it is loosest, so no averaging."""
    readings = [
        _r("Alfa", "pot", 40.0),
        _r("Beta", "pot", 41.0),
        _r("Gamma", "bed", 30.0),
        _r("Delta", "bed", 55.0),
    ]
    worst = worst_spread(spreads_by_group(readings))
    assert worst is not None
    assert worst.group == "bed"
    assert worst.spread == 25.0


def test_the_serialised_form_carries_the_evidence_and_not_only_the_number():
    """An attribute payload a reader can audit without asking anybody."""
    readings = [_r("Alfa", "pot", 40.0), _r("Beta", "pot", 44.0), _r("Gamma", "pot", None)]
    payload = spreads_by_group(readings)[0].to_dict()
    assert payload["spread"] == 4.0
    assert payload["readings"] == {"Alfa": 40.0, "Beta": 44.0}
    assert payload["excluded"] == {"Gamma": "no_reading"}
    assert payload["considered"] == ["Alfa", "Beta"]
