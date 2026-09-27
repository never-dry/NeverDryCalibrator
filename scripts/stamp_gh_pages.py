#!/usr/bin/env python3
"""stamp_gh_pages.py - fill in the version and date the published pages show.

Every page carries a line saying which version of the integration it describes and
when the site was last published. The checklist for a published page asks for
it, and the reason is not bookkeeping: a landing page with no date is one a
visitor cannot date, and a reader who cannot date a page cannot tell a current
project from an abandoned one.

Writing those two values by hand would guarantee they go stale, because they
change on a cadence nobody edits the site on. So the pages carry placeholders
and this script fills them in at deploy time, from the two places that already
know the answer: the integration manifest for the version, and the commit being
deployed for the date.

The date is the commit's, not today's. The badge refresh runs on a weekly
schedule and redeploys the same content; stamping "today" then would announce an
update that did not happen, which is the same lie as no date at all.

Run from the repository root, before the site is uploaded:

    python3 scripts/stamp_gh_pages.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES = ROOT / "docs" / "gh-pages"
MANIFEST = ROOT / "custom_components" / "neverdry_calibrator" / "manifest.json"

PLACEHOLDERS = ("{{SITE_VERSION}}", "{{SITE_UPDATED}}")


def integration_version() -> str:
    """The version the pages will announce, read from the integration manifest."""
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["version"]


def published_on() -> str:
    """The date of the commit being deployed, falling back to today.

    A shallow checkout still has HEAD, which is all this needs. If git is not
    available at all the fallback is today's date: less accurate, but a date
    that is a day or two off is worth more to a reader than no date, and the
    check that follows would rather see a plausible value than a placeholder.
    """
    git = shutil.which("git")
    if not git:
        return date.today().isoformat()
    try:
        out = subprocess.run(  # noqa: S603 - resolved path, fixed argv, no input from anywhere
            [git, "log", "-1", "--format=%cs"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        stamped = out.stdout.strip()
        if stamped:
            return stamped
    except (OSError, subprocess.SubprocessError):
        # Swallowed on purpose, and this is the whole of the handling: every way
        # git can fail here - absent, refusing to run, no repository, taking too
        # long - has the same answer, which is the fallback below. Failing the
        # deploy instead would take the site down to protect a date, and letting
        # the error travel would put a traceback where a footer line belongs.
        # Nothing is logged because the value that ships is visible on the page.
        pass
    return date.today().isoformat()


def main() -> int:
    """Fill both placeholders in every page and return a shell exit status."""
    version = integration_version()
    updated = published_on()
    values = {"{{SITE_VERSION}}": version, "{{SITE_UPDATED}}": updated}

    touched = 0
    filled = 0
    for page in sorted(PAGES.rglob("*.html")):
        text = page.read_text(encoding="utf-8")
        if not any(p in text for p in PLACEHOLDERS):
            continue
        for placeholder, value in values.items():
            filled += text.count(placeholder)
            text = text.replace(placeholder, value)
        page.write_text(text, encoding="utf-8")
        touched += 1

    print(f"stamped v{version} / {updated} into {touched} pages ({filled} placeholders)")
    if not touched:
        print("no page carried a placeholder: nothing to stamp", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
