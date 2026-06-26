#!/usr/bin/env node
// Regenerate index.json from collections/<author>/<slug>/.
// Usage:
//   node scripts/build-index.mjs            # write index.json
//   node scripts/build-index.mjs --check    # fail if index.json is stale (CI)

import { existsSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { contentSha, listCollections, listSeedIds, readJson, summarizeSchema } from "./lib/collections.mjs";

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const REGISTRY = "receptron/mulmoclaude-collections";
const INDEX_PATH = path.join(REPO_ROOT, "index.json");

function buildEntry({ author, slug, id, dir }) {
  const meta = readJson(path.join(dir, "meta.json"));
  const schema = readJson(path.join(dir, "schema.json"));
  const { icon, title, fieldCount, views } = summarizeSchema(schema);
  const seed = listSeedIds(dir);
  // Repo-relative paths in the index are URLs — always POSIX "/", never OS sep.
  const relPosix = ["collections", author, slug].join("/");
  const entry = {
    id,
    author,
    slug,
    title: meta.title ?? title,
    icon,
    description: meta.description ?? "",
    version: meta.version,
    tags: meta.tags ?? [],
    license: meta.license,
    fieldCount,
    views,
    hasSeed: seed.length > 0,
    seedCount: seed.length,
    path: relPosix,
    contentSha: contentSha(dir),
  };
  if (existsSync(path.join(REPO_ROOT, "collections", author, slug, "screenshot.png"))) entry.screenshot = `${relPosix}/screenshot.png`;
  return entry;
}

function buildIndex() {
  const collections = listCollections(REPO_ROOT)
    .map(buildEntry)
    .sort((a, b) => a.id.localeCompare(b.id));
  return { schemaVersion: 1, generatedAt: new Date().toISOString(), registry: REGISTRY, collections };
}

// Compare ignoring generatedAt so --check is stable across runs.
function sansTimestamp(index) {
  return JSON.stringify({ ...index, generatedAt: "" });
}

function main() {
  const check = process.argv.includes("--check");
  const next = buildIndex();
  const serialized = `${JSON.stringify(next, null, 2)}\n`;
  if (check) {
    const current = existsSync(INDEX_PATH) ? readFileSync(INDEX_PATH, "utf-8") : "";
    const stale = !current || sansTimestamp(JSON.parse(current)) !== sansTimestamp(next);
    if (stale) {
      console.error("index.json is stale — run `node scripts/build-index.mjs` and commit.");
      process.exit(1);
    }
    console.log(`index.json up to date (${next.collections.length} collections).`);
    return;
  }
  writeFileSync(INDEX_PATH, serialized);
  console.log(`Wrote index.json (${next.collections.length} collections).`);
}

main();
