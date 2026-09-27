# vendor/ - third-party code served from our own origin

Everything in this directory is somebody else's code, copied here on purpose
instead of being loaded from their CDN. A CDN in the page is a third party that
sees every visitor's IP address before we do, and a script URL we do not control
can change under us between one visit and the next. Serving our own copy removes
both, at the cost of having to update it by hand.

## vemetric-main.js

| | |
|---|---|
| Upstream | https://cdn.vemetric.com/main.js |
| Copied on | 2026-09-27 |
| Size | 7290 bytes |
| SHA-384 | `TX7HhcQPBYtVarsLOZVwwcPXzwliiI/LdBvjJv+sUrMMVJTUUoX5yjRL+wkYrKON` |

Taken from the sibling project's copy rather than fetched again, and verified
byte for byte against the hash recorded there. Two sites running two different
builds of the same analytics script would make a difference between their numbers
impossible to attribute.

The script reads its configuration from its own `<script>` tag
(`document.currentScript`), so the `id`, `data-token` and any `data-*` options
travel with the tag and keep working from this location. It sends events to
`https://hub.vemetric.com`, which is why that origin is the only one allowed in
`connect-src` by the pages' Content-Security-Policy.

Self-hosting removes the CDN from the trust chain. It does **not** remove
Vemetric: the ingest endpoint still sees the visitor's IP address at the moment
the event arrives. That is what `privacy.html` has to say out loud, and does.

## vemetric-init.js

Ours, not theirs, and it stays here because it only exists to serve the script
above: a ten-line queue stub so events fired before the deferred main script has
loaded have somewhere to land.

It is a file rather than an inline script for one reason. An inline script would
need `script-src 'unsafe-inline'` in the Content-Security-Policy, and that single
word would also permit any script an attacker manages to inject into the markup.

## Updating

Fetch the upstream file, record the new size and hash in the table above, and say
when. A copy with no provenance is worse than a CDN, because nobody can tell what
it is a copy of.
