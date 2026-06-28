// Regression tests for the R9 (identity) scoping contract in validate.mjs.
//
// The original validate.mjs accepted `--pr-author <login>` alone and
// applied the R9 rule (`meta.author === <login>`) to EVERY collection
// in the registry — so a contributor opening a PR for their own new
// collection would still trigger R9 failures on every existing
// collection owned by someone else. The fix makes the scope explicit:
//
//   - `--pr-author` REQUIRES `--changed-from <file>` at the parser
//     layer, so the buggy combo is structurally impossible to call.
//   - R9 is then enforced only on collections whose `<author>/<slug>`
//     id appears in the changed-from file.
//
// These tests pin both halves so the bug can't regress without
// breaking them.

import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

import { loadChangedIds, normalizeCollectionId, parseValidateArgs, runValidate } from "../scripts/validate.mjs";

// ── Helpers ───────────────────────────────────────────────────────

// Minimal-but-valid collection layout (meta.json + schema.json + SKILL.md)
// so the only assertion delta we care about is whether the R9 message
// fires. The schema is intentionally tiny — single required string id —
// to avoid coupling to validateSchema's full surface.
function seedCollection(repoRoot, author, slug) {
  const dir = path.join(repoRoot, "collections", author, slug);
  mkdirSync(dir, { recursive: true });
  writeFileSync(
    path.join(dir, "meta.json"),
    JSON.stringify({
      author,
      slug,
      version: "0.1.0",
      title: `${author}/${slug}`,
      description: "test fixture collection",
      license: "MIT",
    }),
  );
  writeFileSync(
    path.join(dir, "schema.json"),
    JSON.stringify({
      title: slug,
      icon: "label",
      dataPath: `data/${slug}/items`,
      primaryKey: "id",
      fields: {
        id: { type: "string", label: "ID", primary: true, required: true },
      },
    }),
  );
  writeFileSync(path.join(dir, "SKILL.md"), `# ${author}/${slug}\n`);
  return dir;
}

function withTempRegistry(setup) {
  const root = mkdtempSync(path.join(os.tmpdir(), "validate-scope-"));
  try {
    setup(root);
    return root;
  } catch (err) {
    rmSync(root, { recursive: true, force: true });
    throw err;
  }
}

function cleanup(root) {
  rmSync(root, { recursive: true, force: true });
}

const r9Errors = (errors) => errors.filter((e) => e.startsWith("R9:"));

// ── parseValidateArgs: safety invariant ───────────────────────────

test("parseValidateArgs: --pr-author without --changed-from throws", () => {
  // Structural defense against the scope-bug regression: a CLI caller
  // that wants R9 MUST also tell us which collections this PR touches.
  assert.throws(() => parseValidateArgs(["--pr-author", "alice"]), /--changed-from/);
});

test("parseValidateArgs: no flags → no enforcement, no error", () => {
  const { prAuthor, changedIds } = parseValidateArgs([]);
  assert.equal(prAuthor, undefined);
  assert.equal(changedIds, null);
});

test("parseValidateArgs: both flags together parse cleanly", () => {
  const root = withTempRegistry((r) => {
    writeFileSync(path.join(r, "changed.txt"), "collections/alice/foo\n");
  });
  try {
    const { prAuthor, changedIds } = parseValidateArgs(["--pr-author", "alice", "--changed-from", path.join(root, "changed.txt")]);
    assert.equal(prAuthor, "alice");
    assert.ok(changedIds instanceof Set);
    assert.ok(changedIds.has("alice/foo"));
  } finally {
    cleanup(root);
  }
});

// ── normalizeCollectionId: input shapes ───────────────────────────

test("normalizeCollectionId: accepts collections/<a>/<s>", () => {
  assert.equal(normalizeCollectionId("collections/alice/foo"), "alice/foo");
});

test("normalizeCollectionId: collapses sub-paths under a collection", () => {
  // The CI workflow pre-trims to dirs but the helper should be
  // tolerant of a raw `git diff --name-only` line too.
  assert.equal(normalizeCollectionId("collections/alice/foo/seed/items/001.json"), "alice/foo");
});

test("normalizeCollectionId: rejects non-collections paths", () => {
  // Two-segment repo-root files (e.g. `scripts/build-index.mjs`) must
  // not be mistaken for a `<author>/<slug>` collection id. The diff
  // filter in CI already narrows to `collections/**`, but the helper
  // is defensive in case the file is hand-edited.
  assert.equal(normalizeCollectionId(""), null);
  assert.equal(normalizeCollectionId("scripts/build-index.mjs"), null);
  assert.equal(normalizeCollectionId("README.md"), null);
  assert.equal(normalizeCollectionId("alice/foo"), null); // pre-normalized form not accepted
  assert.equal(normalizeCollectionId("collections/alice"), null); // missing slug segment
  assert.equal(normalizeCollectionId("collections"), null); // bare root
});

// ── loadChangedIds: file format ───────────────────────────────────

test("loadChangedIds: dedupes multiple files within the same collection", () => {
  const root = withTempRegistry((r) => {
    writeFileSync(
      path.join(r, "changed.txt"),
      [
        "collections/alice/foo/meta.json",
        "collections/alice/foo/schema.json",
        "collections/alice/foo/seed/items/001.json",
        "collections/bob/bar/meta.json",
      ].join("\n"),
    );
  });
  try {
    const ids = loadChangedIds(path.join(root, "changed.txt"));
    assert.deepEqual([...ids].sort(), ["alice/foo", "bob/bar"]);
  } finally {
    cleanup(root);
  }
});

// ── runValidate: R9 scope contract ────────────────────────────────

test("runValidate: R9 only fires for collections listed in changedIds", () => {
  const root = withTempRegistry((r) => {
    seedCollection(r, "alice", "foo");
    seedCollection(r, "bob", "bar");
  });
  try {
    const { results } = runValidate({
      repoRoot: root,
      prAuthor: "alice",
      // Only alice/foo is in scope — even though bob/bar is an
      // existing valid collection, R9 must NOT fire on it.
      changedIds: new Set(["alice/foo"]),
    });
    const aliceFoo = results.find((r) => r.col.id === "alice/foo");
    const bobBar = results.find((r) => r.col.id === "bob/bar");

    // alice/foo passes R9 (own author) and the rest of validation.
    assert.deepEqual(r9Errors(aliceFoo.errors), [], `alice/foo unexpected errors: ${aliceFoo.errors.join("; ")}`);

    // bob/bar passes too — R9 was skipped because it's not in scope,
    // and the rest of validation has nothing to flag on the fixture.
    assert.deepEqual(r9Errors(bobBar.errors), [], `bob/bar must not surface R9: ${bobBar.errors.join("; ")}`);
  } finally {
    cleanup(root);
  }
});

test("runValidate: R9 DOES fire when an in-scope collection's author mismatches", () => {
  // Negative control: same fixture, PR author is bob, alice/foo is in
  // the changed set. R9 must fire on alice/foo because the PR author
  // doesn't own it.
  const root = withTempRegistry((r) => {
    seedCollection(r, "alice", "foo");
  });
  try {
    const { results, failed } = runValidate({
      repoRoot: root,
      prAuthor: "bob",
      changedIds: new Set(["alice/foo"]),
    });
    const aliceFoo = results.find((r) => r.col.id === "alice/foo");
    assert.equal(r9Errors(aliceFoo.errors).length, 1, `expected exactly one R9 error, got: ${aliceFoo.errors.join("; ")}`);
    assert.match(r9Errors(aliceFoo.errors)[0], /must equal PR author "bob"/);
    assert.equal(failed, 1);
  } finally {
    cleanup(root);
  }
});

test("runValidate: no prAuthor → R9 never fires (local-validation mode)", () => {
  const root = withTempRegistry((r) => {
    seedCollection(r, "alice", "foo");
    seedCollection(r, "bob", "bar");
  });
  try {
    const { results, failed } = runValidate({ repoRoot: root, prAuthor: undefined, changedIds: null });
    for (const r of results) {
      assert.deepEqual(r9Errors(r.errors), [], `${r.col.id} should have no R9 error in local-validation mode`);
    }
    assert.equal(failed, 0);
  } finally {
    cleanup(root);
  }
});
