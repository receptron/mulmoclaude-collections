# Contributing a collection

This repository is the curated registry of **MulmoClaude collections** — schema-driven
data apps (a `schema.json` data model + optional custom HTML views + optional seed data).
Users discover and import them from inside MulmoClaude.

## Layout

One collection per directory, namespaced by your GitHub login:

```
collections/<author>/<slug>/
  SKILL.md          # required — how the assistant maintains the records
  schema.json       # required — data model + UI (the collection itself)
  meta.json         # required — distribution metadata (author/version/license/…)
  screenshot.png    # optional — catalog preview
  views/*.html      # optional — custom views (sandboxed; data only via window.__MC_VIEW)
  templates/*.md    # optional — action templates
  manifest.json     # generated — bundle file list (npm run build-index; commit it)
  seed/items/*.json # optional — sample records (see "Seed data" below)
```

- `<author>` **must be your GitHub login**, and `meta.author` must equal it. CI verifies
  both match the PR author — you can only publish under your own namespace.
- `<slug>`: lowercase letters/digits/hyphens, no leading/trailing hyphen, **no `mc-` prefix**.
- `<author>/<slug>` is the collection's global identity. Two authors may both have `movies`.

## Steps

1. Fork this repo.
2. Create `collections/<your-login>/<slug>/` with at least `SKILL.md`, `schema.json`, `meta.json`.
   The easiest source is an existing collection in your MulmoClaude workspace
   (`data/skills/<slug>/`) — copy `SKILL.md`, `schema.json`, and `views/`.
3. `npm run validate` — fix any errors.
4. `npm run build-index` — regenerates `index.json` and each collection's `manifest.json`
   (the file list the host fetches at import). Commit them.
5. Open a PR and fill in the template.

## meta.json

```jsonc
{
  "author": "your-login",      // = your GitHub login (CI-verified)
  "slug": "movies",
  "version": "1.0.0",          // semver; bump on every change (breaking schema change = major)
  "title": "映画リスト",
  "description": "One-line description shown in the catalog.",
  "tags": ["entertainment"],
  "license": "MIT",
  "dataConsent": true           // required true only if seed/ contains real data
}
```

## Seed data

Seed records ship under `seed/items/<id>.json` and are materialized into the importing
user's workspace **only when their collection is empty** (never overwriting).

- You **may** include your own real records — but that is **your responsibility**: set
  `meta.dataConsent: true` to affirm you own the data and consent to publishing it.
- **Never** include credentials, API keys, or tokens. CI hard-fails on detected secrets.
- Prefer a handful of representative demo records over a full personal dataset.
- `image`/`file` fields that point at local workspace paths won't resolve on another
  machine — use public `https:` URLs (e.g. for posters) or omit them in seed.

## What CI checks

`npm run validate` (and the PR CI) verify: meta completeness + semver + slug rules,
author/path/PR-author identity (R9), schema validity (host-equivalent rules), seed records
(JSON, id charset, enum ranges, **secrets hard-fail / PII warn**), custom-view CSP lint, and
that `index.json` and each `manifest.json` are up to date. The runtime view sandbox (CSP, no phone-home) is the real
security boundary; the lint is a courtesy.

## Updating a collection

Edit the files, **bump `meta.version`**, `npm run build-index`, and open a PR. Importers see
an "update available" when the published version/contentSha moves ahead of their installed copy.
Records are never auto-migrated on update — a removed/renamed field just leaves harmless data behind.
