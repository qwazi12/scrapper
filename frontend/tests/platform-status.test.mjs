import { test } from "node:test";
import assert from "node:assert/strict";
import { platformChips } from "../lib/platformStatus.ts";

const byNet = (item) => Object.fromEntries(platformChips(item).map((c) => [c.net, c]));

test("posted platforms show their links; a refused one shows why", () => {
  const c = byNet({ status: "posting", accounts: ["Cinama:*"], catchup: null,
    notes: "✓ Cinama:youtube https://www.youtube.com/watch?v=abc",
    publish_requests: [
      { profile: "Cinama", platforms: ["youtube"], state: "completed", request_id: "r1" },
      { profile: "Cinama", platforms: ["instagram"], state: "rejected", error: "Instagram description is too long" },
      { profile: "Cinama", platforms: ["facebook"], state: "unknown", error: "timeout" }] });
  assert.equal(c.youtube.label, "posted"); assert.equal(c.youtube.url, "https://www.youtube.com/watch?v=abc");
  assert.equal(c.instagram.label, "refused"); assert.match(c.instagram.why, /too long/);
  assert.equal(c.facebook.label, "unknown — check the platform");
});

test("a catch-up video shows what posted and which platform it still waits for", () => {
  const c = byNet({ status: "ready", accounts: ["Cinama:youtube"], notes: "",
    catchup: { done: [["Cinama:facebook", "https://fb/1"], ["Cinama:instagram", "https://ig/1"]] },
    publish_requests: [{ skipped: ["Cinama:youtube"] }] });
  assert.equal(c.facebook.label, "posted"); assert.equal(c.instagram.url, "https://ig/1");
  assert.equal(c.youtube.label, "waits for its slot");
});
