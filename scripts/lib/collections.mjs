// Shared helpers for enumerating + summarizing collections in this registry.
// Plain Node ESM, zero dependencies (CI stays fast).

import { createHash } from "node:crypto";
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";

export const COLLECTIONS_DIR = "collections";
const SCREENSHOT_FILE = "screenshot.png";
// Generated/non-bundle files excluded from the importable bundle + contentSha.
export const MANIFEST_FILE = "manifest.json";

const isDir = (p) => {
  try {
    return statSync(p).isDirectory();
  } catch {
    return false;
  }
};

const listDirs = (p) => (isDir(p) ? readdirSync(p).filter((name) => isDir(path.join(p, name))) : []);

/** Every collections/<author>/<slug>/ as { author, slug, id, dir }. */
export function listCollections(repoRoot) {
  const root = path.join(repoRoot, COLLECTIONS_DIR);
  return listDirs(root).flatMap((author) =>
    listDirs(path.join(root, author)).map((slug) => ({
      author,
      slug,
      id: `${author}/${slug}`,
      dir: path.join(root, author, slug),
    })),
  );
}

export function readJson(filePath) {
  return JSON.parse(readFileSync(filePath, "utf-8"));
}

/** Sorted POSIX relative paths of every file under dir (excluding screenshot).
 *  POSIX-normalized so contentSha is identical across OSes. */
export function bundleFiles(dir, current = dir) {
  return readdirSync(current)
    .flatMap((name) => {
      const abs = path.join(current, name);
      if (isDir(abs)) return bundleFiles(dir, abs);
      if (name === SCREENSHOT_FILE || name === MANIFEST_FILE) return [];
      return [path.relative(dir, abs).split(path.sep).join("/")];
    })
    .sort();
}

/** Stable hash of the importable bundle (screenshot excluded) for update detection. */
export function contentSha(dir) {
  const hash = createHash("sha256");
  for (const rel of bundleFiles(dir)) {
    hash.update(rel);
    hash.update("\0");
    hash.update(readFileSync(path.join(dir, rel)));
    hash.update("\0");
  }
  return hash.digest("hex").slice(0, 16);
}

/** Pull the display-facing summary the index needs out of a schema.json object. */
export function summarizeSchema(schema) {
  const fields = schema.fields && typeof schema.fields === "object" ? Object.keys(schema.fields) : [];
  const views = Array.isArray(schema.views) ? schema.views.map((v) => v.label).filter(Boolean) : [];
  return { icon: schema.icon ?? "", title: schema.title ?? "", fieldCount: fields.length, views };
}

export function listSeedIds(dir) {
  const seedDir = path.join(dir, "seed", "items");
  if (!isDir(seedDir)) return [];
  return readdirSync(seedDir).filter((name) => name.endsWith(".json"));
}
