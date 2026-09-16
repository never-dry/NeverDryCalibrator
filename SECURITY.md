# Security Policy

## Scope

This integration reads entity states inside your own Home Assistant instance,
computes with them, and writes one file per configured pair under
`.storage/`. It makes no network requests, declares no Python requirements, and
runs no dynamic code. The only write it can perform outside its own entities is
the opt-in `apply_device_offset` service, which calls `number.set_value` on a
calibration entity you configured it to accept.

## Data collected

Raw probe indices, water deficits, soil and ambient temperatures, and the
timestamps of the above. Nothing personal, nothing leaving the instance. The
diagnostics download is therefore complete rather than redacted, so a field bug
report is actionable.

## Reporting a vulnerability

Open a private security advisory on the repository, or an issue if the problem is
not sensitive. Please include the diagnostics download when the report concerns
the calibration itself.

## Supported versions

The latest released version is the supported one.
