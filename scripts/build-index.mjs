#!/usr/bin/env node
// Regenerate index.json and each collection's manifest.json from
// collections/<author>/<slug>/.
// Usage:
//   node scripts/build-index.mjs            # write index.json + manifests
//   node scripts/build-index.mjs --check    # fail if any committed manifest is stale (CI)
//
// index.json is published to GitHub Pages by build-index.yml on every push to
// main and is not committed (gitignored), so --check only guards the committed
// manifests. manifest.json lists the importable bundle files (POSIX relative
// paths) so the host can fetch a collection by raw URL without a directory listing.

import { existsSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { bundleFiles, contentSha, listCollections, listSeedIds, MANIFEST_FILE, readJson, summarizeSchema } from "./lib/collections.mjs";

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const REGISTRY = "receptron/mulmoclaude-collections";
const INDEX_PATH = path.join(REPO_ROOT, "index.json");

const serialize = (obj) => `${JSON.stringify(obj, null, 2)}\n`;
const manifestPath = (dir) => path.join(dir, MANIFEST_FILE);

// The importable file list the host fetches at import time. screenshot.png and
// manifest.json itself are excluded by bundleFiles.
const manifestFor = (dir) => ({ files: bundleFiles(dir) });

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

function buildIndex(collections) {
  const entries = collections.map(buildEntry).sort((a, b) => a.id.localeCompare(b.id));
  return { schemaVersion: 1, generatedAt: new Date().toISOString(), registry: REGISTRY, collections: entries };
}

function writeArtifacts(collections) {
  for (const collection of collections) writeFileSync(manifestPath(collection.dir), serialize(manifestFor(collection.dir)));
  writeFileSync(INDEX_PATH, serialize(buildIndex(collections)));
  console.log(`Wrote index.json + ${collections.length} manifest(s).`);
}

function staleManifests(collections) {
  const stale = [];
  for (const collection of collections) {
    const have = existsSync(manifestPath(collection.dir)) ? readJson(manifestPath(collection.dir)) : null;
    if (!have || JSON.stringify(have) !== JSON.stringify(manifestFor(collection.dir))) stale.push(`${collection.id}/manifest.json`);
  }
  return stale;
}

function main() {
  const check = process.argv.includes("--check");
  const collections = listCollections(REPO_ROOT);
  if (check) {
    const stale = staleManifests(collections);
    if (stale.length) {
      console.error(`stale manifest(s) — run \`node scripts/build-index.mjs\` and commit:\n  ${stale.join("\n  ")}`);
      process.exit(1);
    }
    console.log(`${collections.length} manifest(s) up to date.`);
    return;
  }
  writeArtifacts(collections);
}

main();
