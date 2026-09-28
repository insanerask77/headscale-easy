# Third-party notices

Headscale Easy bundles the following third-party assets in `web/static/`:

| Asset | License | Source |
|---|---|---|
| Inter variable font (`fonts/InterVariable.woff2`) | SIL Open Font License 1.1 — see [`web/static/fonts/LICENSE-Inter.txt`](web/static/fonts/LICENSE-Inter.txt) | https://rsms.me/inter/ |
| Icons adapted from Lucide (inline SVG in `web/ui.py`) | ISC License | https://lucide.dev |

Lucide's license:

```
ISC License

Copyright (c) for portions of Lucide are held by Cole Bemis 2013-2022 as part
of Feather (MIT). All other copyright (c) for Lucide are held by Lucide
Contributors 2022.

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND
FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM
LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR
OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
PERFORMANCE OF THIS SOFTWARE.
```

The stack also runs, as separate unmodified container images, Headscale
(BSD-3-Clause), Caddy (Apache-2.0), Authentik (MIT, with enterprise features
under their own license) and PostgreSQL (PostgreSQL License).
