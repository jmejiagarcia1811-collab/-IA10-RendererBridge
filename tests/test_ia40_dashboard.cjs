const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const html = fs.readFileSync("ia40/index.html", "utf8");
const match = html.match(/<script>([\\s\\S]*?)<\\/script>/);
assert.ok(match, "IA 4.0 page must contain its client script");

const elements = new Map();
function getElement(id) {
  if (!elements.has(id)) {
    elements.set(id, {
      id,
      textContent: "",
      innerHTML: "",
      value: "",
      disabled: false,
      onclick: null,
      dataset: {},
      classList: { toggle() {} },
      click() {},
      reset() {},
    });
  }
  return elements.get(id);
}
const tabs = ["board", "new", "qc", "integrations", "audit"].map((tab) => ({
  dataset: { tab },
  classList: { toggle() {} },
  onclick: null,
}));
const document = {
  getElementById: getElement,
  querySelectorAll(selector) { return selector === ".tab" ? tabs : []; },
  addEventListener() {},
  querySelector() { return { click() {} }; },
};
const localStorage = {
  getItem() { return null; },
  setItem() {},
  removeItem() {},
};
const fetch = async (url) => {
  if (url.endsWith("/health")) {
    return {
      ok: true,
      json: async () => ({
        database_ok: true,
        database_mode: "postgres",
        queue_counts: {
          QUEUED: 0,
          QC_PENDING_REVIEW: 172,
          FAILED: 13,
          QC_FAILED: 5,
        },
      }),
    };
  }
  if (url.endsWith("/queue-summary")) {
    // The real queue-summary endpoint groups counts by state AND category;
    // it does not return a plain QUEUED or QC_PENDING_REVIEW key.
    return {
      ok: true,
      json: async () => ({
        counts: {
          "QC_PENDING_REVIEW:technical_qc_pass_editorial_review_pending": 172,
          "FAILED:provider_http_403_forbidden": 13,
        },
      }),
    };
  }
  throw new Error("Unexpected URL in read-only dashboard test");
};

const context = {
  document,
  localStorage,
  fetch,
  crypto: { randomUUID: () => "test-id" },
  Date,
  JSON,
  String,
  Number,
  Object,
  Array,
  Promise,
  Blob: class Blob {},
  URL: { createObjectURL() { return "blob:test"; }, revokeObjectURL() {} },
  confirm: () => true,
  console,
};
vm.runInNewContext(match[1], context, { timeout: 1000 });

(async () => {
  await getElement("health").onclick();
  assert.equal(getElement("live-backend").textContent, "Activo");
  assert.equal(getElement("live-db").textContent, "Conectada");
  assert.equal(getElement("live-pending").textContent, "172");
  assert.equal(getElement("live-queued").textContent, "0");
  assert.match(getElement("healthresult").textContent, /Consulta de solo lectura/);
  console.log("PASS: IA 4.0 displays QUEUED/QC_PENDING_REVIEW from /health, not category keys.");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
