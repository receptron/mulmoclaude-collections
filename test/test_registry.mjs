import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

import { listCollections, readJson, summarizeSchema, contentSha } from "../scripts/lib/collections.mjs";
import { isSafeRecordId, validateSchema, validateRecord } from "../scripts/lib/validateSchema.mjs";

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const exampleDir = path.join(REPO_ROOT, "collections", "isamu", "movies");
const exampleSchema = readJson(path.join(exampleDir, "schema.json"));

test("example movies schema is valid", () => {
  assert.deepEqual(validateSchema(exampleSchema).errors, []);
});

test("summarizeSchema reports fields and view labels", () => {
  const { icon, fieldCount, views } = summarizeSchema(exampleSchema);
  assert.equal(icon, "movie");
  assert.ok(fieldCount > 0);
  assert.deepEqual(views, ["シネマ"]);
});

test("schema missing primary flag is rejected", () => {
  const broken = { title: "X", icon: "x", dataPath: "data/x/items", primaryKey: "id", fields: { id: { type: "string", label: "ID" } } };
  assert.match(validateSchema(broken).errors.join("\n"), /must set primary: true/);
});

test("enum without values is rejected", () => {
  const broken = {
    title: "X", icon: "x", dataPath: "data/x/items", primaryKey: "id",
    fields: { id: { type: "string", label: "ID", primary: true }, g: { type: "enum", label: "G" } },
  };
  assert.match(validateSchema(broken).errors.join("\n"), /enum needs non-empty values/);
});

test("isSafeRecordId boundaries", () => {
  for (const ok of ["abc", "a", "1.2.3", "1718900000.123456", "a-b_c.d"]) assert.ok(isSafeRecordId(ok), ok);
  for (const bad of ["", ".x", "x.", "a/b", "a..b", "-x", "x ", "/etc"]) assert.ok(!isSafeRecordId(bad), bad);
});

test("validateRecord: id must match filename", () => {
  const res = validateRecord({ id: "wrong" }, exampleSchema, "right");
  assert.match(res.errors.join("\n"), /must equal filename/);
});

test("validateRecord: enum value must be in range", () => {
  const res = validateRecord({ id: "x", title: "t", genre: "NotAGenre" }, exampleSchema, "x");
  assert.match(res.errors.join("\n"), /not in enum values/);
});

test("validateRecord: credentials are hard-failed, emails warned", () => {
  const secret = validateRecord({ id: "x", title: "t", notes: "ghp_abcdefghijklmnopqrstuvwxyz0123456789" }, exampleSchema, "x");
  assert.match(secret.errors.join("\n"), /credential\/secret/);
  const pii = validateRecord({ id: "x", title: "t", notes: "reach me at a@b.com" }, exampleSchema, "x");
  assert.equal(pii.errors.length, 0);
  assert.match(pii.warnings.join("\n"), /PII/);
});

test("registry has at least one collection and stable contentSha", () => {
  const cols = listCollections(REPO_ROOT);
  assert.ok(cols.length >= 1);
  assert.equal(contentSha(exampleDir), contentSha(exampleDir));
});
