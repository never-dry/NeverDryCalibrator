"""Report how many times each release archive was downloaded.

Why a script rather than only a badge: GitHub counts downloads of **release
assets**, and nothing else. The source archives it generates for every tag are
not counted, which is why the release workflow attaches an explicit
``neverdry_calibrator.zip``: without that asset there is no number to report at
all. HACS fetches exactly that asset on install and on every update.

What the numbers mean, and do not mean:

* the **total** counts one long-standing user once per update, so it measures
  activity rather than people;
* the **latest release** starts at zero the day it ships and only climbs as
  people get round to updating;
* the **most downloaded single release** is the closest honest proxy for reach,
  because each person fetches a given release once.

None of the three is an install count. Anyone quoting one of these as "users" is
quoting the wrong number.

Usage:
    python3 scripts/download_counts.py [--repo owner/name] [--json]

Unauthenticated calls are rate limited to sixty an hour; set ``GITHUB_TOKEN`` in
the environment to lift that. The token is read, never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import urllib.error
import urllib.request

DEFAULT_REPO = "never-dry/NeverDryCalibrator"
API = "https://api.github.com/repos/{repo}/releases?per_page=100"


def _ssl_context() -> ssl.SSLContext:
    """A verifying context that also works on a Python without system roots.

    A framework build of Python on macOS ships no certificate store of its own
    until someone runs its Install Certificates script, and the failure looks
    like a network problem rather than a missing root. Using certifi when it is
    importable removes a support question that has nothing to do with this
    project. Verification is never disabled.
    """
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def fetch_releases(repo: str) -> list[dict]:
    """Return every release of the repository, newest first.

    Raises on anything that is not a list: a rate-limited or unauthorised API
    answers with an object, and turning that into zeros would publish a
    confident wrong figure.
    """
    request = urllib.request.Request(
        API.format(repo=repo),
        headers={"Accept": "application/vnd.github+json", "User-Agent": "neverdry-calibrator-counts"},
    )
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=20, context=_ssl_context()) as response:  # noqa: S310
        payload = json.load(response)
    if not isinstance(payload, list):
        raise RuntimeError(f"unexpected answer from the releases API: {payload}")
    return payload


def counts(releases: list[dict]) -> list[tuple[str, int, bool]]:
    """Tag, downloads of its archives, and whether it is a pre-release."""
    rows: list[tuple[str, int, bool]] = []
    for release in releases:
        downloads = sum(asset.get("download_count", 0) for asset in release.get("assets", []))
        rows.append((release.get("tag_name", "?"), downloads, bool(release.get("prerelease"))))
    return rows


def main() -> int:
    """Print the per-release breakdown, or emit it as JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--json", action="store_true", help="emit machine readable output")
    arguments = parser.parse_args()

    try:
        rows = counts(fetch_releases(arguments.repo))
    except (urllib.error.URLError, RuntimeError) as error:
        print(f"could not read the releases of {arguments.repo}: {error}")
        if isinstance(error, urllib.error.URLError) and "CERTIFICATE_VERIFY_FAILED" in str(error.reason):
            print("this Python has no certificate store: pip install certifi, or run its Install Certificates script")
        return 1

    if not rows:
        print(f"{arguments.repo} has no releases yet")
        return 0

    total = sum(downloads for _, downloads, _ in rows)
    latest_tag, latest_downloads, _ = rows[0]
    peak_tag, peak_downloads, _ = max(rows, key=lambda row: row[1])

    if arguments.json:
        print(
            json.dumps(
                {
                    "repo": arguments.repo,
                    "total": total,
                    "latest": {"tag": latest_tag, "downloads": latest_downloads},
                    "most_downloaded": {"tag": peak_tag, "downloads": peak_downloads},
                    "releases": [
                        {"tag": tag, "downloads": downloads, "prerelease": pre} for tag, downloads, pre in rows
                    ],
                },
                indent=2,
            )
        )
        return 0

    width = max(len(tag) for tag, _, _ in rows)
    print(f"{arguments.repo}\n")
    for tag, downloads, prerelease in rows:
        mark = "  (pre-release)" if prerelease else ""
        print(f"  {tag:<{width}}  {downloads:>7}{mark}")
    print(f"\n  {'total':<{width}}  {total:>7}")
    print(f"  most downloaded: {peak_tag} with {peak_downloads}")
    print("  none of these is an install count: HACS fetches the archive again at every update")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
