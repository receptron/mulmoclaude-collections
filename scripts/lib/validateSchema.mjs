// Host-equivalent collection-schema + record validation (subset).
// Mirrors receptron/mulmoclaude packages/core/src/collection/server/validate.ts
// and safeRecordId from .../server/paths.ts. Kept zero-dep so CI is fast; the
// host remains the source of truth — re-validate on import (R7).

const FIELD_TYPES = new Set([
  "string", "text", "email", "number", "date", "datetime", "boolean",
  "markdown", "money", "enum", "ref", "embed", "table", "derived",
  "image", "file", "toggle",
]);

const COMPUTED_TYPES = new Set(["derived", "embed", "toggle"]);

const RECORD_ID_RE = /^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$/;

export function isSafeRecordId(id) {
  return typeof id === "string" && RECORD_ID_RE.test(id) && !id.includes("..");
}

export function isComputedField(spec) {
  return !!spec && COMPUTED_TYPES.has(spec.type);
}

const isObject = (v) => !!v && typeof v === "object" && !Array.isArray(v);

function validateField(name, spec, fieldNames, errors) {
  if (!isObject(spec)) return errors.push(`field "${name}": must be an object`);
  if (!FIELD_TYPES.has(spec.type)) errors.push(`field "${name}": unknown type "${spec.type}"`);
  if (typeof spec.label !== "string" || !spec.label) errors.push(`field "${name}": missing label`);
  if (spec.type === "enum" && !(Array.isArray(spec.values) && spec.values.length)) errors.push(`field "${name}": enum needs non-empty values[]`);
  if ((spec.type === "ref" || spec.type === "embed") && !spec.to) errors.push(`field "${name}": ${spec.type} needs "to"`);
  if (spec.type === "table" && !isObject(spec.of)) errors.push(`field "${name}": table needs "of"`);
  if (spec.type === "derived" && !spec.formula) errors.push(`field "${name}": derived needs "formula"`);
  if (spec.type === "toggle") validateToggle(name, spec, fieldNames, errors);
  if (spec.when) validateWhen(name, spec.when, fieldNames, errors);
}

function validateToggle(name, spec, fieldNames, errors) {
  if (!spec.field || !fieldNames.includes(spec.field)) errors.push(`field "${name}": toggle.field must name a real field`);
  if (spec.onValue === undefined || spec.offValue === undefined) errors.push(`field "${name}": toggle needs onValue/offValue`);
}

function validateWhen(owner, when, fieldNames, errors) {
  if (!isObject(when) || !when.field || !Array.isArray(when.in)) return errors.push(`"${owner}": when needs { field, in: [] }`);
  if (!fieldNames.includes(when.field)) errors.push(`"${owner}": when.field "${when.field}" is not a real field`);
}

function validateViews(views, errors) {
  if (views === undefined) return;
  if (!Array.isArray(views)) return errors.push(`views must be an array`);
  const ids = new Set();
  for (const v of views) {
    if (!v || !v.id || !v.label || !v.file) errors.push(`view: each entry needs id/label/file`);
    else if (ids.has(v.id)) errors.push(`view: duplicate id "${v.id}"`);
    else ids.add(v.id);
    if (v && v.file && (!v.file.startsWith("views/") || !v.file.endsWith(".html") || v.file.includes(".."))) {
      errors.push(`view "${v && v.id}": file must be a path-safe views/*.html`);
    }
  }
}

/** Returns { errors:[], warnings:[] }. errors non-empty ⇒ invalid. */
export function validateSchema(schema) {
  const errors = [];
  const warnings = [];
  if (!isObject(schema)) return { errors: ["schema.json is not an object"], warnings };
  for (const key of ["title", "icon", "dataPath", "primaryKey"]) {
    if (typeof schema[key] !== "string" || !schema[key]) errors.push(`missing "${key}"`);
  }
  if (!isObject(schema.fields)) {
    errors.push(`missing "fields"`);
    return { errors, warnings };
  }
  const fieldNames = Object.keys(schema.fields);
  for (const [name, spec] of Object.entries(schema.fields)) validateField(name, spec, fieldNames, errors);
  const pk = schema.fields[schema.primaryKey];
  if (!pk) errors.push(`primaryKey "${schema.primaryKey}" is not a field`);
  else if (pk.primary !== true) errors.push(`primaryKey field "${schema.primaryKey}" must set primary: true`);
  validateViews(schema.views, errors);
  if (schema.dataPath && (schema.dataPath.startsWith("/") || schema.dataPath.includes(".."))) {
    errors.push(`dataPath must be a workspace-relative path without ".."`);
  }
  return { errors, warnings };
}

const SECRET_RE = /(AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|sk-[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})/;
const EMAIL_RE = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/;

/** Validate one seed record against the schema. { errors:[], warnings:[] }. */
export function validateRecord(record, schema, idFromFilename) {
  const errors = [];
  const warnings = [];
  if (!isObject(record)) return { errors: ["record is not a JSON object"], warnings };
  const idValue = record[schema.primaryKey];
  if (idValue !== idFromFilename) errors.push(`primaryKey "${schema.primaryKey}" (${idValue}) must equal filename "${idFromFilename}"`);
  if (!isSafeRecordId(idFromFilename)) errors.push(`record id "${idFromFilename}" violates id charset`);
  for (const [name, spec] of Object.entries(schema.fields ?? {})) {
    if (spec.required && record[name] === undefined) errors.push(`missing required field "${name}"`);
    if (isComputedField(spec) && record[name] !== undefined) errors.push(`computed field "${name}" must not be written`);
    if (spec.type === "enum" && record[name] !== undefined && !spec.values.includes(String(record[name]))) {
      errors.push(`field "${name}": "${record[name]}" not in enum values`);
    }
  }
  const raw = JSON.stringify(record);
  if (SECRET_RE.test(raw)) errors.push(`possible credential/secret detected in record`);
  if (EMAIL_RE.test(raw)) warnings.push(`possible email/PII in record (allowed, contributor responsibility)`);
  return { errors, warnings };
}
