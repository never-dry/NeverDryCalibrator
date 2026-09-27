#!/usr/bin/env python3
"""Watch several probes in one pot of soil and report how much they disagree.

This is the cheapest useful experiment this project has, and it needs no code in
Home Assistant and no calibration: put N identical probes into the same soil,
close together, at the same depth, and read them together for a while.

**Why the number matters.** Probes that share soil also share the weather and
the water balance, so whatever they disagree about is neither of those: it is the
instrument. And it is a bound rather than a curiosity. If two nominally identical
probes in one pot differ by X index points, then no calibration of either one can
honestly claim to resolve better than about X, whatever its fit statistics say.
The error budget this project declares has never had a measured floor under it;
this puts one there in an afternoon.

**What it does not give.** One reading is one moisture level. Two probes can
agree at field capacity and diverge badly when dry, which is exactly the failure
a short run cannot see. Leave it running through a full dry-down (``--hours 72``
or more) and the CSV holds the whole transfer function instead of one point.

**The confound to design out.** In a garden bed, probes ten centimetres apart sit
in measurably different soil, so the spread is instrument *plus* heterogeneity
and one run cannot separate them. Either swap the probes between holes and run
again (what follows the probe is the instrument, what stays with the hole is the
soil), or use a bucket of mixed soil, which is the version worth publishing.

Readings are compared only when they are of comparable age. A probe that has not
reported for an hour is not disagreeing with the others, it is late, and counting
it would measure the delay.

Usage:
    export HA_URL=http://homeassistant.local:8123
    export HA_TOKEN=...            # long-lived access token, read and never printed
    python3 scripts/probe_spread.py sensor.probe_a sensor.probe_b sensor.probe_c
    python3 scripts/probe_spread.py --hours 72 --every 300 --csv pot.csv sensor.a sensor.b

Results, including boring ones, are wanted:
https://github.com/never-dry/NeverDryCalibrator/discussions
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime

#: A reading older than this is not compared with the others. Five minutes is
#: long enough for any probe that can inform an irrigation decision and short
#: enough that the soil has not moved underneath the comparison.
DEFAULT_MAX_AGE_S = 300.0


def _die(message: str) -> None:
    """Stop with a message on stderr and a non-zero status."""
    print(message, file=sys.stderr)
    raise SystemExit(2)


def fetch_states(base_url: str, token: str, entity_ids: list[str]) -> dict[str, dict]:
    """Current state of each entity, as Home Assistant reports it.

    One request per entity rather than one for everything: the bulk endpoint
    returns every entity in the installation, which on a real instance is
    megabytes to find five values.
    """
    out: dict[str, dict] = {}
    for entity_id in entity_ids:
        request = urllib.request.Request(  # noqa: S310 - scheme comes from the user's own HA_URL
            f"{base_url.rstrip('/')}/api/states/{entity_id}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
                out[entity_id] = json.loads(response.read().decode())
        except urllib.error.HTTPError as error:
            if error.code == 404:
                _die(f"{entity_id}: no such entity on that instance")
            if error.code == 401:
                _die("HA_TOKEN was refused. Is it a long-lived access token, and still valid?")
            _die(f"{entity_id}: HTTP {error.code}")
        except OSError as error:
            _die(f"{base_url}: {error}")
    return out


def _age_seconds(state: dict, now: datetime) -> float | None:
    """How long ago this state was last reported, in seconds."""
    stamp = state.get("last_reported") or state.get("last_updated")
    if not stamp:
        return None
    try:
        reported = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, (now - reported).total_seconds())


def read_round(base_url: str, token: str, entity_ids: list[str], max_age_s: float) -> dict:
    """One simultaneous look at every probe, with the ones too old set aside."""
    now = datetime.now(UTC)
    states = fetch_states(base_url, token, entity_ids)

    values: dict[str, float] = {}
    skipped: dict[str, str] = {}
    for entity_id, state in states.items():
        raw = state.get("state")
        if raw in (None, "unknown", "unavailable"):
            skipped[entity_id] = str(raw)
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            skipped[entity_id] = "not a number"
            continue
        age = _age_seconds(state, now)
        if age is not None and age > max_age_s:
            skipped[entity_id] = f"stale ({age / 60:.0f} min)"
            continue
        values[entity_id] = value

    result: dict = {"at": now.isoformat(timespec="seconds"), "values": values, "skipped": skipped}
    if len(values) >= 2:
        numbers = list(values.values())
        median = statistics.median(numbers)
        result["spread"] = max(numbers) - min(numbers)
        result["median"] = median
        result["deviation"] = statistics.median([abs(v - median) for v in numbers])
    return result


def _print_round(result: dict, entity_ids: list[str]) -> None:
    """One line per look, with the spread last because it is the answer."""
    cells = []
    for entity_id in entity_ids:
        short = entity_id.split(".", 1)[-1][:14]
        if entity_id in result["values"]:
            cells.append(f"{short}={result['values'][entity_id]:>5.1f}")
        else:
            cells.append(f"{short}={result['skipped'].get(entity_id, '?'):>5}")
    spread = f"spread={result['spread']:5.1f}" if "spread" in result else "spread=    -"
    print(f"{result['at'][11:19]}  {'  '.join(cells)}   {spread}")


def summarise(rounds: list[dict]) -> None:
    """What the run as a whole says, which is the part worth reporting."""
    spreads = [r["spread"] for r in rounds if "spread" in r]
    print()
    if not spreads:
        print("Nothing could be compared: fewer than two probes reported a fresh reading at the")
        print("same time. Check the entity ids, and that every probe is actually reporting.")
        return

    print(f"Looks with at least two usable probes : {len(spreads)} of {len(rounds)}")
    print(f"Spread, median across the run         : {statistics.median(spreads):.2f} index points")
    print(f"Spread, worst seen                    : {max(spreads):.2f} index points")
    print(f"Spread, best seen                     : {min(spreads):.2f} index points")
    print()
    print("Read that as a floor, not a result: no calibration of any one of these probes can")
    print("honestly resolve better than roughly the spread between them. One run is one")
    print("moisture level, so a short run says nothing about whether they also agree when dry.")


def main() -> int:
    """Read the probes on an interval, write a CSV, and say what the spread was."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("entities", nargs="+", help="entity ids of the probes, two or more")
    parser.add_argument("--every", type=float, default=60.0, help="seconds between looks (default 60)")
    parser.add_argument(
        "--hours", type=float, default=0.25, help="how long to watch (default 0.25; ten minutes is 0.17)"
    )
    parser.add_argument("--csv", default="probe_spread.csv", help="where to write the readings")
    parser.add_argument(
        "--max-age",
        type=float,
        default=DEFAULT_MAX_AGE_S,
        help=f"seconds before a reading is too old to compare (default {DEFAULT_MAX_AGE_S:.0f})",
    )
    args = parser.parse_args()

    if len(args.entities) < 2:
        _die("Two probes is the minimum: one probe has nothing to disagree with.")

    base_url, token = os.environ.get("HA_URL"), os.environ.get("HA_TOKEN")
    if not base_url or not token:
        _die("Set HA_URL and HA_TOKEN in the environment. The token is read and never printed.")

    deadline = time.monotonic() + args.hours * 3600
    rounds: list[dict] = []

    print(f"Watching {len(args.entities)} probes every {args.every:.0f}s for {args.hours:.2f}h.")
    print("Probes must be in the SAME soil for any of this to mean anything.\n")

    with open(args.csv, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["at", *args.entities, "spread", "median", "deviation", "skipped"])
        try:
            while True:
                result = read_round(base_url, token, args.entities, args.max_age)
                rounds.append(result)
                _print_round(result, args.entities)
                writer.writerow(
                    [
                        result["at"],
                        *[result["values"].get(e, "") for e in args.entities],
                        result.get("spread", ""),
                        result.get("median", ""),
                        result.get("deviation", ""),
                        ";".join(f"{k}={v}" for k, v in result["skipped"].items()),
                    ]
                )
                handle.flush()
                if time.monotonic() >= deadline:
                    break
                time.sleep(args.every)
        except KeyboardInterrupt:
            print("\nStopped early. What was collected is in the CSV and counts.")

    summarise(rounds)
    print(f"\nReadings written to {args.csv}.")
    print("Results, including boring ones, are wanted at")
    print("https://github.com/never-dry/NeverDryCalibrator/discussions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
