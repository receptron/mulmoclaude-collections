#!/usr/bin/env node
// Validate every collection in the registry. CI calls this on PRs.
// Usage:
//   node scripts/validate.mjs
//   node scripts/validate.mjs --pr-author <github-login>   # enforce R9 identity

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

function argValue(flag) {
  const i = process.argv.indexOf(flag);
  return i >= 0 ? process.argv[i + 1] : undefined;
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

function main() {
  const prAuthor = argValue("--pr-author");
  const cols = listCollections(REPO_ROOT);
  let failed = 0;
  for (const col of cols) {
    const { errors, warnings, seedCount } = validateCollection(col, prAuthor);
    const status = errors.length ? "FAIL" : "ok";
    console.log(`[${status}] ${col.id} (seed: ${seedCount ?? 0})`);
    warnings.forEach((m) => console.log(`   warn: ${m}`));
    errors.forEach((m) => console.log(`   err:  ${m}`));
    if (errors.length) failed += 1;
  }
  console.log(`\n${cols.length} collection(s), ${failed} failed.`);
  if (failed) process.exit(1);
}

main();
