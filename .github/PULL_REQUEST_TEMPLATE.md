<!-- Adding or updating a collection? Fill this in. CI validates automatically. -->

## Collection

- **Path**: `collections/<your-github-login>/<slug>/`
- **What it is** (one line):
- **New collection** or **update**? (bump `meta.version` on updates)

## Screenshot

<!-- Optional but recommended. Drag an image of the collection / its custom view here. -->

## Checklist

- [ ] The path is `collections/<my-github-login>/<slug>/` and `meta.author` is **my GitHub login** (CI enforces they match the PR author).
- [ ] `meta.json` has `author`, `slug`, `version` (semver), `title`, `description`, `license`.
- [ ] `schema.json` validates locally (`npm run validate`).
- [ ] I regenerated the index (`npm run build-index`) and committed `index.json`.
- [ ] `slug` does not use the reserved `mc-` prefix.
- [ ] **If I included `seed/` data**: it is mine to publish (or fully synthetic), it contains **no credentials/tokens**, and I set `meta.dataConsent: true`.
- [ ] Any custom `views/*.html` read data only via `window.__MC_VIEW` (no external `fetch`).
