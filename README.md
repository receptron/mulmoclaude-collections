# MulmoClaude Collections

A curated registry of **MulmoClaude collections** — schema-driven data apps you can discover
and import from inside MulmoClaude.

A *collection* is a small bundle: a `schema.json` that declares a data model and its UI
(tables, calendars, kanban, forms), optional custom HTML **views**, optional action
**templates**, and optional **seed** records. The MulmoClaude host renders the whole app from
that schema — no per-collection host code.

## For users

In MulmoClaude, open **/collections → Discover**. The app reads this registry's published
[`index.json`](#how-it-works) and lists every collection with its icon, fields, views, author,
and (if provided) a screenshot. Import one and it appears at `/collections/<slug>`, with any
bundled seed data populated on first import.

## For contributors

See **[CONTRIBUTING.md](./CONTRIBUTING.md)**. In short: add
`collections/<your-github-login>/<slug>/` (with `SKILL.md`, `schema.json`, `meta.json`),
run `npm run validate` and `npm run build-index`, and open a PR. You may only publish under
your own GitHub-login namespace (CI enforces it).

```
collections/<author>/<slug>/
  SKILL.md  schema.json  meta.json
  screenshot.png?  views/*.html?  templates/*.md?  seed/items/*.json?
```

## How it works

- `scripts/build-index.mjs` walks `collections/` and regenerates **`index.json`** — the single
  file the MulmoClaude backend fetches (one GET) to render the Discover catalog — plus a
  per-collection **`manifest.json`** listing the bundle files the host fetches when importing. The
  [`build-index` workflow](./.github/workflows/build-index.yml) publishes it (and the JSON
  Schemas) to **GitHub Pages** on every push to `main`, so the backend reads a stable URL
  without bot-commits to `main`.
  > Enable Pages once: **Settings → Pages → Source: GitHub Actions**.
- `scripts/validate.mjs` checks every collection (run by [`pr-validate`](./.github/workflows/pr-validate.yml)
  on each PR): metadata, semver, author/PR-author identity, host-equivalent schema validity,
  seed records (secrets hard-fail, PII warn), and a custom-view CSP lint.
- Contracts live in [`schema/`](./schema): `meta.schema.json` and `index.schema.json`
  (the public, versioned index contract).

## Commands

```bash
npm run validate       # validate every collection
npm run build-index    # regenerate index.json
npm run check-index    # fail if index.json is stale
npm test               # run the unit tests
```

## License

Registry tooling: MIT (see [LICENSE](./LICENSE)). Each collection declares its own license in
its `meta.json`.
