#!/usr/bin/env node
// Validate every collection in the registry. CI calls this on PRs.
//
// Usage:
//   node scripts/validate.mjs
//     → schema / seed / lint check on every collection. No R9 enforcement.
//
//   node scripts/validate.mjs --pr-author <login> --changed-from <file>
//     → as above, PLUS the R9 identity rule (`meta.author` must equal
//       <login>) applied ONLY to collections listed in <file>. The file
//       holds one path per line, in either `collections/<author>/<slug>`
//       or `<author>/<slug>` shape (the CI workflow computes it from
//       `git diff --name-only origin/main...HEAD`).
//
// The `--changed-from` flag exists to keep R9 scoped to the collections
// the PR actually touches. Without it (or without `--pr-author`), R9 is
// not enforced — see the doctring on `parseValidateArgs` for the
// rationale and the regression test at test/test_validate_scope.mjs for
// the contract.

import { readFileSync, readdirSync, existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { listCollections, listSeedIds, readJson } from "./lib/collections.mjs";
import { validateRecord, validateSchema } from "./lib/validateSchema.mjs";

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SEMVER_RE = /^\d+\.\d+\.\d+$/;
const SLUG_RE = /^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/;
const AUTHOR_RE = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$/;
const META_REQUIRED = ["author", "slug", "version", "title", "description", "license"];
const ALLOWED_FETCH_HINT = "window.__MC_VIEW.dataUrl";

function argValue(argv, flag) {
  const i = argv.indexOf(flag);
  return i >= 0 ? argv[i + 1] : undefined;
}

/**
 * Normalize one `git diff --name-only` line into the `<author>/<slug>`
 * form `listCollections` uses. Accepts:
 *   - `collections/<author>/<slug>` (directory line)
 *   - `collections/<author>/<slug>/anything/below` (file path under a
 *     collection — we collapse to the collection root)
 * Anything else returns null — including pre-normalized two-segment
 * ids like `alice/foo`, which we deliberately reject so an
 * unrelated repo-root file (`scripts/build-index.mjs`,
 * `package.json`) can never be mistaken for a collection id by
 * accident. Tests that want to pass an id set directly should build
 * the `Set` themselves and call `runValidate` instead of going
 * through this normalizer.
 */
export function normalizeCollectionId(line) {
  const trimmed = line.trim();
  if (!trimmed) return null;
  const parts = trimmed.split("/");
  if (parts[0] === "collections" && parts.length >= 3 && parts[1] && parts[2]) {
    return `${parts[1]}/${parts[2]}`;
  }
  return null;
}

/**
 * Read a list of paths (or `<author>/<slug>` ids) from a file and
 * return the unique set of collection ids it names. CI uses
 *   git diff --name-only origin/main...HEAD -- 'collections/**' > F
 * to fill this; multiple files under the same collection collapse to
 * one entry.
 */
export function loadChangedIds(filePath) {
  const text = readFileSync(filePath, "utf-8");
  const ids = new Set();
  for (const line of text.split("\n")) {
    const id = normalizeCollectionId(line);
    if (id) ids.add(id);
  }
  return ids;
}

/**
 * Parse argv into a config object. Centralizes the safety invariant:
 *
 *   `--pr-author` REQUIRES `--changed-from`.
 *
 * The original validate.mjs accepted `--pr-author` alone and applied
 * R9 to every collection in the registry. That was a scope bug — a
 * fresh contributor's PR would fail R9 on every existing collection
 * owned by anyone else. Requiring the changed-paths file at the parser
 * makes the bug structurally impossible to re-introduce: any caller
 * that wants identity enforcement must also tell us which collections
 * this PR touches.
 *
 * Exported for the regression test in test/test_validate_scope.mjs.
 */
export function parseValidateArgs(argv) {
  const prAuthor = argValue(argv, "--pr-author");
  const changedFromFile = argValue(argv, "--changed-from");
  if (prAuthor && !changedFromFile) {
    throw new Error(
      "validate.mjs: --pr-author requires --changed-from <file> " +
        "(the file lists collections/<author>/<slug> paths the PR " +
        "touches; the R9 identity check is applied only to those).",
    );
  }
  const changedIds = changedFromFile ? loadChangedIds(changedFromFile) : null;
  return { prAuthor, changedIds };
}

function validateMeta(meta, col, prAuthor, errors) {
  for (const key of META_REQUIRED) if (!meta[key]) errors.push(`meta.json: missing "${key}"`);
  if (meta.slug && !SLUG_RE.test(meta.slug)) errors.push(`meta.slug "${meta.slug}" invalid`);
  if (meta.slug && meta.slug.startsWith("mc-")) errors.push(`meta.slug must not use the reserved "mc-" prefix`);
  if (meta.author && !AUTHOR_RE.test(meta.author)) errors.push(`meta.author "${meta.author}" is not a valid GitHub login`);
  if (meta.version && !SEMVER_RE.test(meta.version)) errors.push(`meta.version "${meta.version}" is not semver`);
  if (meta.slug && meta.slug !== col.slug) errors.push(`meta.slug "${meta.slug}" must equal path slug "${col.slug}"`);
  if (meta.author && meta.author !== col.author) errors.push(`meta.author "${meta.author}" must equal path author "${col.author}"`);
  if (prAuthor && meta.author && meta.author !== prAuthor) errors.push(`R9: meta.author "${meta.author}" must equal PR author "${prAuthor}"`);
}

// Heuristic CSP lint — the runtime sandbox is the real defense; this catches
// obvious phone-home before review.
function lintViews(dir, warnings, errors) {
  const viewsDir = path.join(dir, "views");
  if (!existsSync(viewsDir)) return;
  for (const name of readdirSync(viewsDir).filter((n) => n.endsWith(".html"))) {
    const html = readFileSync(path.join(viewsDir, name), "utf-8");
    const fetches = [...html.matchAll(/fetch\(\s*["'`]?(https?:\/\/[^"'`)\s]+)/g)].map((m) => m[1]);
    for (const url of fetches) errors.push(`view ${name}: fetch() to external origin "${url}" is blocked by the sandbox; read via ${ALLOWED_FETCH_HINT}`);
    if (/\bnew\s+WebSocket\(/.test(html)) warnings.push(`view ${name}: WebSocket use will be blocked by the sandbox`);
  }
}

function validateSeed(col, schema, errors, warnings) {
  const ids = listSeedIds(col.dir);
  if (ids.length === 0) return 0;
  const meta = readJson(path.join(col.dir, "meta.json"));
  if (meta.dataConsent !== true) errors.push(`seed present but meta.dataConsent !== true — affirm ownership/consent to publish seed data`);
  for (const file of ids) {
    const idFromFilename = file.replace(/\.json$/, "");
    let record;
    try {
      record = readJson(path.join(col.dir, "seed", "items", file));
    } catch (e) {
      errors.push(`seed/${file}: invalid JSON (${e.message})`);
      continue;
    }
    const res = validateRecord(record, schema, idFromFilename);
    res.errors.forEach((m) => errors.push(`seed/${file}: ${m}`));
    res.warnings.forEach((m) => warnings.push(`seed/${file}: ${m}`));
  }
  return ids.length;
}

function validateCollection(col, prAuthor) {
  const errors = [];
  const warnings = [];
  let meta;
  try {
    meta = readJson(path.join(col.dir, "meta.json"));
  } catch (e) {
    return { errors: [`meta.json unreadable: ${e.message}`], warnings };
  }
  validateMeta(meta, col, prAuthor, errors);
  let schema;
  try {
    schema = readJson(path.join(col.dir, "schema.json"));
  } catch (e) {
    return { errors: [...errors, `schema.json unreadable: ${e.message}`], warnings };
  }
  const schemaRes = validateSchema(schema);
  schemaRes.errors.forEach((m) => errors.push(`schema: ${m}`));
  if (!existsSync(path.join(col.dir, "SKILL.md"))) errors.push(`missing SKILL.md`);
  lintViews(col.dir, warnings, errors);
  const seedCount = validateSeed(col, schema, errors, warnings);
  return { errors, warnings, seedCount };
}

/**
 * Pure-ish validator (still reads filesystem at repoRoot). Exposed for
 * direct calls in tests so we don't have to spawn a subprocess just to
 * exercise the R9-scoping contract. Returns:
 *   { results: [{ col, errors, warnings, seedCount }, ...],
 *     total: number, failed: number }
 *
 * R9 is enforced on `col` iff `changedIds === null` (no scope file —
 * i.e. the CLI rejected this combo, so any caller reaching here with
 * a non-null prAuthor and a null changedIds set is doing it
 * deliberately, e.g. a local "enforce on everything" run) OR
 * `changedIds.has(col.id)`.
 */
export function runValidate({ repoRoot, prAuthor, changedIds }) {
  const cols = listCollections(repoRoot);
  const results = [];
  let failed = 0;
  for (const col of cols) {
    const inScope = changedIds === null || changedIds.has(col.id);
    const enforcePrAuthor = inScope ? prAuthor : undefined;
    const { errors, warnings, seedCount } = validateCollection(col, enforcePrAuthor);
    results.push({ col, errors, warnings, seedCount });
    if (errors.length) failed += 1;
  }
  return { results, total: cols.length, failed };
}

function main() {
  let parsed;
  try {
    parsed = parseValidateArgs(process.argv.slice(2));
  } catch (err) {
    console.error(`ERROR: ${err.message}`);
    process.exit(2);
  }
  const { results, total, failed } = runValidate({ repoRoot: REPO_ROOT, ...parsed });
  for (const { col, errors, warnings, seedCount } of results) {
    const status = errors.length ? "FAIL" : "ok";
    console.log(`[${status}] ${col.id} (seed: ${seedCount ?? 0})`);
    warnings.forEach((m) => console.log(`   warn: ${m}`));
    errors.forEach((m) => console.log(`   err:  ${m}`));
  }
  console.log(`\n${total} collection(s), ${failed} failed.`);
  if (failed) process.exit(1);
}

// Run as CLI only when invoked directly (not when imported from tests).
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main();
}
