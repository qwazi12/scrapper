import { test } from "node:test";
import assert from "node:assert/strict";
import { apiError, ApiError } from "../lib/apiErrors.ts";

const response = (status, payload, headers = {}) => new Response(JSON.stringify(payload), { status, headers: { "Content-Type": "application/json", ...headers } });

test("HTTP context errors are readable and retain typed status", async () => {
  const error = await apiError(response(409, { detail: "Comment and source context changed; reload before saving." }));
  assert.ok(error instanceof ApiError); assert.equal(error.status, 409);
  assert.equal(error.message, "409: Comment and source context changed; reload before saving.");
  assert.equal(error.retryAfterSeconds, null);
});

test("validation errors identify fields while excluding private inputs and context", async () => {
  const error = await apiError(response(422, { detail: [{ loc: ["body", "metrics", "views"], msg: "Use a nonnegative integer", input: "private@example.org", ctx: { secret: "private-token" } }] }));
  assert.match(error.message, /metrics.views: Use a nonnegative integer/);
  assert.doesNotMatch(error.message, /private@example|private-token|input|ctx/);
});

test("proxy HTML and malformed payloads never appear in owner messages", async () => {
  for (const body of ["<html>internal-secret-token</html>", '{"detail":{"private":"internal-secret-token"}}', "{broken-json"]) {
    const error = await apiError(new Response(body, { status: 502 }));
    assert.match(error.message, /Check the item's current state/); assert.doesNotMatch(error.message, /internal-secret|broken-json|<html/);
  }
});

test("Retry-After accepts numeric/date forms without scheduling any retry", async () => {
  const now = Date.UTC(2026, 9, 18, 12, 0, 0);
  const numeric = await apiError(response(429, { detail: "Read quota reached." }, { "Retry-After": "60" }), now);
  assert.equal(numeric.retryAfterSeconds, 60); assert.match(numeric.message, /Try again in 60 seconds/);
  const dated = await apiError(response(429, {}, { "Retry-After": new Date(now + 90000).toUTCString() }), now);
  assert.equal(dated.retryAfterSeconds, 90);
  const invalid = await apiError(response(429, {}, { "Retry-After": "not-a-delay" }), now);
  assert.equal(invalid.retryAfterSeconds, null);
  for (const header of ["-1", "1.5", "not-a-date-2026"]) {
    const rejected = await apiError(response(429, {}, { "Retry-After": header }), now);
    assert.equal(rejected.retryAfterSeconds, null);
  }
});

test("oversized validation input is cancelled and stays out of the message", async () => {
  let cancelled = false;
  const bytes = new TextEncoder().encode(JSON.stringify({ detail: [{ msg: "Invalid value", input: "private-".repeat(10000) }] }));
  const stream = new ReadableStream({ pull(controller) { controller.enqueue(bytes); }, cancel() { cancelled = true; } });
  const error = await apiError(new Response(stream, { status: 422 }));
  assert.ok(cancelled); assert.match(error.message, /Some supplied values are invalid/); assert.doesNotMatch(error.message, /private-/);
});

test("expired media links remain distinguishable from refused owner credentials", async () => {
  const error = await apiError(response(401, { detail: "media link expired — reload the page" }));
  assert.match(error.message, /media link/);
});
