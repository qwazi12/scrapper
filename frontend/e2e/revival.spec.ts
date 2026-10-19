import { test, expect } from "@playwright/test";
import { readFileSync, writeFileSync, copyFileSync, existsSync } from "node:fs";
import { execFileSync } from "node:child_process";
import path from "node:path";

const evidence = path.resolve(__dirname, "../../artifacts/e2e");
const stack = JSON.parse(readFileSync(path.join(evidence, "stack.json"), "utf8"));
if (!stack.api.startsWith("http://127.0.0.1:") || !stack.web.startsWith("http://127.0.0.1:")) throw new Error("Local E2E only");
const headers = { "x-access-token": stack.token };

test.beforeEach(async ({ page, context, request }) => {
  const isolation = await (await request.get(`${stack.api}/api/e2e/isolation`)).json();
  expect(isolation.safe_e2e_mode).toBe(true);
  expect(isolation.real_publish_disabled).toBe(true);
  expect(isolation.worker_mode).toBe("web_only");
  expect(isolation.scheduler_threads).toEqual([]);
  // Independent browser contexts get independent test request windows. Production
  // limits remain unchanged and a dedicated case exercises actual 429/retry.
  const budget = await request.post(`${stack.api}/api/e2e/reset-request-budget`, { headers });
  expect(budget.ok()).toBe(true); expect((await budget.json()).policy_changed).toBe(false);
  await context.route("**/*", route => {
    const hostname = new URL(route.request().url()).hostname;
    return ["127.0.0.1", "localhost"].includes(hostname) ? route.continue() : route.abort("blockedbyclient");
  });
  await page.addInitScript(({ api, token }) => {
    localStorage.setItem("scrapper_api_base", api);
    localStorage.setItem("scrapper_token", token);
  }, { api: stack.api, token: stack.token });
});

function originalAudioFixture() {
  const file = path.join(stack.root, "fixtures/native-original-background.wav");
  if (!existsSync(file)) execFileSync("ffmpeg", ["-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=1100:d=3", file]);
  return file;
}

async function acceptExactExport(page: any, screenshot?: string) {
  const form = page.getByRole("form", { name: "Exact export acceptance" });
  await expect(form).toBeVisible();
  const checks = form.getByRole("checkbox");
  for (let i = 0; i < 4; i++) await checks.nth(i).check();
  const decisions = form.getByRole("textbox", { name: /^Editorial decision / });
  for (let i = 0; i < await decisions.count(); i++) await decisions.nth(i).fill("Synthetic fixture intentionally uses this signal; inspected the generated original media.");
  await form.getByLabel("Rights and intended-use review").fill("All media in this isolated scenario is original synthetic test footage and offline speech.");
  const ack = form.getByRole("checkbox", { name: "I accept these exact files, checks and editorial decisions.", exact: true });
  const save = form.getByRole("button", { name: "Record exact export acceptance", exact: true });
  await expect(save).toBeDisabled();
  await ack.check();
  await form.getByLabel("Rights and intended-use review").fill("Original synthetic test footage, generated tones, and offline fixture speech; no borrowed source assets.");
  await expect(ack).not.toBeChecked();
  await expect(save).toBeDisabled();
  await ack.check();
  if (screenshot) await form.screenshot({ path: path.join(evidence, screenshot) });
  await save.click();
  await expect(form).toHaveCount(0);
}

test("LongForm real UI: research, rejected edit, repair, speech, export approval and simulated delivery", async ({ page, request }) => {
  const errors: string[] = [];
  page.on("pageerror", e => errors.push(e.message));
  page.on("console", msg => { if (msg.type() === "error") errors.push(msg.text()); });
  await page.goto("/#longform");
  await expect(page.getByRole("heading", { name: "🎬 LongForm Studio", exact: true })).toBeVisible();
  await page.getByPlaceholder("Search a movie or TV show…").fill("Synthetic Film 2");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await page.getByRole("button", { name: "Make breakdown", exact: true }).click();
  await expect(page.getByTestId("script-editor")).toBeVisible();
  const pid = Number(new URL(page.url()).hash.split("/")[1]);
  const getProject = async () => (await request.get(`${stack.api}/api/studio/projects/${pid}`, { headers })).json();
  const original = await page.getByTestId("script-editor").inputValue();
  await page.getByTestId("script-editor").fill("INVENTED Robert Pattinson plays Feyd-Rautha.");
  await page.getByRole("button", { name: "Save script", exact: true }).click();
  await expect(page.getByTestId("research-blocked")).toBeVisible();
  await expect(page.getByRole("button", { name: "Voice + shots", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Check saved script", exact: true }).click();
  await expect.poll(async () => (await getProject()).stage_status).toBe("error");
  await expect(page.getByTestId("research-blocked")).toContainText(/unsupported/i);
  await page.screenshot({ path: path.join(evidence, "research-blocked.png"), fullPage: true });
  // A malformed checker cannot make the draft green either.
  await page.getByTestId("script-editor").fill("MALFORMED checker response.");
  await page.getByRole("button", { name: "Save script", exact: true }).click();
  await page.getByRole("button", { name: "Check saved script", exact: true }).click();
  await expect.poll(async () => (await getProject()).script.check_issues.join(" ")).toContain("non-array");
  await expect(page.getByTestId("research-blocked")).toBeVisible();
  await page.getByTestId("script-editor").fill(original);
  await page.getByRole("button", { name: "Save script", exact: true }).click();
  await page.getByRole("button", { name: "Check saved script", exact: true }).click();
  await expect(page.getByRole("button", { name: "Voice + shots", exact: true })).toBeEnabled();
  await expect(page.getByTestId("job-error").filter({ hasText: `Studio #${pid}: verify` })).toHaveCount(0, { timeout: 10_000 });
  await page.reload();
  await expect(page.getByTestId("script-editor")).toHaveValue(original);
  await page.getByRole("button", { name: "Voice + shots", exact: true }).click();
  await expect(page.getByRole("button", { name: "Render video", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Render video", exact: true }).click();
  await expect(page.getByTestId("export-quality")).toContainText("decoded", { timeout: 120_000 });
  const project = await getProject();
  expect(project.render.quality.errors).toEqual([]);
  const actual = path.join(stack.root, "data", "studio", String(pid), project.render.file);
  const final = path.join(evidence, "longform-final.mp4");
  copyFileSync(actual, final);
  const probe = JSON.parse(execFileSync("ffprobe", ["-v", "error", "-show_streams", "-show_format", "-of", "json", final], { encoding: "utf8" }));
  expect(probe.streams.find((s: any) => s.codec_type === "video").width).toBe(1920);
  expect(probe.streams.find((s: any) => s.codec_type === "audio")).toBeTruthy();
  execFileSync("ffmpeg", ["-v", "error", "-xerror", "-i", final, "-f", "null", "-"]);
  writeFileSync(path.join(evidence, "longform-probe.json"), JSON.stringify(probe, null, 2));
  writeFileSync(path.join(evidence, "longform-quality.json"), JSON.stringify(project.render.quality, null, 2));
  await page.screenshot({ path: path.join(evidence, "longform-render-review.png"), fullPage: true });
  await page.getByRole("button", { name: "Approve inspected export", exact: true }).click();
  await acceptExactExport(page);
  await expect(page.getByRole("button", { name: "Send to Posting Queue", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Send to Posting Queue", exact: true }).click();
  await expect(page.getByText(/Ready to Post in SocialPilot/)).toBeVisible();
  const queued = await getProject();
  await page.getByRole("button", { name: /SocialPilot AI/ }).click();
  await page.getByRole("button", { name: "🎬 LongForm Breakdowns", exact: true }).click();
  await page.getByRole("button", { name: /Ready to Post/ }).click();
  await page.screenshot({ path: path.join(evidence, "queue-before-submit.png"), fullPage: true });
  await page.getByRole("button", { name: "🚀 Post Now", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "OK", exact: true }).click();
  const queueStatus = async () => (await (await request.get(`${stack.api}/api/queue`, { headers })).json()).find((q: any) => q.id === queued.queue_item_id)?.status;
  await expect.poll(queueStatus).toBe("posting");
  const duplicate = await request.post(`${stack.api}/api/queue/${queued.queue_item_id}/publish`, { headers });
  expect(duplicate.status()).toBe(409);
  await page.getByRole("button", { name: "Reconcile submitted posts", exact: true }).click();
  await expect.poll(queueStatus).toBe("archived");
  await page.getByRole("button", { name: /Posted Archive/ }).click();
  await page.screenshot({ path: path.join(evidence, "queue-reconciled.png"), fullPage: true });
  writeFileSync(path.join(evidence, "browser-errors.json"), JSON.stringify(errors, null, 2));
  expect(errors).toEqual([]);
});

test("all main navigation pages and same-document hash changes render without browser errors", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", e => errors.push(e.message));
  await page.goto("/");
  for (const hash of ["longform", "countdowns", "comps", "logs", "settings", "socialpilot", "scraper"]) {
    await page.evaluate(hash => { location.hash = hash; }, hash);
    await expect(page).toHaveURL(new RegExp(`#${hash}`));
    await expect(page.locator("main")).not.toBeEmpty();
    await page.screenshot({ path: path.join(evidence, `navigation-${hash}.png`), fullPage: true });
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => { location.hash = "longform"; });
  await expect(page.getByRole("heading", { name: "🎬 LongForm Studio", exact: true })).toBeVisible();
  await page.screenshot({ path: path.join(evidence, "mobile-longform.png"), fullPage: true });
  expect(errors).toEqual([]);
});

test("Top 5 real UI: five independent segment pipelines, final stitch, inspection and approval", async ({ page, request }) => {
  const startingReceipts = (await (await request.get(`${stack.api}/api/e2e/isolation`)).json()).fake_publish_receipts;
  test.setTimeout(300_000);
  const errors: string[] = [];
  page.on("pageerror", e => errors.push(e.message));
  page.on("console", msg => { if (msg.type() === "error") errors.push(msg.text()); });
  await page.goto("/#countdowns");
  await page.getByPlaceholder("Type a topic, e.g. Top 10 horror movies on Netflix right now").fill("Top 5 Synthetic Animated Films");
  await page.getByRole("button", { name: "Top 5", exact: true }).click();
  await page.getByRole("button", { name: "Research & rank", exact: true }).click();
  await expect(page.getByRole("button", { name: "Confirm titles", exact: true })).toBeEnabled();
  const cid = Number(new URL(page.url()).hash.split("/")[1]);
  const getCountdown = async () => (await request.get(`${stack.api}/api/countdowns/${cid}`, { headers })).json();
  const researched = await getCountdown();
  expect(researched.entry_count).toBe(5);
  expect(new Set(researched.ranking.entries.map((e: any) => e.tmdb_id)).size).toBe(5);
  await page.getByText("Edit editorial ranking rationale", { exact: true }).first().click();
  await page.getByLabel("Ranking rationale for Synthetic Film 1", { exact: true }).fill("Our editorial preference favors the visual sequence in this original synthetic example.");
  await page.getByRole("button", { name: "Save ranking rationale", exact: true }).click();
  await expect.poll(async () => (await getCountdown()).ranking.entries[0].why_it_ranks).toContain("Our editorial preference");
  await page.getByRole("button", { name: "Confirm titles", exact: true }).click();
  await page.getByRole("button", { name: "▶ Make segments", exact: true }).click();
  await expect.poll(async () => (await getCountdown()).segments_made, { timeout: 180_000 }).toBe(5);
  await expect(page.getByRole("button", { name: "Render video", exact: true })).toBeDisabled();
  const beforeBookends = await request.post(`${stack.api}/api/countdowns/${cid}/render`, { headers });
  expect(beforeBookends.status()).toBe(409);
  const bookendCheck = page.getByRole("checkbox", { name: /I checked the saved intro and outro/ });
  await bookendCheck.check();
  const beforeMetadataChange = await getCountdown();
  const metadataChange = await request.put(`${stack.api}/api/countdowns/${cid}/script`, { headers,
    data: { title: `${beforeMetadataChange.script.title} — Editorial Picks`, expected_script_version: beforeMetadataChange.script_version } });
  expect(metadataChange.ok()).toBe(true);
  await expect(bookendCheck).not.toBeChecked();
  await expect(page.getByRole("button", { name: "Mark intro/outro reviewed", exact: true })).toBeDisabled();
  await bookendCheck.check();
  await page.getByRole("button", { name: "Mark intro/outro reviewed", exact: true }).click();
  await expect(page.getByRole("button", { name: "Render video", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Render video", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "OK", exact: true }).click();
  await expect(page.getByTestId("countdown-export-quality")).toContainText("decoded", { timeout: 120_000 });
  const completed = await getCountdown();
  expect(completed.render.quality.errors).toEqual([]);
  expect(completed.render.compilation_id).toMatch(/^[a-f0-9]{32}$/);
  const final = path.join(evidence, "countdown-top5-final.mp4");
  copyFileSync(completed.render.file, final);
  const probe = JSON.parse(execFileSync("ffprobe", ["-v", "error", "-show_streams", "-show_format", "-of", "json", final], { encoding: "utf8" }));
  expect(probe.streams.some((s: any) => s.codec_type === "audio")).toBe(true);
  expect(probe.streams.find((s: any) => s.codec_type === "video").height).toBe(1080);
  execFileSync("ffmpeg", ["-v", "error", "-xerror", "-i", final, "-f", "null", "-"]);
  writeFileSync(path.join(evidence, "countdown-probe.json"), JSON.stringify(probe, null, 2));
  writeFileSync(path.join(evidence, "countdown-quality.json"), JSON.stringify(completed.render.quality, null, 2));
  await page.getByRole("button", { name: "🔄 Refresh Options", exact: true }).click();
  await expect.poll(async () => (await getCountdown()).render.thumb_round).toBe(1);
  await page.screenshot({ path: path.join(evidence, "countdown-review.png"), fullPage: true });
  await page.getByRole("button", { name: "Approve inspected countdown", exact: true }).click();
  await acceptExactExport(page);
  await page.getByRole("button", { name: "📤 Send to Countdowns queue", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "OK", exact: true }).click();
  await expect.poll(async () => !!(await getCountdown()).queue_item_id).toBe(true);
  const queuedExport = await getCountdown();
  const queuedThumbnail = readFileSync(queuedExport.render.thumbnail);
  const replacement = await request.post(`${stack.api}/api/countdowns/${cid}/thumbnail`, { headers, data: { option: "action" } });
  expect(replacement.status()).toBe(409);
  expect(readFileSync(queuedExport.render.thumbnail).equals(queuedThumbnail)).toBe(true);
  await page.screenshot({ path: path.join(evidence, "countdown-queued.png"), fullPage: true });
  writeFileSync(path.join(evidence, "countdown-browser-errors.json"), JSON.stringify(errors, null, 2));
  const isolation = await (await request.get(`${stack.api}/api/e2e/isolation`)).json();
  writeFileSync(path.join(evidence, "final-isolation.json"), JSON.stringify(isolation, null, 2));
  expect(isolation.blocked_network_attempts).toBe(0);
  expect(isolation.scheduler_threads).toEqual([]);
  expect(isolation.fake_publish_receipts).toBe(startingReceipts);  // No automatic submission during the Countdown workflow.
  // Reproduce the immediate post-start stale GET, independently of timing.
  let injectedCountdownDone = false;
  await page.route(new RegExp(`/api/countdowns/${cid}$`), async route => {
    if (!injectedCountdownDone) {
      injectedCountdownDone = true;
      const response = await route.fetch();
      await route.fulfill({ response, json: { ...completed, stage: "segments", stage_status: "done",
        stage_message: "All 5 segments made", render: null, has: { ...completed.has, render: false } } });
    } else await route.continue();
  });
  await page.reload();
  await expect(page.getByTestId("countdown-export-quality")).toContainText("decoded");
  expect(injectedCountdownDone).toBe(true);
  expect(errors).toEqual([]);
});

test("Editorial UI: reviewed research, cleared still, native Short and carousel, simulated delivery", async ({ page, request }) => {
  test.setTimeout(300_000);
  const errors: string[] = [];
  page.on("pageerror", e => errors.push(e.message));
  await page.goto("/#editorial");
  await page.getByLabel("Topic", { exact: true }).fill("Synthetic Native Shapes");
  await page.getByLabel("Original angle", { exact: true }).fill("Show how one original shape shifts the emphasis of a frame across three deliberate visual beats.");
  await page.getByLabel("Audience and question", { exact: true }).fill("Viewers learning visual storytelling: which frame feels clearer?");
  await page.getByLabel("Research source URL", { exact: true }).fill("https://example.invalid/original-test-assets");
  await page.getByLabel("Supporting excerpt", { exact: true }).fill("The original still and colored shape sequence were created in this isolated test. No third-party footage or factual claims are used.");
  await page.getByLabel("Planned time (your local timezone)").fill("2026-10-20T12:00");
  await page.getByRole("button", { name: "Create opportunity", exact: true }).click();
  const opportunity = page.getByRole("article").filter({ has: page.getByRole("heading", { name: "Synthetic Native Shapes", exact: true }) });
  await expect(opportunity).toContainText("Demand: Unavailable");
  await opportunity.getByRole("button", { name: "Approve research brief", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "OK", exact: true }).click();
  await expect(opportunity).toContainText("approved");
  await page.getByRole("button", { name: "Native campaigns", exact: true }).click();
  await page.getByText("Import a cleared still", { exact: true }).click();
  await page.getByLabel("Still image", { exact: true }).setInputFiles(path.join(stack.root, "fixtures/poster.jpg"));
  await page.getByLabel("Rights record", { exact: true }).fill("Original colored shape fixture created locally by the test harness; permitted for all synthetic test treatments.");
  await page.getByLabel("Scene tags", { exact: true }).fill("original shapes, synthetic frame");
  await page.getByRole("button", { name: "Index cleared still", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Still indexed" })).toBeVisible();
  const opportunities = await (await request.get(`${stack.api}/api/editorial/opportunities`, { headers })).json();
  const oid = opportunities.find((o: any) => o.title === "Synthetic Native Shapes").id;
  await page.getByLabel("Approved research", { exact: true }).selectOption(String(oid));
  const texts = ["Can a simple shape change the way you read a frame?",
    "Moving our original shape shifts attention from one side of the frame to the other.",
    "The strongest frame makes one clear choice. Which version would you keep?"];
  for (let i = 0; i < 3; i++) await page.getByLabel(`Beat text ${i + 1}`, { exact: true }).fill(texts[i]);
  const assets = await (await request.get(`${stack.api}/api/editorial/assets`, { headers })).json();
  await page.getByLabel("Visual for beat 2", { exact: true }).selectOption(String(assets[0].id));
  await page.getByLabel("Short caption", { exact: true }).fill("Watch our original shape shift attention across the frame. Which composition would you keep?");
  await page.getByLabel("Carousel caption", { exact: true }).fill("Swipe through three original visual choices. Pause on the clearest frame and tell us why.");
  await page.getByLabel("Community question draft", { exact: true }).fill("Which visual change made the biggest difference to your reading of the frame?");
  await page.getByRole("button", { name: "Create native campaign", exact: true }).click();
  const campaigns = async () => (await request.get(`${stack.api}/api/editorial/campaigns`, { headers })).json();
  await expect.poll(async () => (await campaigns()).length).toBe(1);
  const cid = (await campaigns())[0].id;
  const campaign = page.getByTestId(`campaign-${cid}`);
  const queueIds: number[] = [];
  for (const fmt of ["short", "carousel"]) {
    await campaign.getByRole("button", { name: `Render ${fmt}`, exact: true }).click();
    const review = campaign.getByTestId(`native-export-${fmt}`);
    await expect(review).toContainText("Final decode: passed", { timeout: 180_000 });
    const item = (await campaigns())[0].exports.find((e: any) => e.format === fmt);
    expect(item.manifest.quality.errors).toEqual([]);
    const premature = await request.post(`${stack.api}/api/editorial/exports/${item.id}/enqueue`, { headers, data: { accounts: ["default:instagram"] } });
    expect(premature.status()).toBe(409);
    if (fmt === "short") {
      const final = path.join(evidence, "native-short-final.mp4");
      copyFileSync(item.manifest.video.file, final);
      const probe = JSON.parse(execFileSync("ffprobe", ["-v", "error", "-show_streams", "-show_format", "-of", "json", final], { encoding: "utf8" }));
      const video = probe.streams.find((s: any) => s.codec_type === "video");
      expect([video.width, video.height]).toEqual([1080, 1920]);
      expect(probe.streams.some((s: any) => s.codec_type === "audio")).toBe(true);
      expect(item.manifest.timeline.map((b: any) => b.text)).toEqual(texts);
      expect(item.manifest.cards.every((c: any) => c.caption_lines > 0 && c.caption_lines <= 5)).toBe(true);
      execFileSync("ffmpeg", ["-v", "error", "-xerror", "-i", final, "-f", "null", "-"]);
      writeFileSync(path.join(evidence, "native-short-probe.json"), JSON.stringify(probe, null, 2));
    } else {
      expect(item.manifest.cards).toHaveLength(3);
      for (const [i, card] of item.manifest.cards.entries()) copyFileSync(card.file, path.join(evidence, `native-carousel-${i + 1}.png`));
    }
    writeFileSync(path.join(evidence, `native-${fmt}-manifest.json`), JSON.stringify(item.manifest, null, 2));
    await review.screenshot({ path: path.join(evidence, `native-${fmt}-review.png`) });
    await review.getByRole("button", { name: `Approve inspected ${fmt}`, exact: true }).click();
    await acceptExactExport(page, `native-${fmt}-acceptance.png`);
    await review.getByRole("checkbox", { name: "default:instagram", exact: true }).check();
    await review.getByRole("button", { name: `Add ${fmt} to review queue`, exact: true }).click();
    await expect(review).toContainText("Queue item #");
    queueIds.push((await campaigns())[0].exports.find((e: any) => e.format === fmt).queue_item_id);
  }
  await page.getByRole("button", { name: /SocialPilot AI/ }).click();
  await page.getByRole("button", { name: /All Items/ }).click();
  const status = async (id: number) => (await (await request.get(`${stack.api}/api/queue`, { headers })).json()).find((q: any) => q.id === id)?.status;
  for (const id of queueIds) {
    const row = page.getByRole("row").filter({ has: page.getByText(`#${id}`, { exact: true }) });
    await row.getByRole("button", { name: "✓ Approve", exact: true }).click();
    await expect.poll(() => status(id)).toBe("ready");
    await row.getByRole("button", { name: "🚀 Post Now", exact: true }).click();
    await page.getByRole("dialog").getByRole("button", { name: "OK", exact: true }).click();
    await expect.poll(() => status(id)).toBe("posting");
  }
  await page.getByRole("button", { name: "Reconcile submitted posts", exact: true }).click();
  for (const id of queueIds) await expect.poll(() => status(id)).toBe("archived");
  const photos = readFileSync(path.join(stack.root, "fake-photo-provider.jsonl"), "utf8").trim().split("\n").map(x => JSON.parse(x));
  expect(photos).toHaveLength(1); expect(photos[0].files).toHaveLength(3);
  await page.goto("/#editorial");
  await page.getByRole("button", { name: "Performance evidence", exact: true }).click();
  await expect(page.getByText("youtube: unavailable", { exact: true })).toBeVisible();
  await page.getByLabel("Content ID", { exact: true }).fill("synthetic-native-short");
  await page.getByLabel("Published at", { exact: true }).fill("2026-10-14T12:00");
  await page.getByLabel("Measured at", { exact: true }).fill("2026-10-15T12:00");
  await page.getByLabel("Views (leave missing measures blank)", { exact: true }).fill("125");
  await page.getByLabel("Export source and reporting period", { exact: true }).fill("Synthetic local observation fixture at 24 hours; no real performance claim");
  await page.getByRole("button", { name: "Import observation", exact: true }).click();
  await expect(page.getByRole("row").filter({ hasText: "youtube / short" })).toContainText("125");
  await page.screenshot({ path: path.join(evidence, "editorial-performance.png"), fullPage: true });
  await page.getByRole("button", { name: "Audience & experiments", exact: true }).click();
  await page.getByLabel("Source content ID", { exact: true }).fill("synthetic-native-short");
  await page.getByLabel("Comment export source", { exact: true }).fill("Authorized synthetic local comment export; no real audience");
  await page.getByLabel("Comment texts", { exact: true }).fill("How does framing guide attention? Contact viewer@example.org @syntheticviewer\nActually that credit is incorrect; check the original source.");
  await page.getByRole("button", { name: "Import private feedback", exact: true }).click();
  await expect(page.getByRole("blockquote").filter({ hasText: "How does framing" })).toContainText("[email removed]");
  await expect(page.getByRole("blockquote").filter({ hasText: "How does framing" })).not.toContainText("viewer@example.org");
  const question = page.getByRole("article").filter({ has: page.getByRole("heading", { name: "question", exact: true }) });
  await question.getByRole("textbox").fill("Thanks for the question. We will inspect the original framing examples before drafting a follow-up.");
  await question.getByRole("checkbox", { name: "I reviewed this exact private draft and current source/thread context.", exact: true }).check();
  await question.getByRole("button", { name: "Mark reviewed for manual use", exact: true }).click();
  await expect(question).toContainText("Nothing sent.");
  await page.getByLabel("Experiment name", { exact: true }).fill("Synthetic opening question experiment");
  await page.getByLabel("Hypothesis", { exact: true }).fill("An original visual question may improve engaged views at the same publication age.");
  await page.getByLabel("Control content IDs (comma separated)").fill("synthetic-control");
  await page.getByLabel("Variant content IDs (comma separated)").fill("synthetic-native-short");
  await page.getByLabel("Controls and known confounders").fill("Keep subject, length and audience comparable; vary the opening question only.");
  await page.getByRole("button", { name: "Register experiment", exact: true }).click();
  await expect(page.getByRole("article").filter({ has: page.getByRole("heading", { name: "Synthetic opening question experiment", exact: true }) })).toContainText("insufficient comparable data");
  await page.screenshot({ path: path.join(evidence, "audience-private-drafts.png"), fullPage: true });
  await question.getByRole("button", { name: "Draft follow-up opportunity", exact: true }).click();
  await expect(page.getByLabel("Topic", { exact: true })).toHaveValue(/How does framing/);
  await page.getByRole("button", { name: "Create opportunity", exact: true }).click();
  await expect(page.getByLabel("Topic", { exact: true })).toHaveValue("");
  const followups = await (await request.get(`${stack.api}/api/editorial/opportunities`, { headers })).json();
  expect(followups.find((o: any) => o.title.startsWith("How does framing")).feedback_refs).toHaveLength(1);
  const isolation = await (await request.get(`${stack.api}/api/e2e/isolation`)).json();
  expect(isolation.blocked_network_attempts).toBe(0); expect(isolation.scheduler_threads).toEqual([]);
  expect(isolation.external_spend_usd).toBe(0);
  writeFileSync(path.join(evidence, "native-final-isolation.json"), JSON.stringify(isolation, null, 2));
  writeFileSync(path.join(evidence, "native-browser-errors.json"), JSON.stringify(errors, null, 2));
  expect(errors).toEqual([]);
});

test("studio project fragments, back/forward and reload keep the intended project", async ({ page, request }) => {
  const projects = await (await request.get(`${stack.api}/api/studio/projects`, { headers })).json();
  const first = projects.find((p: any) => p.title === "Synthetic Film 2");
  expect(first).toBeTruthy();
  const number = Math.max(5, ...projects.map((p: any) => Number(/Synthetic Film (\d+)/.exec(p.title)?.[1]) || 0)) + 1;
  const title = `Synthetic Film ${number}`;
  let injectedInterstage = false;
  await page.route(/\/api\/studio\/projects\/\d+$/, async route => {
    const response = await route.fetch();
    const body = await response.json();
    if (body.title === title && !injectedInterstage) {
      injectedInterstage = true;
      await route.fulfill({ response, json: { ...body, stage: "gather", stage_status: "done", script: null,
        plan: null, render: null, has: { ...body.has, script: false, plan: false, render: false } } });
    } else await route.fulfill({ response });
  });
  await page.goto("/#longform");
  await page.getByPlaceholder("Search a movie or TV show…").fill(title);
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await page.getByRole("button", { name: "Make breakdown", exact: true }).click();
  await expect(page.getByTestId("script-editor")).toBeVisible();
  expect(injectedInterstage).toBe(true);
  const second = Number(new URL(page.url()).hash.split("/")[1]);
  expect(second).not.toBe(first.id);
  await page.evaluate(id => { window.location.hash = `longform/${id}`; }, first.id);
  await expect(page.getByRole("heading", { name: "Synthetic Film 2", exact: true })).toBeVisible();
  await page.evaluate(id => { window.location.hash = `longform/${id}`; }, second);
  await expect(page.getByRole("heading", { name: title, exact: true })).toBeVisible();
  await page.goBack();
  await expect(page.getByRole("heading", { name: "Synthetic Film 2", exact: true })).toBeVisible();
  await page.goForward();
  await expect(page.getByRole("heading", { name: title, exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: title, exact: true })).toBeVisible();
  await page.screenshot({ path: path.join(evidence, "same-studio-navigation.png"), fullPage: true });
});


test("independent source review UI retains records and invalidates checked narration", async ({ page, request }) => {
  await page.goto("/#longform");
  await page.getByPlaceholder("Search a movie or TV show…").fill("Synthetic Film 18");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await page.getByRole("button", { name: "Make breakdown", exact: true }).click();
  await expect(page.getByTestId("script-editor")).toBeVisible();
  const pid = Number(new URL(page.url()).hash.split("/")[1]);
  await page.getByText("Research — facts & sources", { exact: true }).click();
  await expect(page.getByText("Unverified — excluded from narrated web facts", { exact: true })).toBeVisible();
  await page.getByText("Review independent evidence", { exact: true }).click();
  await page.getByRole("button", { name: "Retrieve Synthetic offline article", exact: true }).click();
  await page.getByLabel("Exact supporting or conflicting excerpt").fill("Original colored shapes change through six scenes.");
  await page.getByLabel("Verdict", { exact: true }).selectOption("supported");
  await page.getByLabel("Why the source supports or contradicts this claim").fill("The offline synthetic record directly describes these original scenes; the title, medium, year and territory were checked.");
  await page.getByRole("checkbox", { name: /I checked the exact context/ }).check();
  await page.getByLabel("Why the source supports or contradicts this claim").fill("The original synthetic record was read in exact title, release year, medium and territory context before this decision.");
  await expect(page.getByRole("checkbox", { name: /I checked the exact context/ })).not.toBeChecked();
  await expect(page.getByRole("button", { name: "Save evidence review", exact: true })).toBeDisabled();
  await page.getByRole("checkbox", { name: /I checked the exact context/ }).check();
  await page.getByRole("button", { name: "Save evidence review", exact: true }).click();
  await expect(page.getByText(/Owner verdict: supported/)).toBeVisible();
  await expect(page.getByTestId("research-blocked")).toContainText("Source evidence changed");
  await expect(page.getByRole("button", { name: "Voice + shots", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Check saved script", exact: true }).click();
  await expect(page.getByRole("button", { name: "Voice + shots", exact: true })).toBeEnabled();
  await page.reload();
  const project = await (await request.get(`${stack.api}/api/studio/projects/${pid}`, { headers })).json();
  expect(project.script.storyboard.R).toHaveLength(1);
  expect(project.research.claims[0].evidence.scope).toBe("independently_retrieved_owner_reviewed");
  await page.getByText("Research — facts & sources", { exact: true }).click();
  await expect(page.getByText(/Owner verdict: supported/)).toBeVisible();
  await page.screenshot({ path: path.join(evidence, "independent-source-review.png"), fullPage: true });
  await page.getByText("Review independent evidence", { exact: true }).click();
  const records = await (await request.get(`${stack.api}/api/studio/projects/${pid}/source-records`, { headers })).json();
  await page.getByLabel("Retrieved source record", { exact: true }).selectOption(records.records[0].id);
  await page.getByLabel("Exact supporting or conflicting excerpt").fill("Original colored shapes change through six scenes.");
  await page.getByLabel("Verdict", { exact: true }).selectOption("contradicted");
  await page.getByLabel("Why the source supports or contradicts this claim").fill("A synthetic correction disputes the earlier reading; an explicit review is needed before restoring support.");
  await page.getByRole("checkbox", { name: /I checked the exact context/ }).check();
  await page.getByRole("button", { name: "Save evidence review", exact: true }).click();
  await expect(page.getByText(/Owner verdict: contradicted/)).toBeVisible();
  await page.getByLabel("Verdict", { exact: true }).selectOption("supported");
  await page.getByRole("checkbox", { name: /I checked the exact context/ }).check();
  await expect(page.getByRole("button", { name: "Save evidence review", exact: true })).toBeDisabled();
  await expect(page.getByRole("group", { name: "Earlier conflicting or unresolved reviews", exact: true })).toContainText("A synthetic correction disputes");
  await page.getByRole("checkbox", { name: /I reviewed this earlier decision/ }).check();
  await page.getByLabel("How the earlier evidence was resolved").fill("I compared the exact synthetic source context with the earlier decision; the original scene description remains accurate.");
  await expect(page.getByRole("checkbox", { name: /I checked the exact context/ })).not.toBeChecked();
  await page.getByRole("checkbox", { name: /I checked the exact context/ }).check();
  await page.getByRole("button", { name: "Save evidence review", exact: true }).click();
  await expect(page.getByText(/Owner verdict: supported/)).toBeVisible();
  await page.getByRole("button", { name: "Check saved script", exact: true }).click();
  await expect(page.getByRole("button", { name: "Voice + shots", exact: true })).toBeEnabled();
  await page.screenshot({ path: path.join(evidence, "independent-conflict-resolution.png"), fullPage: true });
});

test("residential worker claims, uploads over real HTTP and resumes the waiting Studio UI", async ({ page, request }) => {
  await page.goto("/#longform");
  await page.getByPlaceholder("Search a movie or TV show…").fill("Synthetic Film 40");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await page.getByRole("button", { name: "Make breakdown", exact: true }).click();
  await expect(page.getByText(/queued for the home Mac worker.*run again once it arrives/).first()).toBeVisible();
  const pid = Number(new URL(page.url()).hash.split("/")[1]);
  const project = async () => (await request.get(`${stack.api}/api/studio/projects/${pid}`, { headers })).json();
  const waiting = await project();
  expect(waiting.stage_status).toBe("awaiting_worker");
  expect(waiting.has.trailer).toBe(false); expect(waiting.has.script).toBe(false);
  const clipId = waiting.trailer.pending_clip_id;
  await page.reload();
  await expect(page.getByRole("button", { name: /Continue to step 5 \(from 2. Footage\)/ })).toBeEnabled();
  const recovered = await request.post(`${stack.api}/api/e2e/residential-run`);
  expect(recovered.ok()).toBe(true); expect((await recovered.json()).recovered).toBe(1);
  await page.getByRole("button", { name: /Continue to step 5 \(from 2. Footage\)/ }).click();
  await expect(page.getByRole("button", { name: "✓ Steps 1–5 done", exact: true })).toBeVisible();
  const done = await project();
  expect(done.has.plan).toBe(true); expect(done.script_ready).toBe(true);
  expect(done.trailer.sources[0].id).toBe(`yt-clip-${clipId}`);
  const clips = await (await request.get(`${stack.api}/api/clips`, { headers })).json();
  expect(clips.find((c: any) => c.id === clipId).status).toBe("done");
  const again = await (await request.post(`${stack.api}/api/e2e/residential-run`)).json();
  expect(again.recovered).toBe(0);
  await page.reload();
  await expect(page.getByRole("button", { name: "✓ Steps 1–5 done", exact: true })).toBeVisible();
  await page.screenshot({ path: path.join(evidence, "residential-worker-resumed.png"), fullPage: true });
});

test("uncertain publication is resolved through owner evidence UI without duplicate submission", async ({ page, request }) => {
  const { id } = await (await request.post(`${stack.api}/api/e2e/uncertain-queue`)).json();
  await page.goto("/");
  await page.getByRole("button", { name: /SocialPilot AI/ }).click();
  await page.getByRole("button", { name: /All Items/ }).click();
  const row = page.getByRole("row").filter({ has: page.getByText(`#${id}`, { exact: true }) });
  await row.getByRole("button", { name: "✓ Approve", exact: true }).click();
  await row.getByRole("button", { name: "🚀 Post Now", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "OK", exact: true }).click();
  const receiptView = async () => (await request.get(`${stack.api}/api/queue/${id}/publication-evidence`, { headers })).json();
  await expect.poll(async () => (await receiptView()).current[0]?.entry.state).toBe("unknown");
  const before = await (await request.get(`${stack.api}/api/e2e/isolation`)).json();
  const ledger = readFileSync(path.join(stack.root, "fake-provider.jsonl"), "utf8").trim().split("\n").map(x => JSON.parse(x));
  const rid = ledger[ledger.length - 1].request_id;
  await page.getByRole("button", { name: "Publication evidence", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Publication evidence", exact: true });
  await dialog.getByLabel("Queue item", { exact: true }).selectOption(String(id));
  await dialog.getByLabel("Provider review outcome").selectOption("accepted");
  await dialog.getByLabel("Provider request ID").fill(rid);
  await dialog.getByLabel("Provider evidence URL").fill(`https://provider.example/receipts/${rid}`);
  await dialog.getByLabel("Review rationale").fill("I inspected the exact synthetic account and provider ledger; this receipt records the uncertain submission.");
  await expect(dialog.getByRole("button", { name: "Save submission evidence", exact: true })).toBeDisabled();
  await dialog.getByRole("checkbox", { name: /I reviewed this exact account/ }).check();
  await dialog.getByRole("button", { name: "Save submission evidence", exact: true }).click();
  await expect(dialog.getByRole("status")).toContainText("Evidence saved");
  await dialog.getByText(/Append-only history/).click();
  await expect(dialog.getByText("owner_decision", { exact: true })).toBeVisible();
  await page.screenshot({ path: path.join(evidence, "publication-owner-evidence.png"), fullPage: true });
  await dialog.getByRole("button", { name: "Close evidence", exact: true }).click();
  await page.getByRole("button", { name: "Reconcile submitted posts", exact: true }).click();
  await expect.poll(async () => (await receiptView()).status).toBe("archived");
  const after = await (await request.get(`${stack.api}/api/e2e/isolation`)).json();
  expect(after.fake_publish_receipts).toBe(before.fake_publish_receipts);
  expect(after.blocked_network_attempts).toBe(0); expect(after.external_spend_usd).toBe(0);
  writeFileSync(path.join(evidence, "worker-journal-isolation.json"), JSON.stringify(after, null, 2));
});

test("financial holds UI requires explicit orphan review and reconciles USD and credits locally", async ({ page, request }) => {
  const ids = await (await request.post(`${stack.api}/api/e2e/financial-holds`)).json();
  await page.goto("/#settings");
  await page.getByText(/Unsettled requests & cost contracts/).click();
  await page.getByLabel("Unsettled request", { exact: true }).selectOption(ids.usd);
  await page.getByLabel("Confirmed actual amount (USD)").fill("0.2");
  await page.getByLabel("Provider receipt reference", { exact: true }).fill("synthetic-usd-receipt");
  await page.getByLabel("Financial reconciliation rationale").fill("Reviewed this synthetic request and its exact provider billing receipt.");
  const save = page.getByRole("button", { name: "Record financial reconciliation", exact: true });
  await expect(save).toBeDisabled();
  await page.getByRole("checkbox", { name: /I reviewed this exact request against/ }).check();
  await expect(save).toBeDisabled();
  await page.getByRole("checkbox", { name: /I confirm the interrupted request worker/ }).check();
  const reviewed = page.getByRole("checkbox", { name: /I reviewed this exact request against/ });
  await expect(reviewed).not.toBeChecked();
  await expect(save).toBeDisabled();
  await reviewed.check();
  await page.getByLabel("Financial reconciliation rationale").fill("Reviewed the interrupted synthetic request against its exact revised provider receipt.");
  await expect(reviewed).not.toBeChecked();
  await expect(save).toBeDisabled();
  await reviewed.check();
  await save.click();
  await expect(page.getByRole("status").filter({ hasText: "local ledger has been reconciled" })).toBeVisible();
  await page.getByLabel("Unsettled request", { exact: true }).selectOption(ids.credit);
  await page.getByLabel("Confirmed actual amount (credits)").fill("3");
  await page.getByLabel("Provider receipt reference", { exact: true }).fill("synthetic-credit-receipt");
  await page.getByLabel("Financial reconciliation rationale").fill("Reviewed the synthetic credit receipt; three credits were confirmed for this request.");
  await page.getByRole("checkbox", { name: /I reviewed this exact request against/ }).check();
  await save.click();
  await expect(page.getByRole("status").filter({ hasText: "local ledger has been reconciled" })).toBeVisible();
  await page.getByText("Per-request USD ceilings", { exact: true }).click();
  await page.getByLabel("Provider service and model").fill("synthetic-finance:local-model");
  await page.getByLabel("Maximum USD per request").fill("0.125");
  await page.getByRole("button", { name: "Save request ceiling", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Request ceilings saved locally" })).toBeVisible();
  const ledger = await (await request.get(`${stack.api}/api/financial/holds`, { headers })).json();
  expect(ledger.holds).toEqual([]);
  expect(ledger.history.filter((r: any) => [ids.usd, ids.credit].includes(r.reservation_id))).toHaveLength(2);
  const costs = await (await request.get(`${stack.api}/api/costs`, { headers })).json();
  expect(costs.settings.request_bounds["synthetic-finance:local-model"]).toBe(0.125);
  await page.screenshot({ path: path.join(evidence, "financial-hold-review.png"), fullPage: true });
});

test("unsaved Studio and Countdown scripts survive polling, navigation, reload and conflicting saves", async ({ page, request }) => {
  await page.goto("/#longform");
  await page.getByPlaceholder("Search a movie or TV show…").fill("Synthetic Film 50");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await page.getByRole("button", { name: "Make breakdown", exact: true }).click();
  const editor = page.getByTestId("script-editor");
  await expect(editor).toHaveValue(/Original colored shapes/);
  const pid = Number(new URL(page.url()).hash.split("/")[1]);
  const original = await editor.inputValue();
  const draft = original + "\nWhich unsaved draft should we keep?";
  await editor.fill(draft);
  await expect(page.getByRole("status").filter({ hasText: "Unsaved draft stored" })).toBeVisible();
  await page.goto("/#settings");
  await page.goto(`/#longform/${pid}`);
  await expect(page.getByRole("status").filter({ hasText: "recovered unsaved draft" })).toBeVisible();
  await expect(editor).toHaveValue(original);
  await page.getByRole("button", { name: "Restore unsaved draft", exact: true }).click();
  await expect(editor).toHaveValue(draft);
  const project = await (await request.get(`${stack.api}/api/studio/projects/${pid}`, { headers })).json();
  const newer = { ...project.script, sentences: [{ paragraph: 1, text: "A newer saved script." }] };
  const update = await request.patch(`${stack.api}/api/studio/projects/${pid}`, { headers,
    data: { script: newer, expected_script_version: project.script_version } });
  expect(update.ok()).toBe(true);
  await page.reload();
  await page.getByRole("button", { name: "Restore unsaved draft", exact: true }).click();
  await expect(editor).toHaveValue(draft);
  await expect(page.getByRole("alert").filter({ hasText: "saved script also changed" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Save script", exact: true })).toBeDisabled();
  await page.getByText("Current saved script", { exact: true }).click();
  await expect(page.getByText("A newer saved script.", { exact: true })).toBeVisible();
  // Restoring a browser draft does not write to the backend.
  expect((await (await request.get(`${stack.api}/api/studio/projects/${pid}`, { headers })).json()).script.sentences[0].text).toBe("A newer saved script.");
  await page.getByRole("button", { name: "Use saved version", exact: true }).click();
  await expect(editor).toHaveValue("A newer saved script.");
  await editor.fill("Which reviewed draft should we keep?");
  await page.getByRole("button", { name: "Save script", exact: true }).click();
  await expect(page.getByText(/Saved. If the words changed/)).toBeVisible();
  await page.reload();
  await expect(editor).toHaveValue("Which reviewed draft should we keep?");
  await expect(page.getByRole("button", { name: "Restore unsaved draft", exact: true })).toHaveCount(0);

  let c = (await (await request.get(`${stack.api}/api/countdowns`, { headers })).json()).find((x: any) => x.has.titles);
  if (!c) {
    c = await (await request.post(`${stack.api}/api/countdowns`, { headers, data: { topic: "Synthetic draft recovery", fmt: "top5" } })).json();
    await request.post(`${stack.api}/api/countdowns/${c.id}/research`, { headers });
    await expect.poll(async () => (await (await request.get(`${stack.api}/api/countdowns/${c.id}`, { headers })).json()).entry_count).toBe(5);
    await request.post(`${stack.api}/api/countdowns/${c.id}/confirm`, { headers });
  }
  const openScript = async () => {
    const summary = page.locator("summary").filter({ hasText: /^4\. Script$/ });
    const intro = page.getByLabel("Intro hook (voiced over a preview of the titles; tease #1 without naming it)");
    if (!await intro.isVisible()) await summary.click();
    return intro;
  };
  await page.goto(`/#countdowns/${c.id}`);
  let intro = await openScript();
  const savedIntro = await intro.inputValue();
  await intro.fill("An unsaved countdown hook survives an unrelated status refresh.");
  // Updating review metadata changes updated_at, but must not reset unsaved copy.
  await request.put(`${stack.api}/api/countdowns/${c.id}/review`, { headers, data: { on: true } });
  await page.waitForResponse(r => r.url() === `${stack.api}/api/countdowns/${c.id}` && r.request().method() === "GET");
  await expect(intro).toHaveValue("An unsaved countdown hook survives an unrelated status refresh.");
  await page.goto("/#settings");
  await page.goto(`/#countdowns/${c.id}`);
  intro = await openScript();
  await page.getByRole("button", { name: "Restore unsaved draft", exact: true }).click();
  await expect(intro).toHaveValue("An unsaved countdown hook survives an unrelated status refresh.");
  await page.getByRole("button", { name: "Discard unsaved changes", exact: true }).click();
  await expect(intro).toHaveValue(savedIntro);
  await page.screenshot({ path: path.join(evidence, "script-draft-recovery.png"), fullPage: true });

  await page.goto(`/#longform/${pid}`);
  await expect(editor).toHaveValue("Which reviewed draft should we keep?");
  await page.evaluate(() => {
    const original = Storage.prototype.setItem;
    (window as any).restoreDraftStorage = () => { Storage.prototype.setItem = original; };
    Storage.prototype.setItem = function(key, value) {
      if (key.startsWith("scrapper-draft:")) throw new DOMException("Synthetic storage quota failure", "QuotaExceededError");
      original.call(this, key, value);
    };
  });
  await editor.fill("This unsaved draft cannot be cached.");
  await expect(page.getByRole("alert").filter({ hasText: "Browser draft storage is unavailable" })).toBeVisible();
  await page.getByRole("button", { name: /Settings & Channels/ }).click();
  await expect(page.getByRole("dialog")).toContainText("will lose unsaved changes");
  await page.getByRole("dialog").getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(editor).toHaveValue("This unsaved draft cannot be cached.");
  await expect(page).toHaveURL(new RegExp(`#longform/${pid}$`));
  // External hash/history changes must receive the same protection as buttons.
  await page.evaluate(() => { window.location.hash = "settings"; });
  await expect(page.getByRole("dialog")).toContainText("will lose unsaved changes");
  await page.getByRole("dialog").getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`#longform/${pid}$`));
  await expect(editor).toHaveValue("This unsaved draft cannot be cached.");
  await page.getByRole("button", { name: "Discard unsaved changes", exact: true }).click();
  await expect(editor).toHaveValue("Which reviewed draft should we keep?");
  await page.getByRole("button", { name: /Settings & Channels/ }).click();
  await expect(page).toHaveURL(/#settings$/);
  await page.evaluate(() => { (window as any).restoreDraftStorage(); });
});

test("retained export UI recovers prior words and media after invalidation and project deletion", async ({ page, request }) => {
  const project = await (await request.get(`${stack.api}/api/studio/projects/1`, { headers })).json();
  expect(project.script.sentences.length).toBeGreaterThan(0);
  const versions = await (await request.get(`${stack.api}/api/export-history?kind=studio&project_id=1`, { headers })).json();
  const original = versions.versions.find((v: any) => v.assets.some((a: any) => a.role === "video" && a.available));
  expect(original).toBeTruthy();
  const detail = await (await request.get(`${stack.api}/api/export-history/${original.id}`, { headers })).json();
  const savedSentences = detail.manifest.snapshot.inputs.script.sentences;
  const originalWords = savedSentences.map((s: any, i: number) =>
    `${i && savedSentences[i - 1].paragraph !== s.paragraph ? "\n" : ""}${s.text}\n`).join("").trim();
  const blob = detail.manifest.assets.find((a: any) => a.role === "video");
  const originalBytes = readFileSync(blob.blob);
  const history = page.getByTestId("export-history");
  // This corpus compresses many owner sessions into minutes. Keep the real
  // server limit and exercise its explicit retry UI if the shared IP is busy.
  const rateAware = async (url: string, method: string, action: () => Promise<void>, retry: () => Promise<void> = action) => {
    const attempt = async (run: () => Promise<void>) => {
      const [response] = await Promise.all([page.waitForResponse(r => r.url() === url && r.request().method() === method), run()]);
      return response;
    };
    let response = await attempt(action);
    if (response.status() === 429) {
      await expect(page.getByText(/Rate limit exceeded/).first()).toBeVisible();
      const seconds = Number(response.headers()["retry-after"]);
      expect(seconds).toBeGreaterThan(0); expect(seconds).toBeLessThanOrEqual(60);
      await page.waitForTimeout(seconds * 1000 + 100);
      response = await attempt(retry);
    }
    for (let n = 0; n < 4 && response.status() === 409 && (await response.text()).includes("Media is in use"); n++) {
      await expect(page.getByText(/Media is in use/).first()).toBeVisible();
      await page.waitForTimeout(1000); // Explicit UI retry after the active stream releases ownership.
      response = await attempt(retry);
    }
    expect(response.ok()).toBe(true);
  };
  await page.goto("/#longform/1");
  await page.getByTestId("script-editor").fill(`A newer owner draft preserved before the explicit recovery, revision ${Date.now()}.`);
  await rateAware(`${stack.api}/api/studio/projects/1`, "PATCH",
    () => page.getByRole("button", { name: "Save script", exact: true }).click());
  await expect(page.getByTestId("research-blocked")).toBeVisible();
  await rateAware(`${stack.api}/api/export-history?kind=studio&project_id=1`, "GET",
    () => history.getByText("Retained export history", { exact: true }).click(),
    () => history.getByRole("button", { name: "Refresh history", exact: true }).click());
  await rateAware(`${stack.api}/api/export-history/${original.id}`, "GET",
    () => history.getByRole("button", { name: `Inspect retained export ${original.id}`, exact: true }).click());
  await expect(history.getByTestId("retained-version-detail")).toContainText("Original colored shapes");
  await rateAware(`${stack.api}/api/export-history/${original.id}/restore-draft`, "POST", async () => {
    await history.getByRole("button", { name: "Restore saved script as draft", exact: true }).click();
    await page.getByRole("dialog").getByRole("button", { name: "OK", exact: true }).click();
  });
  await expect(page.getByTestId("script-editor")).toHaveValue(originalWords);
  await expect(page.getByRole("button", { name: "Voice + shots", exact: true })).toBeDisabled();
  await page.screenshot({ path: path.join(evidence, "retained-script-recovery.png"), fullPage: true });
  const downloaded = await request.get(`${stack.api}/api/export-history/${original.id}/file/video`, { headers });
  expect(downloaded.status()).toBe(200);
  expect((await downloaded.body()).equals(originalBytes)).toBe(true);
  const partial = await request.get(`${stack.api}/api/export-history/${original.id}/file/video`, { headers: { ...headers, Range: "bytes=0-31" } });
  expect(partial.status()).toBe(206);
  expect((await partial.body()).equals(originalBytes.subarray(0, 32))).toBe(true);
  await page.getByRole("button", { name: "Delete", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "OK", exact: true }).click();
  await expect.poll(async () => (await request.get(`${stack.api}/api/studio/projects/1`, { headers })).status()).toBe(404);
  expect((await request.get(`${stack.api}/api/export-history/${original.id}/file/video`, { headers })).status()).toBe(200);
  await page.goto("/#settings");
  await expect(page.getByText("Invalid Date", { exact: true })).toHaveCount(0);
  await expect(page.getByText(/automatic remote deletion is disabled/)).toBeVisible();
  const archive = page.getByTestId("export-history");
  await rateAware(`${stack.api}/api/export-history`, "GET",
    () => archive.getByText("Retained export history", { exact: true }).click(),
    () => archive.getByRole("button", { name: "Refresh history", exact: true }).click());
  await rateAware(`${stack.api}/api/export-history/${original.id}`, "GET",
    () => archive.getByRole("button", { name: `Inspect retained export ${original.id}`, exact: true }).click());
  await expect(archive.getByTestId("retained-version-detail")).toContainText("Original colored shapes");
  await page.screenshot({ path: path.join(evidence, "deleted-project-export-history.png"), fullPage: true });
});


test("retained delivery UI preserves published bytes and remains available when schedule settings fail", async ({ page, request }) => {
  const before = await (await request.get(`${stack.api}/api/e2e/isolation`)).json();
  const queued = await (await request.get(`${stack.api}/api/queue`, { headers })).json();
  const original = queued.find((q: any) => q.status === "archived" && q.pipeline === "LongForm");
  expect(original).toBeTruthy();
  const originalBytes = readFileSync(original.video_path);
  await page.goto("/#queue");
  await page.getByRole("button", { name: /SocialPilot AI/ }).click();
  await page.getByRole("button", { name: "🎬 LongForm Breakdowns", exact: true }).click();
  await page.getByRole("button", { name: /Posted Archive/ }).click();
  const row = page.getByRole("row").filter({ has: page.getByText(`#${original.id}`, { exact: true }) });
  await row.getByTitle("Delete", { exact: true }).click();
  const [removed] = await Promise.all([
    page.waitForResponse(r => r.url() === `${stack.api}/api/queue/${original.id}` && r.request().method() === "DELETE"),
    page.getByRole("dialog").getByRole("button", { name: "OK", exact: true }).click(),
  ]);
  expect(removed.ok()).toBe(true);
  const history = await (await request.get(`${stack.api}/api/delivery-history`, { headers })).json();
  const retained = history.deliveries.find((d: any) => d.queue_id === original.id);
  expect(retained).toBeTruthy();
  const detail = await (await request.get(`${stack.api}/api/delivery-history/${retained.id}`, { headers })).json();
  const video = detail.manifest.assets.findIndex((a: any) => a.role === "video");
  const file = await request.get(`${stack.api}/api/delivery-history/${retained.id}/file/${video}`, { headers });
  expect(file.ok()).toBe(true); expect((await file.body()).equals(originalBytes)).toBe(true);
  expect(file.headers()["content-type"]).toContain("video/mp4");
  await page.route(`${stack.api}/api/schedule`, async route => {
    if (route.request().method() === "GET") await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "Synthetic schedule service unavailable" }) });
    else await route.continue();
  });
  await page.goto("/#settings");
  await expect(page.getByRole("alert").filter({ hasText: "Schedule unavailable" })).toBeVisible();
  const archive = page.getByTestId("delivery-history");
  await archive.getByText("Retained delivery history and Drive versions", { exact: true }).click();
  await archive.getByRole("button", { name: `Inspect retained delivery ${retained.id}`, exact: true }).click();
  await expect(archive.getByTestId("retained-delivery-detail")).toContainText(original.title);
  await expect(archive.getByTestId("retained-delivery-detail")).toContainText(original.description);
  await expect(archive.getByRole("link", { name: /Open retained video/ })).toBeVisible();
  await page.screenshot({ path: path.join(evidence, "retained-delivery-recovery.png"), fullPage: true });
  const after = await (await request.get(`${stack.api}/api/e2e/isolation`)).json();
  expect(after.fake_publish_receipts).toBe(before.fake_publish_receipts);
  expect(after.blocked_network_attempts).toBe(0);
});

test("Native formats UI creates actual single-image and private community/poll manual packages", async ({ page, request }) => {
  const before = await (await request.get(`${stack.api}/api/queue/counts`, { headers })).json();
  await page.goto("/#editorial");
  await page.getByLabel("Topic", { exact: true }).fill("Original Manual Format Fixtures");
  await page.getByLabel("Original angle", { exact: true }).fill("Compare original composition choices through a single image, community discussion and poll.");
  await page.getByLabel("Audience and question", { exact: true }).fill("Viewers discussing our original generated shapes and framing choices.");
  await page.getByLabel("Research source URL", { exact: true }).fill("https://example.invalid/original-manual-assets");
  await page.getByLabel("Supporting excerpt", { exact: true }).fill("All compositions and words in this isolated fixture were created for local tests; no borrowed media is used.");
  const suitable = page.getByRole("group", { name: "Suitable formats", exact: true });
  for (const fmt of ["short", "carousel"]) await suitable.getByRole("checkbox", { name: fmt, exact: true }).uncheck();
  for (const fmt of ["image", "community", "poll"]) await suitable.getByRole("checkbox", { name: fmt, exact: true }).check();
  await page.getByRole("button", { name: "Create opportunity", exact: true }).click();
  const opportunity = page.getByRole("article").filter({ has: page.getByRole("heading", { name: "Original Manual Format Fixtures", exact: true }) });
  await opportunity.getByRole("button", { name: "Approve research brief", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "OK", exact: true }).click();
  await expect(opportunity).toContainText("approved");
  const opportunities = await (await request.get(`${stack.api}/api/editorial/opportunities`, { headers })).json();
  const oid = opportunities.find((row: any) => row.title === "Original Manual Format Fixtures").id;
  await page.getByRole("button", { name: "Native campaigns", exact: true }).click();
  await page.getByLabel("Approved research", { exact: true }).selectOption(String(oid));
  const treatments = page.getByRole("group", { name: "Treatments to make", exact: true });
  for (const fmt of ["image", "community", "poll"]) await treatments.getByRole("checkbox", { name: fmt, exact: true }).check();
  for (const [index, text] of ["Can a shape change the meaning of a frame?", "Our original composition moves attention toward a quiet corner.", "Choose the frame that makes the intended idea clearest."].entries())
    await page.getByLabel(`Beat text ${index + 1}`, { exact: true }).fill(text);
  await page.getByLabel("Single-image text", { exact: true }).fill("One original shape can change a scene.");
  await page.getByLabel("Single-image caption", { exact: true }).fill("Choose the original frame with the clearest emphasis.");
  await page.getByLabel("Community post text", { exact: true }).fill("We created three original shape compositions. Which change made the intended idea clearest, and why?");
  await page.getByLabel("Poll question", { exact: true }).fill("Which original composition would you choose?");
  await page.getByLabel("Poll options (one per line, 2–4)", { exact: true }).fill("Bold center\nQuiet corner\nMoving edge");
  await page.getByRole("button", { name: "Create native campaign", exact: true }).click();
  const campaigns = async () => (await request.get(`${stack.api}/api/editorial/campaigns`, { headers })).json();
  await expect.poll(async () => (await campaigns()).some((row: any) => row.opportunity_id === oid)).toBe(true);
  const cid = (await campaigns()).find((row: any) => row.opportunity_id === oid).id;
  const campaign = page.getByTestId(`campaign-${cid}`);
  for (const fmt of ["image", "community", "poll"]) {
    await campaign.getByRole("button", { name: `Render ${fmt}`, exact: true }).click();
    const review = campaign.getByTestId(`native-export-${fmt}`);
    await expect(review).toContainText("Final decode: passed");
    await review.getByRole("button", { name: `Approve inspected ${fmt}`, exact: true }).click();
    await acceptExactExport(page, `native-${fmt}-acceptance.png`);
    if (fmt !== "image") {
      await expect(review.getByRole("group", { name: "Destinations for this export", exact: true })).toHaveCount(0);
      const button = review.getByRole("button", { name: "Download JSON manual package", exact: true });
      await expect(button).toBeEnabled();
      const [download] = await Promise.all([page.waitForEvent("download"), button.click()]);
      const file = path.join(evidence, `native-${fmt}-manual.json`); await download.saveAs(file);
      const payload = JSON.parse(readFileSync(file, "utf8"));
      expect(payload.delivery).toBe("manual_only"); expect(payload.publication_status).toBe("not_published");
      expect(payload.options).toEqual(fmt === "poll" ? ["Bold center", "Quiet corner", "Moving edge"] : []);
    }
    await review.screenshot({ path: path.join(evidence, `native-${fmt}-manual-review.png`) });
  }
  const after = await (await request.get(`${stack.api}/api/queue/counts`, { headers })).json();
  expect(after).toEqual(before);
  const completed = (await campaigns()).find((row: any) => row.id === cid);
  expect(completed.exports.every((item: any) => !item.queue_item_id)).toBe(true);
});

test("Asset library UI indexes original media, finds owner captions and reuses duplicates", async ({ page, request }) => {
  await page.goto("/#editorial");
  await page.getByRole("button", { name: "Asset library", exact: true }).click();
  await page.getByText("Index a local video or audio asset", { exact: true }).click();
  await page.getByLabel("Local media file", { exact: true }).setInputFiles(path.join(stack.root, "fixtures/trailer.mp4"));
  await page.getByLabel("Asset rights and intended use", { exact: true }).fill("Original procedural shape footage created by the isolated fixture; permitted for these local tests.");
  await page.getByLabel("Asset scene description", { exact: true }).fill("Original moving shapes and color panels composed for a synthetic local test.");
  await page.getByLabel("Asset tags", { exact: true }).fill("original shapes, procedural");
  await page.getByLabel("People/entities (owner labels)", { exact: true }).fill("No real people");
  await page.getByRole("button", { name: "Add caption interval", exact: true }).click();
  await page.getByLabel("Cue 1 start", { exact: true }).fill("0");
  await page.getByLabel("Cue 1 end", { exact: true }).fill("1");
  await page.getByLabel("Cue 1 text", { exact: true }).fill("A turquoise fixture moves across the original frame.");
  await page.getByRole("button", { name: "Index local media", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Media indexed locally" })).toBeVisible();
  await page.getByLabel("Search asset context", { exact: true }).fill("turquoise");
  await page.getByLabel("Filter asset kind", { exact: true }).selectOption("video");
  await page.getByRole("button", { name: "Search indexed assets", exact: true }).click();
  const result = await (await request.get(`${stack.api}/api/editorial/assets/search?q=turquoise&kind=video`, { headers })).json();
  expect(result.method).toBe("lexical_tags_description_owner_captions");
  const indexed = page.getByTestId(`indexed-asset-${result.assets[0].id}`);
  await expect(indexed).toContainText("A turquoise fixture moves across the original frame.");
  await expect(indexed.locator("img")).toHaveCount(3);
  await expect.poll(async () => indexed.locator("video").evaluate((element: HTMLVideoElement) => element.readyState)).toBeGreaterThan(0);
  await indexed.getByRole("button", { name: "Preview 0.00–1.00s", exact: true }).click();
  await page.getByRole("button", { name: "Index local media", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Duplicate media reused" })).toBeVisible();
  const after = await (await request.get(`${stack.api}/api/editorial/assets/search?q=turquoise&kind=video`, { headers })).json();
  expect(after.assets.filter((row: any) => row.id === result.assets[0].id)).toHaveLength(1);
  await page.screenshot({ path: path.join(evidence, "authorized-asset-library.png"), fullPage: true });
});

test("Audience lineage UI maps exact native bytes, syncs processed-day fixtures and retains corrections", async ({ page, request }) => {
  const opportunity = await request.post(`${stack.api}/api/editorial/opportunities`, { headers, data: {
    title: "Original audience lineage fixture", angle: "Discuss original shape composition through an independently retained local image.",
    audience: "Viewers comparing original generated shapes", formats: ["image"], evidence: [{ url: "https://example.invalid/lineage-fixture",
      excerpt: "Original procedural shapes and text were created for this local test and contain no borrowed material.", entity: "Original shapes", retrieved_at: new Date().toISOString() }],
  } });
  expect(opportunity.ok()).toBe(true); const oid = (await opportunity.json()).id;
  expect((await request.post(`${stack.api}/api/editorial/opportunities/${oid}/approve`, { headers })).ok()).toBe(true);
  const created = await request.post(`${stack.api}/api/editorial/campaigns`, { headers, data: {
    opportunity_id: oid, title: "Original audience lineage fixture", formats: ["image"],
    beats: [{ role: "hook", text: "Can one original shape shift your attention?", source_refs: [1] },
      { role: "body", text: "The original composition moves attention toward a quiet corner.", source_refs: [1] },
      { role: "payoff", text: "Choose the shape composition with the clearest message.", source_refs: [1] }],
    image_text: "One original shape changes the frame.", image_caption: "Which original composition makes the clearest choice?",
  } });
  expect(created.ok()).toBe(true); const cid = (await created.json()).id;
  const rendered = await request.post(`${stack.api}/api/editorial/campaigns/${cid}/render/image`, { headers });
  expect(rendered.ok()).toBe(true); const eid = (await rendered.json()).id;
  await request.post(`${stack.api}/api/e2e/enable-audience-fixture`);
  await page.goto("/#editorial"); await page.getByRole("button", { name: "Performance evidence", exact: true }).click();
  await expect(page.getByText(/YouTube adapter: fixture_only/)).toBeVisible();
  const form = page.getByRole("form", { name: "Content mapping", exact: true });
  await form.getByLabel("Account/channel ID", { exact: true }).fill("UC" + "f".repeat(22));
  await form.getByLabel("Published content ID", { exact: true }).fill("fixture0001");
  await form.getByLabel("Mapped format", { exact: true }).selectOption("image");
  await form.getByLabel("Mapped publication time", { exact: true }).fill(new Date(Date.now() - 4 * 86400000).toISOString().slice(0, 16));
  await form.getByLabel("Exact retained source", { exact: true }).selectOption(`native_export:${eid}`);
  await form.getByLabel("Mapping evidence and correction rationale", { exact: true }).fill("Owner compared this original synthetic image and its exact local source against the fixture public identifier.");
  const ack = form.getByRole("checkbox", { name: "I reviewed this exact public identifier, publication time and retained source.", exact: true });
  const save = form.getByRole("button", { name: "Record exact content mapping", exact: true });
  await ack.check();
  await form.getByLabel("Mapping evidence and correction rationale", { exact: true }).fill("Owner reviewed the exact generated image, its metadata and the synthetic public identifier before mapping.");
  await expect(ack).not.toBeChecked(); await expect(save).toBeDisabled(); await ack.check(); await save.click();
  await expect(page.getByRole("status").filter({ hasText: "Exact content mapping recorded" })).toBeVisible();
  const mappings = async () => (await request.get(`${stack.api}/api/editorial/content-lineage`, { headers })).json();
  const first = (await mappings()).bindings.find((row: any) => row.content_id === "fixture0001");
  const card = page.getByTestId(`content-mapping-${first.id}`);
  await card.getByRole("button", { name: "Sync read-only YouTube metrics for fixture0001", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Read-only observation recorded through" })).toBeVisible();
  const queries = await (await request.get(`${stack.api}/api/e2e/audience-queries`)).json();
  expect(queries.real_account_access).toBe(false); expect(queries.queries).toHaveLength(2);
  expect(queries.queries[0].dimensions).toBe("day"); expect(queries.queries[1].dimensions).toBeUndefined();
  await card.getByRole("button", { name: "Correct mapping for fixture0001", exact: true }).click();
  await form.getByLabel("Mapped format", { exact: true }).selectOption("community");
  await form.getByLabel("Mapping evidence and correction rationale", { exact: true }).fill("Owner corrected the format after reviewing the original local image used for this synthetic community treatment.");
  await ack.check(); await save.click();
  await expect.poll(async () => (await mappings()).bindings.find((row: any) => row.content_id === "fixture0001").revision).toBe(2);
  expect((await mappings()).versions.filter((row: any) => row.content_id === "fixture0001")).toHaveLength(2);
  await page.screenshot({ path: path.join(evidence, "audience-content-lineage.png"), fullPage: true });
});

test("Comment inbox UI resumes read-only pages, preserves drafts and records exact-source correction proposals", async ({ page, request }) => {
  const opportunity = await request.post(`${stack.api}/api/editorial/opportunities`, { headers, data: {
    title: "Original comment context fixture", angle: "Examine original shape composition with retained local source context.",
    audience: "Viewers comparing original generated shapes", formats: ["image"], evidence: [{ url: "https://example.invalid/comment-fixture",
      excerpt: "Original procedural shapes were generated in the isolated local test; no borrowed media is included.", entity: "Original shapes", retrieved_at: new Date().toISOString() }],
  } });
  expect(opportunity.ok()).toBe(true); const oid = (await opportunity.json()).id;
  expect((await request.post(`${stack.api}/api/editorial/opportunities/${oid}/approve`, { headers })).ok()).toBe(true);
  const created = await request.post(`${stack.api}/api/editorial/campaigns`, { headers, data: {
    opportunity_id: oid, title: "Original comment context fixture", formats: ["image"],
    beats: [{ role: "hook", text: "Can one original shape shift your attention?", source_refs: [1] },
      { role: "body", text: "The original composition moves attention toward a quiet corner.", source_refs: [1] },
      { role: "payoff", text: "Choose the shape composition with the clearest message.", source_refs: [1] }],
    image_text: "One original shape changes the frame.", image_caption: "Which original composition makes the clearest choice?",
  } });
  expect(created.ok()).toBe(true); const cid = (await created.json()).id;
  const rendered = await request.post(`${stack.api}/api/editorial/campaigns/${cid}/render/image`, { headers });
  expect(rendered.ok()).toBe(true); const eid = (await rendered.json()).id;
  const source = (await (await request.get(`${stack.api}/api/editorial/content-sources`, { headers })).json()).sources.find((row: any) => row.id === eid);
  const mapped = await request.post(`${stack.api}/api/editorial/content-lineage`, { headers, data: {
    platform: "youtube", account_id: "UC" + "f".repeat(22), content_id: "fixture0002", format: "image",
    published_at: new Date(Date.now() - 4 * 86400000).toISOString(), source_kind: "native_export", source_id: eid,
    expected_source_fingerprint: source.fingerprint, mapping_evidence: "Owner reviewed the exact synthetic image and its local original source before mapping.", reviewed: true,
  } });
  expect(mapped.ok()).toBe(true); const lid = (await mapped.json()).id;
  await request.post(`${stack.api}/api/e2e/enable-comment-fixture`);
  const feedback = async () => (await (await request.get(`${stack.api}/api/editorial/feedback?lineage_id=${lid}`, { headers })).json()).feedback;
  const open = async () => {
    await page.goto("/#editorial"); await page.getByRole("button", { name: "Audience & experiments", exact: true }).click();
    await page.getByLabel("Comment content mapping", { exact: true }).selectOption(lid);
  };
  await open();
  const read = page.getByRole("button", { name: "Read next comment page", exact: true });
  await read.click(); await expect.poll(async () => (await feedback()).length).toBe(1);
  await expect(page.getByText(/1 pages · 1 provider rows · 1 new versions · 0 duplicates/)).toBeVisible();
  await page.reload(); await page.getByRole("button", { name: "Audience & experiments", exact: true }).click();
  await page.getByLabel("Comment content mapping", { exact: true }).selectOption(lid);
  await read.click(); await expect.poll(async () => (await feedback()).length).toBe(2);
  await expect(page.getByText(/2 pages · 3 provider rows · 2 new versions · 1 duplicates/)).toBeVisible();
  const parent = (await feedback()).find((row: any) => row.text.includes("original shape?"));
  expect(JSON.stringify(parent)).not.toContain("owner@example.org"); expect(JSON.stringify(parent)).not.toContain("@hiddenviewer"); expect(JSON.stringify(parent)).not.toContain("ExcludedAuthor");
  await page.getByLabel("Comment page scope", { exact: true }).selectOption(parent.latest_evidence_id);
  await read.click(); await expect.poll(async () => (await feedback()).length).toBe(3);
  const card = page.getByTestId(`feedback-${parent.id}`);
  await card.getByRole("button", { name: "Inspect thread & correction targets", exact: true }).click();
  const editor = card.getByRole("textbox", { name: `Private reply draft #${parent.id}`, exact: true });
  const words = "I will compare the quieter corner against the exact retained original composition before answering.";
  await editor.fill(words);
  await expect(card.getByRole("status").filter({ hasText: "Unsaved draft stored" })).toBeVisible();
  await page.getByRole("button", { name: "Opportunities & calendar", exact: true }).click();
  await page.getByRole("button", { name: "Audience & experiments", exact: true }).click();
  await page.getByLabel("Comment content mapping", { exact: true }).selectOption(lid);
  await card.getByRole("button", { name: "Restore unsaved draft", exact: true }).click(); await expect(editor).toHaveValue(words);
  await card.getByRole("button", { name: "Inspect thread & correction targets", exact: true }).click();
  const ack = card.getByRole("checkbox", { name: "I reviewed this exact private draft and current source/thread context.", exact: true });
  const reviewed = card.getByRole("button", { name: "Mark reviewed for manual use", exact: true });
  await ack.check(); await editor.fill(words + " Thank you."); await expect(ack).not.toBeChecked(); await expect(reviewed).toBeDisabled();
  await ack.check(); await reviewed.click(); await expect(card.getByRole("status").filter({ hasText: "Reviewed for manual use. Nothing sent." })).toBeVisible();
  await request.post(`${stack.api}/api/e2e/edit-comment-fixture`);
  await page.getByRole("button", { name: "Restart reviewed comment scan", exact: true }).click();
  await read.click(); await expect(card.getByRole("blockquote").first()).toContainText("quieter corner instead");
  await expect(editor).toHaveValue(words + " Thank you."); await expect(card.getByText("Owner review required.", { exact: true })).toBeVisible();
  await card.getByRole("button", { name: "Inspect thread & correction targets", exact: true }).click();
  await card.getByText("Propose a source correction", { exact: true }).click();
  const targets = card.getByLabel("Exact retained correction target", { exact: true });
  const value = await targets.locator("option").nth(1).getAttribute("value"); await targets.selectOption(value!);
  await card.getByLabel("Proposed clarification", { exact: true }).fill("Compare the quieter corner against the retained original composition before revising any claim.");
  await card.getByLabel("Correction reference URL", { exact: true }).fill("https://example.invalid/owner-reference");
  await card.getByLabel("Correction reference excerpt", { exact: true }).fill("The original shape composition has a quieter corner and was generated for the local fixture.");
  await card.getByLabel("Correction proposal rationale", { exact: true }).fill("This proposes an independent review of the exact retained native beat and does not certify it as a correction.");
  await card.getByRole("checkbox", { name: "I reviewed this exact retained target and the unverified proposal evidence.", exact: true }).check();
  await card.getByRole("button", { name: "Record private correction proposal", exact: true }).click();
  await expect(card.getByRole("status").filter({ hasText: "Private correction proposal saved for independent source review" })).toBeVisible();
  const queries = await (await request.get(`${stack.api}/api/e2e/comment-queries`)).json();
  expect(queries.real_account_access).toBe(false); expect(queries.queries.map((q: any) => q.endpoint)).toEqual(["commentThreads", "commentThreads", "comments", "commentThreads"]);
  expect(queries.queries[1].params.pageToken).toBe("fixture-next-comment-page");
  await page.evaluate(() => {
    const original = Storage.prototype.setItem;
    (window as any).restoreFeedbackStorage = () => { Storage.prototype.setItem = original; };
    Storage.prototype.setItem = function(key, value) {
      if (key.startsWith("screen-central:feedback-reply:")) throw new DOMException("Synthetic draft storage failure", "QuotaExceededError");
      original.call(this, key, value);
    };
  });
  await editor.fill("This private draft remains on screen while browser storage is unavailable.");
  await expect(card.getByRole("alert").filter({ hasText: "Browser draft storage is unavailable" })).toBeVisible();
  await page.getByRole("button", { name: "Opportunities & calendar", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("will lose unsaved changes");
  await page.getByRole("dialog").getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(editor).toHaveValue("This private draft remains on screen while browser storage is unavailable.");
  await card.getByRole("button", { name: "Discard unsaved changes", exact: true }).click();
  await page.evaluate(() => { (window as any).restoreFeedbackStorage(); });
  await page.screenshot({ path: path.join(evidence, "comment-inbox-private-review.png"), fullPage: true });
});

test("Learning diagnostics UI keeps peer evidence, cost limits and reviewed experiment planning private", async ({ page, request }) => {
  const before = await (await request.get(`${stack.api}/api/e2e/isolation`)).json();
  const queues = await (await request.get(`${stack.api}/api/queue/counts`, { headers })).json();
  const published = new Date(Date.now() - 25 * 3600000).toISOString();
  const observed = new Date(Date.now() - 3600000).toISOString();
  const subject = "diagnostic-ui-subject";
  const ids = [subject, ...Array.from({ length: 5 }, (_, index) => `diagnostic-ui-peer-${index}`)];
  const add = async (id: string, low = false) => {
    const result = await request.post(`${stack.api}/api/editorial/observations`, { headers, data: {
      platform: "youtube", content_id: id, format: "short", published_at: published, observed_at: observed,
      metrics: { views: low ? 100 : 1000, impressions: low ? 100 : 1000, clicks: low ? 1 : 100, average_view_percent: low ? 30 : 80 },
      source: "Owner-authorized original synthetic performance export; no real account accessed",
    } }); expect(result.ok()).toBe(true);
  };
  for (const id of ids) await add(id, id === subject);
  await page.goto("/#editorial"); await page.getByRole("button", { name: "Performance evidence", exact: true }).click();
  const find = async () => {
    await page.getByLabel("Find diagnostic content ID", { exact: true }).fill(subject);
    await page.getByRole("button", { name: "Find performance diagnostic", exact: true }).click();
  };
  await find();
  const card = page.getByTestId(`performance-diagnostic-${subject}`);
  await expect(card).toBeVisible(); await expect(card).toContainText("Recorded cost: Unavailable");
  const ctr = card.getByRole("row").filter({ has: page.getByRole("cell", { name: "click through percent", exact: true }) });
  await expect(ctr).toContainText("observed below peer median");
  const rationale = card.getByLabel("Decision rationale", { exact: true });
  const ack = card.getByRole("checkbox", { name: "I reviewed these exact measures, cost limits and confounders.", exact: true });
  const save = card.getByRole("button", { name: "Record private learning decision", exact: true });
  await rationale.fill("Review one truthful packaging concept while keeping topic and timing comparable; this cohort is observational.");
  await ack.check(); await rationale.fill("Review topic, duration, packaging and latency confounders before a matched, privately planned experiment.");
  await expect(ack).not.toBeChecked(); await expect(save).toBeDisabled(); await ack.check(); await save.click();
  await expect(card.getByRole("status")).toHaveText("Private planning decision recorded. No action executed.");
  await card.getByRole("button", { name: "Draft experiment from reviewed evidence", exact: true }).click();
  await expect(page.getByLabel("Experiment name", { exact: true })).toHaveValue(`Review ${subject} creative treatment`);
  await expect(page.getByLabel("Experiment platform", { exact: true })).toBeDisabled();
  await page.getByLabel("Control content IDs (comma separated)", { exact: true }).fill(ids.slice(0, 3).join(","));
  await page.reload(); await page.getByRole("button", { name: "Audience & experiments", exact: true }).click();
  await page.getByRole("form", { name: "Creative experiment", exact: true }).getByRole("button", { name: "Restore unsaved draft", exact: true }).click();
  await expect(page.getByLabel("Experiment platform", { exact: true })).toBeDisabled();
  await expect(page.getByLabel("Experiment name", { exact: true })).toHaveValue(`Review ${subject} creative treatment`);
  await page.getByLabel("Control content IDs (comma separated)", { exact: true }).fill(ids.slice(0, 3).join(","));
  await page.getByLabel("Variant content IDs (comma separated)", { exact: true }).fill(ids.slice(3).join(","));
  await page.getByLabel("Controls and known confounders", { exact: true }).fill("Compare original subjects at the same age; topic, duration, timing and reporting latency may differ.");
  await page.getByRole("button", { name: "Register experiment", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Experiment registered" })).toBeVisible();
  const experiment = page.getByRole("article").filter({ has: page.getByRole("heading", { name: `Review ${subject} creative treatment`, exact: true }) });
  await expect(experiment).toContainText("insufficient comparable data"); await expect(experiment).toContainText("No causal conclusion or automatic winner");
  const learning = await (await request.get(`${stack.api}/api/editorial/learning`, { headers })).json();
  const saved = learning.experiments.find((row: any) => row.name === `Review ${subject} creative treatment`);
  expect(saved.learning_context.decision_id).toMatch(/^[a-f0-9]{64}$/);
  expect(saved.metric).toBe("click_through_percent"); expect(saved.result.winner).toBeNull();
  await page.getByRole("button", { name: "Performance evidence", exact: true }).click(); await find();
  await card.getByLabel("Learning decision", { exact: true }).selectOption("defer");
  const unsaved = "Keep these private review words if another comparable observation changes the peer evidence during review.";
  await rationale.fill(unsaved); await ack.check();
  await add("diagnostic-ui-new-peer");
  await save.click(); await expect(card.getByRole("status").filter({ hasText: "Measures, peers, source mapping or cost context changed" })).toBeVisible();
  await expect(rationale).toHaveValue(unsaved); await expect(ack).not.toBeChecked();
  await find(); await expect(card).toContainText("Evidence changed since this decision.");
  await card.getByRole("button", { name: "Inspect private learning decisions", exact: true }).click();
  await expect(card.getByText("Most recent 1 decisions", { exact: true })).toBeVisible();
  const after = await (await request.get(`${stack.api}/api/e2e/isolation`)).json();
  expect(after.fake_publish_receipts).toBe(before.fake_publish_receipts); expect(after.blocked_network_attempts).toBe(0);
  expect(await (await request.get(`${stack.api}/api/queue/counts`, { headers })).json()).toEqual(queues);
  await page.screenshot({ path: path.join(evidence, "learning-diagnostics-private-decision.png"), fullPage: true });
});

test("Owner form recovery preserves imports and experiments across navigation, errors and failed browser storage", async ({ page, request }) => {
  const starting = await (await request.get(`${stack.api}/api/e2e/isolation`)).json();
  const calls: string[] = [];
  page.on("request", req => { if (req.method() === "POST" && /\/api\/editorial\/(feedback\/import|experiments)$/.test(req.url())) calls.push(req.url()); });
  const open = async () => { await page.goto("/#editorial"); await page.getByRole("button", { name: "Audience & experiments", exact: true }).click(); };
  await open();
  const imported = page.getByRole("form", { name: "Authorized comment import", exact: true });
  const experiment = page.getByRole("form", { name: "Creative experiment", exact: true });
  const words = "How can an original composition make the visual promise clearer?";
  await imported.getByLabel("Source content ID", { exact: true }).fill("private-import-recovery");
  await imported.getByLabel("Comment export source", { exact: true }).fill("Owner-authorized original synthetic feedback export");
  await imported.getByLabel("Comment texts", { exact: true }).fill(words);
  await experiment.getByLabel("Experiment name", { exact: true }).fill("Private form recovery experiment");
  await experiment.getByLabel("Hypothesis", { exact: true }).fill("A clearer original visual promise may change engaged viewing at comparable age.");
  await experiment.getByLabel("Control content IDs (comma separated)", { exact: true }).fill("control-alpha,control-beta");
  await experiment.getByLabel("Variant content IDs (comma separated)", { exact: true }).fill("variant-alpha,variant-beta");
  await experiment.getByLabel("Controls and known confounders", { exact: true }).fill("Keep topic, duration and publication timing comparable; provider latency can differ.");
  await page.getByRole("button", { name: "Performance evidence", exact: true }).click();
  await page.getByRole("button", { name: "Audience & experiments", exact: true }).click();
  for (const form of [imported, experiment]) await form.getByRole("button", { name: "Restore unsaved draft", exact: true }).click();
  await expect(imported.getByLabel("Comment texts", { exact: true })).toHaveValue(words);
  await expect(experiment.getByLabel("Experiment name", { exact: true })).toHaveValue("Private form recovery experiment");
  expect(calls).toHaveLength(0); // Recovery controls must never submit their enclosing form.
  await page.reload(); await page.getByRole("button", { name: "Audience & experiments", exact: true }).click();
  for (const form of [imported, experiment]) await form.getByRole("button", { name: "Restore unsaved draft", exact: true }).click();
  expect(calls).toHaveLength(0);
  await page.route(`${stack.api}/api/editorial/feedback/import`, route => route.fulfill({ status: 422, contentType: "application/json", body: JSON.stringify({
    detail: [{ loc: ["body", "comments", 0], msg: "Review the authorized source before importing", input: "private-validation-marker@example.invalid" }],
  }) }));
  await imported.getByRole("button", { name: "Import private feedback", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "comments.0: Review the authorized source before importing" })).toBeVisible();
  await expect(page.getByText(/private-validation-marker/)).toHaveCount(0);
  await expect(imported.getByLabel("Comment texts", { exact: true })).toHaveValue(words);
  await page.unroute(`${stack.api}/api/editorial/feedback/import`);
  let failRefresh = true;
  await page.route(`${stack.api}/api/editorial/learning`, route => failRefresh ? route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "Synthetic read-only refresh unavailable" }) }) : route.continue());
  await imported.getByRole("button", { name: "Import private feedback", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Deidentified comments imported" })).toContainText("Refresh unavailable");
  await expect(imported.getByLabel("Comment texts", { exact: true })).toHaveValue("");
  failRefresh = false; await page.getByRole("button", { name: "Refresh audience evidence", exact: true }).click();
  await expect(page.getByRole("article").filter({ hasText: words })).toBeVisible();
  await expect(experiment.getByLabel("Experiment name", { exact: true })).toHaveValue("Private form recovery experiment");
  await page.route(`${stack.api}/api/editorial/experiments`, route => route.abort("failed"));
  await experiment.getByRole("button", { name: "Register experiment", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "The server response could not be confirmed" })).toBeVisible();
  await expect(experiment.getByLabel("Experiment name", { exact: true })).toHaveValue("Private form recovery experiment");
  await page.unroute(`${stack.api}/api/editorial/experiments`);
  await experiment.getByRole("button", { name: "Register experiment", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Experiment registered" })).toBeVisible();
  await expect(experiment.getByLabel("Experiment name", { exact: true })).toHaveValue("");
  await page.evaluate(() => {
    const original = Storage.prototype.setItem;
    (window as any).restoreOwnerStorage = () => { Storage.prototype.setItem = original; };
    Storage.prototype.setItem = function(key, value) {
      if (key === "screen-central:comment-import") throw new DOMException("Synthetic browser storage failure", "QuotaExceededError");
      original.call(this, key, value);
    };
  });
  await imported.getByLabel("Comment texts", { exact: true }).fill("A private unsaved import remains on screen until deliberately discarded.");
  await expect(imported.getByRole("alert")).toContainText("Browser draft storage is unavailable");
  await page.getByRole("button", { name: "Performance evidence", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("will lose unsaved changes");
  await page.getByRole("dialog").getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(imported.getByLabel("Comment texts", { exact: true })).toHaveValue("A private unsaved import remains on screen until deliberately discarded.");
  const beforeDiscard = calls.length;
  await imported.getByRole("button", { name: "Discard unsaved changes", exact: true }).click();
  expect(calls).toHaveLength(beforeDiscard);
  await page.evaluate(() => { (window as any).restoreOwnerStorage(); });
  await page.screenshot({ path: path.join(evidence, "owner-form-recovery.png"), fullPage: true });
  const after = await (await request.get(`${stack.api}/api/e2e/isolation`)).json();
  expect(after.fake_publish_receipts).toBe(starting.fake_publish_receipts); expect(after.blocked_network_attempts).toBe(0);
});

test("A refused access token is cached only for its original API origin", async ({ page, request }) => {
  const fakeBase = "http://127.0.0.1:43210";
  await page.route(`${fakeBase}/**`, route => route.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ detail: "Synthetic owner credential was refused" }) }));
  await page.goto("/#settings");
  await page.evaluate(base => localStorage.setItem("scrapper_api_base", base), fakeBase);
  const refused = page.waitForResponse(response => response.url().startsWith(fakeBase) && response.status() === 401);
  await page.getByRole("button", { name: /Editorial desk/ }).click(); await refused;
  await expect(page.getByRole("status").filter({ hasText: /credential was refused|access token was refused/ }).first()).toBeVisible();
  await page.evaluate(base => localStorage.setItem("scrapper_api_base", base), stack.api);
  await page.getByRole("button", { name: /Settings & Channels/ }).click();
  const recovered = page.waitForResponse(response => response.url() === `${stack.api}/api/editorial/insights` && response.status() === 200);
  await page.getByRole("button", { name: /Editorial desk/ }).click(); await recovered;
  await expect(page.getByRole("heading", { name: "Editorial desk", exact: true })).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: /credential was refused|access token was refused/ })).toHaveCount(0);
  expect((await request.get(`${stack.api}/api/e2e/isolation`)).ok()).toBe(true);
});

test("Queue read recovery uses the real limiter with a seeded exhausted fixture budget", async ({ page, request }) => {
  const before = await (await request.get(`${stack.api}/api/e2e/isolation`)).json();
  const fixture = await (await request.post(`${stack.api}/api/e2e/queue-recovery-row`, { headers })).json();
  expect(fixture.status).toBe("review");
  const mutations: string[] = [];
  page.on("request", req => { if (req.method() !== "GET" && /\/api\/(queue|autopost)(?:[/?]|$)/.test(req.url())) mutations.push(req.url()); });
  await page.goto("/#socialpilot/queue");
  await expect(page.getByText(fixture.title, { exact: true })).toBeVisible();
  const edit = page.getByRole("dialog", { name: "Edit queue item", exact: true });
  await page.getByRole("row").filter({ has: page.getByText(fixture.title, { exact: true }) }).getByTitle("Edit video metadata", { exact: true }).click();
  const unsaved = "An owner queue title survives an unavailable background refresh.";
  await edit.getByLabel("Queue item title", { exact: true }).fill(unsaved);
  const seeded = await (await request.post(`${stack.api}/api/e2e/exhaust-request-budget`, { headers })).json();
  expect(seeded.mode).toBe("seeded_exhaustion_fixture"); expect(seeded.per_minute_limit).toBe(600); expect(seeded.policy_changed).toBe(false);
  const [denied] = await Promise.all([
    page.waitForResponse(response => new URL(response.url()).pathname === "/api/queue" && response.status() === 429),
    page.evaluate(() => document.dispatchEvent(new Event("visibilitychange"))),
  ]);
  const retry = Number(denied.headers()["retry-after"]); expect(retry).toBeGreaterThan(0); expect(retry).toBeLessThanOrEqual(60);
  const unavailable = page.getByTestId("queue-unavailable");
  await expect(unavailable.getByRole("alert")).toContainText("Rate limit exceeded");
  await expect(unavailable).toContainText("Last loaded snapshot retained"); await expect(unavailable.getByText(fixture.title, { exact: false })).toBeVisible();
  await expect(page.getByText("No queue items found for this selection.", { exact: true })).toHaveCount(0);
  expect(mutations).toHaveLength(0);
  await page.waitForTimeout(retry * 1000 + 150); // Real server window; no clock jump or automatic mutation retry.
  let reviewedRecoveredDraft = false;
  if (await edit.isVisible()) {
    await expect(edit.getByLabel("Queue item title", { exact: true })).toHaveValue(unsaved);
    reviewedRecoveredDraft = true; await edit.getByRole("button", { name: "Cancel", exact: true }).click();
  }
  await Promise.all([
    page.waitForResponse(response => new URL(response.url()).pathname === "/api/queue" && response.status() === 200),
    page.getByRole("button", { name: "Refresh queue", exact: true }).click(),
  ]);
  await expect(unavailable).toHaveCount(0);
  if (!reviewedRecoveredDraft) {
    await expect(edit.getByLabel("Queue item title", { exact: true })).toHaveValue(unsaved);
    await edit.getByRole("button", { name: "Cancel", exact: true }).click();
  }
  await expect(page.getByText(fixture.title, { exact: true })).toBeVisible();
  // A first-load outage must also remain unavailable, rather than look like an empty queue.
  const total = (await (await request.get(`${stack.api}/api/queue/counts`, { headers })).json()).all;
  await expect(page.getByRole("button", { name: /SocialPilot AI/ })).toContainText(`${total} items`);
  await page.goto("/#settings"); let fail = true;
  await page.route(`${stack.api}/api/queue**`, route => fail && ["/api/queue", "/api/queue/counts"].includes(new URL(route.request().url()).pathname)
    ? route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "Synthetic queue read outage" }) }) : route.continue());
  await page.getByRole("button", { name: /SocialPilot AI/ }).click();
  await page.getByRole("button", { name: /Posting Queue/ }).first().click();
  await expect(unavailable).toContainText("Current queue contents could not be confirmed");
  await expect(unavailable.getByRole("alert")).toHaveText("503: Synthetic queue read outage");
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
  await expect(page.getByRole("button", { name: /SocialPilot AI/ })).toContainText(`${total} items`);
  fail = false; await page.getByRole("button", { name: "Refresh queue", exact: true }).click();
  await expect(unavailable).toHaveCount(0); await expect(page.getByText(fixture.title, { exact: true })).toBeVisible();
  expect(mutations).toHaveLength(0);
  const after = await (await request.get(`${stack.api}/api/e2e/isolation`)).json();
  expect(after.fake_publish_receipts).toBe(before.fake_publish_receipts); expect(after.blocked_network_attempts).toBe(0);
  const stored = (await (await request.get(`${stack.api}/api/queue`, { headers })).json()).find((row: any) => row.id === fixture.id);
  expect(stored.status).toBe("review"); expect(stored.title).toBe(fixture.title);
  await page.screenshot({ path: path.join(evidence, "queue-read-recovery.png"), fullPage: true });
});

test("Native selected-video UI imports authorized media, chooses an interval and reviews its intentional loop", async ({ page, request }) => {
  const isolationBefore = await (await request.get(`${stack.api}/api/e2e/isolation`)).json();
  await page.goto("/#editorial");
  await page.getByRole("button", { name: "Asset library", exact: true }).click();
  await page.getByText("Index a local video or audio asset", { exact: true }).click();
  await page.getByLabel("Local media file", { exact: true }).setInputFiles(path.join(stack.root, "fixtures/trailer.mp4"));
  await page.getByLabel("Asset rights and intended use", { exact: true }).fill("Original procedural shape media from this isolated harness, authorized for this private local clip treatment.");
  await page.getByLabel("Asset scene description", { exact: true }).fill("Original red and blue color panels with geometric shapes; an owner-selected interval, not semantic search.");
  await page.getByLabel("Asset tags", { exact: true }).fill("original clip, source interval");
  const [indexed] = await Promise.all([
    page.waitForResponse(r => r.url().endsWith("/api/editorial/assets/media") && r.request().method() === "POST"),
    page.getByRole("button", { name: "Index local media", exact: true }).click(),
  ]);
  expect(indexed.ok()).toBe(true); const aid = (await indexed.json()).id;
  await page.getByLabel("Asset kind", { exact: true }).selectOption("audio");
  await page.getByLabel("Local media file", { exact: true }).setInputFiles(originalAudioFixture());
  await page.getByLabel("Asset rights and intended use", { exact: true }).fill("Original generated1100Hz tone created locally for this private background-audio fixture.");
  await page.getByLabel("Asset scene description", { exact: true }).fill("Original synthetic tone; objective mix and source-provenance fixture, not a musical quality sample.");
  const [audioIndexed] = await Promise.all([
    page.waitForResponse(r => r.url().endsWith("/api/editorial/assets/media") && r.request().method() === "POST"),
    page.getByRole("button", { name: "Index local media", exact: true }).click(),
  ]);
  expect(audioIndexed.ok()).toBe(true); const audioId = (await audioIndexed.json()).id;
  await page.getByRole("button", { name: "Opportunities & calendar", exact: true }).click();
  await page.getByLabel("Topic", { exact: true }).fill("Original selected-video treatment fixture");
  await page.getByLabel("Original angle", { exact: true }).fill("Use an explicit original source interval while separately narrating a new visual composition lesson.");
  await page.getByLabel("Audience and question", { exact: true }).fill("Viewers comparing deliberate original color and shape treatments.");
  await page.getByLabel("Research source URL", { exact: true }).fill("https://example.invalid/original-selected-interval");
  await page.getByLabel("Supporting excerpt", { exact: true }).fill("These original shapes and color panels were generated locally for this fixture; no borrowed footage or factual film claims are used.");
  await page.getByRole("button", { name: "Create opportunity", exact: true }).click();
  const opportunity = page.getByRole("article").filter({ has: page.getByRole("heading", { name: "Original selected-video treatment fixture", exact: true }) });
  await opportunity.getByRole("button", { name: "Approve research brief", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "OK", exact: true }).click();
  await expect(opportunity).toContainText("approved");
  const opportunities = await (await request.get(`${stack.api}/api/editorial/opportunities`, { headers })).json();
  const oid = opportunities.find((row: any) => row.title === "Original selected-video treatment fixture").id;
  await page.getByRole("button", { name: "Native campaigns", exact: true }).click();
  await page.getByLabel("Approved research", { exact: true }).selectOption(String(oid));
  for (const [index, text] of ["Can an original color change the emphasis of this frame?", "Choose an interval that supports the point instead of copying someone else's story.", "Keep the final composition clear. Which original choice would you keep?"].entries())
    await page.getByLabel(`Beat text ${index + 1}`, { exact: true }).fill(text);
  await page.getByLabel("Visual for beat 1", { exact: true }).selectOption(String(aid));
  const create = page.getByRole("button", { name: "Create native campaign", exact: true });
  await expect(create).toBeDisabled();
  await expect(page.getByRole("alert").filter({ hasText: "Video intervals currently require" })).toContainText("Short/Reel-only");
  await page.getByRole("group", { name: "Treatments to make", exact: true }).getByRole("checkbox", { name: "carousel", exact: true }).uncheck();
  await page.getByLabel("Video start for beat 1", { exact: true }).fill("1");
  await page.getByLabel("Video end for beat 1", { exact: true }).fill("3");
  await page.getByLabel("Video end treatment for beat 1", { exact: true }).selectOption("loop");
  await page.getByLabel("Background audio for beat 1", { exact: true }).selectOption(String(audioId));
  await page.getByLabel("Background start for beat 1", { exact: true }).fill("0.25");
  await page.getByLabel("Background end for beat 1", { exact: true }).fill("1.25");
  await page.getByLabel("Background relative level for beat 1 (dB)", { exact: true }).fill("-24");
  await page.getByLabel("Background end treatment for beat 1", { exact: true }).selectOption("loop");
  await page.getByLabel("Short caption", { exact: true }).fill("Compare this original interval with the new narrated visual point. Which composition reads most clearly?");
  const [saved] = await Promise.all([
    page.waitForResponse(r => r.url().endsWith("/api/editorial/campaigns") && r.request().method() === "POST"), create.click(),
  ]);
  expect(saved.ok()).toBe(true); const cid = (await saved.json()).id;
  const campaign = page.getByTestId(`campaign-${cid}`);
  await campaign.getByRole("button", { name: "Render short", exact: true }).click();
  const review = campaign.getByTestId("native-export-short");
  await expect(review).toContainText("Final decode: passed");
  await expect(review).toContainText("1.000–3.000s");
  await expect(review).toContainText("loop selected interval");
  await expect(review).toContainText("source audio excluded");
  await expect(review).toContainText("Selected background audio");
  await expect(review).toContainText("relative level -24.0dB");
  const player = review.locator("video");
  await expect.poll(() => player.evaluate((video: HTMLVideoElement) => video.readyState)).toBeGreaterThanOrEqual(2);
  await player.evaluate(async (video: HTMLVideoElement) => { video.muted = true; await video.play(); });
  await expect.poll(() => player.evaluate((video: HTMLVideoElement) => video.currentTime)).toBeGreaterThan(0.3);
  await player.evaluate((video: HTMLVideoElement) => video.pause());
  const campaigns = await (await request.get(`${stack.api}/api/editorial/campaigns`, { headers })).json();
  const exported = campaigns.find((row: any) => row.id === cid).exports[0];
  expect(exported.manifest.audio_sources[0]).toMatchObject({ asset_id: audioId, source_start: 0.25, source_end: 1.25, end_policy: "loop", gain_db: -24 });
  expect(exported.manifest.visual_sources[0]).toMatchObject({ asset_id: aid, source_start: 1, source_end: 3, end_policy: "loop", source_audio: "excluded" });
  copyFileSync(exported.manifest.video.file, path.join(evidence, "native-selected-clip-final.mp4"));
  writeFileSync(path.join(evidence, "native-selected-clip-manifest.json"), JSON.stringify(exported.manifest, null, 2));
  execFileSync("ffmpeg", ["-v", "error", "-xerror", "-i", exported.manifest.video.file, "-f", "null", "-"]);
  await review.getByRole("button", { name: "Approve inspected short", exact: true }).click();
  await expect(page.getByRole("form", { name: "Exact export acceptance" })).toContainText("loops the selected interval");
  await expect(page.getByRole("form", { name: "Exact export acceptance" })).toContainText("background audio");
  await acceptExactExport(page, "native-selected-clip-acceptance.png");
  await review.getByRole("checkbox", { name: "default:instagram", exact: true }).check();
  await review.getByRole("button", { name: "Add short to review queue", exact: true }).click();
  await expect(review).toContainText("Queue item #");
  await expect.poll(() => player.evaluate((video: HTMLVideoElement) => video.readyState)).toBeGreaterThanOrEqual(2);
  await player.evaluate((video: HTMLVideoElement) => { video.currentTime = 1.5; });
  await expect.poll(() => player.evaluate((video: HTMLVideoElement) => !video.seeking && video.readyState >= 2)).toBe(true);
  await review.screenshot({ path: path.join(evidence, "native-selected-clip-review.png") });
  const isolationAfter = await (await request.get(`${stack.api}/api/e2e/isolation`)).json();
  expect(isolationAfter.fake_publish_receipts).toBe(isolationBefore.fake_publish_receipts);
});

test("Native campaign recovery keeps copy and clip choices private through stale research and failed requests", async ({ page, request }) => {
  const errors: string[] = []; page.on("pageerror", error => errors.push(error.message));
  const opportunity = await request.post(`${stack.api}/api/editorial/opportunities`, { headers, data: {
    title: "Native draft recovery original fixture", angle: "Compare original compositions through explicit owner-selected media and distinct format copy.",
    audience: "Viewers of our original generated shape fixtures", formats: ["short", "reel", "carousel", "image", "community", "poll"],
    evidence: [{ url: "https://example.invalid/native-draft-recovery", excerpt: "All media and text are original synthetic fixtures made for private local testing.", entity: "Original shapes", retrieved_at: new Date().toISOString() }],
  } });
  expect(opportunity.ok()).toBe(true); const oid = (await opportunity.json()).id;
  expect((await request.post(`${stack.api}/api/editorial/opportunities/${oid}/approve`, { headers })).ok()).toBe(true);
  const imported = await request.post(`${stack.api}/api/editorial/assets/media`, { headers, multipart: {
    kind: "video", license: "owner_created", attestation: "Original locally generated footage, permitted for this private recovery fixture.", description: "Original colored panels and shapes from the isolated harness.",
    file: { name: "original.mp4", mimeType: "video/mp4", buffer: readFileSync(path.join(stack.root, "fixtures/trailer.mp4")) },
  } });
  expect(imported.ok()).toBe(true); const aid = (await imported.json()).id;
  const audioImported = await request.post(`${stack.api}/api/editorial/assets/media`, { headers, multipart: {
    kind: "audio", license: "owner_created", attestation: "Original generated tone made for this private local recovery fixture.", description: "Original synthetic1100Hz tone for explicit background-audio selection.",
    file: { name: "original-tone.wav", mimeType: "audio/wav", buffer: readFileSync(originalAudioFixture()) },
  } });
  expect(audioImported.ok()).toBe(true); const audioId = (await audioImported.json()).id;
  const key = `screen-central:${stack.api}:native-campaign`;
  const posts: any[] = [];
  let mode = "validation"; let lateCreated = false; let releaseLate: () => void = () => {};
  const lateGate = new Promise<void>(resolve => { releaseLate = resolve; });
  await page.route("**/api/editorial/campaigns", async route => {
    if (route.request().method() === "POST") {
      posts.push(route.request().postDataJSON());
      if (mode === "validation") return route.fulfill({ status: 422, contentType: "application/json", body: JSON.stringify({ detail: [{ loc: ["body", "title"], msg: "Repair the treatment title", input: "private-do-not-display" }] }) });
      if (mode === "abort") return route.abort("failed");
      if (mode === "late-response") { const response = await route.fetch(); expect(response.ok()).toBe(true); lateCreated = true; await lateGate; return route.fulfill({ response }); }
    } else if (mode === "saved-read-failed") return route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "Synthetic view read unavailable" }) });
    return route.continue();
  });
  await page.goto("/#editorial"); await page.getByRole("button", { name: "Native campaigns", exact: true }).click();
  const form = page.getByRole("form", { name: "Native campaign draft", exact: true });
  await form.getByLabel("Approved research", { exact: true }).selectOption(String(oid));
  await form.getByLabel("Campaign title", { exact: true }).fill("My unsaved original composition treatment");
  for (const [index, text] of ["Can this original shape change the way a frame reads?", "Choose source intervals that support the new point instead of copying a narrative.", "Keep one clear original choice. Which composition would you keep?"].entries())
    await form.getByLabel(`Beat text ${index + 1}`, { exact: true }).fill(text);
  const treatments = form.getByRole("group", { name: "Treatments to make", exact: true });
  for (const fmt of ["reel", "image", "community", "poll"]) await treatments.getByRole("checkbox", { name: fmt, exact: true }).check();
  const words = { "Short caption": "My original Short copy remains private until I save it.", "Carousel caption": "My distinct original carousel copy also survives navigation.",
    "Reel caption": "My separate Reel treatment copy should remain available.", "Single-image text": "One original shape changes this composition.", "Single-image caption": "Choose the clearest original still composition.",
    "Community post text": "We created these original compositions. Which framing choice makes the point most clearly, and why?", "Poll question": "Which original frame makes the idea clearest?", "Poll options (one per line, 2–4)": "Quiet corner\nBold center", "Community question draft": "What would you change in this original composition?" };
  for (const [label, value] of Object.entries(words)) await form.getByLabel(label, { exact: true }).fill(value);
  await form.getByLabel("Visual for beat 1", { exact: true }).selectOption(String(aid));
  await form.getByLabel("Video start for beat 1", { exact: true }).fill("0.5");
  await form.getByLabel("Video end for beat 1", { exact: true }).fill("2.5");
  await form.getByLabel("Video end treatment for beat 1", { exact: true }).selectOption("hold");
  await form.getByLabel("Background audio for beat 1", { exact: true }).selectOption(String(audioId));
  await form.getByLabel("Background start for beat 1", { exact: true }).fill("0.25");
  await form.getByLabel("Background end for beat 1", { exact: true }).fill("1.25");
  await form.getByLabel("Background relative level for beat 1 (dB)", { exact: true }).fill("-24");
  await form.getByLabel("Background end treatment for beat 1", { exact: true }).selectOption("silence");
  await expect.poll(() => page.evaluate(key => localStorage.getItem(key), key)).toBeTruthy();
  const cached = await page.evaluate(key => JSON.parse(localStorage.getItem(key)!), key);
  await page.getByRole("button", { name: "Opportunities & calendar", exact: true }).click();
  await page.getByRole("button", { name: "Native campaigns", exact: true }).click();
  await form.getByRole("button", { name: "Restore unsaved draft", exact: true }).click();
  expect(posts).toHaveLength(0);
  await expect(form.getByLabel("Campaign title", { exact: true })).toHaveValue("My unsaved original composition treatment");
  // A newly reviewed source must not silently adopt the old recovered selection.
  const old = (await (await request.get(`${stack.api}/api/editorial/opportunities`, { headers })).json()).find((row: any) => row.id === oid);
  const edited = Object.fromEntries(["title", "angle", "audience", "formats", "evidence"].map(field => [field, old[field]]));
  edited.angle = "Updated approved research context: inspect the original composition question again before reusing this private treatment.";
  expect((await request.put(`${stack.api}/api/editorial/opportunities/${oid}`, { headers, data: edited })).ok()).toBe(true);
  expect((await request.post(`${stack.api}/api/editorial/opportunities/${oid}/approve`, { headers })).ok()).toBe(true);
  await page.reload(); await page.getByRole("button", { name: "Native campaigns", exact: true }).click();
  await form.getByRole("button", { name: "Restore unsaved draft", exact: true }).click();
  for (const [label, value] of Object.entries(words)) await expect(form.getByLabel(label, { exact: true })).toHaveValue(value);
  await expect(form.getByLabel("Video start for beat 1", { exact: true })).toHaveValue("0.5");
  await expect(form.getByLabel("Video end for beat 1", { exact: true })).toHaveValue("2.5");
  await expect(form.getByLabel("Video end treatment for beat 1", { exact: true })).toHaveValue("hold");
  await expect(form.getByLabel("Background audio for beat 1", { exact: true })).toHaveValue(String(audioId));
  await expect(form.getByLabel("Background start for beat 1", { exact: true })).toHaveValue("0.25");
  await expect(form.getByLabel("Background end for beat 1", { exact: true })).toHaveValue("1.25");
  await expect(form.getByLabel("Background relative level for beat 1 (dB)", { exact: true })).toHaveValue("-24");
  await expect(form.getByLabel("Background end treatment for beat 1", { exact: true })).toHaveValue("silence");
  const save = form.getByRole("button", { name: "Create native campaign", exact: true });
  await expect(save).toBeDisabled(); expect(posts).toHaveLength(0);
  await form.getByText("Current approved research context", { exact: true }).click();
  await expect(form).toContainText("Updated approved research context");
  await form.getByRole("button", { name: "Use current approved research for this draft", exact: true }).click();
  for (const fmt of ["carousel", "image", "community", "poll"]) await treatments.getByRole("checkbox", { name: fmt, exact: true }).uncheck();
  await save.click(); await expect(page.getByRole("status").filter({ hasText: "Repair the treatment title" })).toBeVisible();
  await expect(page.getByText("private-do-not-display", { exact: false })).toHaveCount(0);
  await expect(form.getByLabel("Campaign title", { exact: true })).toHaveValue("My unsaved original composition treatment");
  mode = "abort"; await save.click(); await expect(page.getByRole("status").filter({ hasText: "could not be confirmed" })).toBeVisible();
  expect(posts).toHaveLength(2);
  await expect(form.getByLabel("Video end treatment for beat 1", { exact: true })).toHaveValue("hold");
  mode = "late-response"; await save.click();
  await expect.poll(() => lateCreated).toBe(true);
  await expect(form.getByLabel("Campaign title", { exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Opportunities & calendar", exact: true }).click();
  await page.getByRole("button", { name: "Native campaigns", exact: true }).click();
  await form.getByRole("button", { name: "Restore unsaved draft", exact: true }).click();
  await form.getByLabel("Campaign title", { exact: true }).fill("Newer private words after returning to the editor");
  await expect.poll(() => page.evaluate(key => localStorage.getItem(key), key)).toContain("Newer private words");
  const lateResponse = page.waitForResponse(r => r.url().endsWith("/api/editorial/campaigns") && r.request().method() === "POST" && r.status() === 200);
  releaseLate(); await (await lateResponse).finished();
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => resolve())));
  await expect(form.getByLabel("Campaign title", { exact: true })).toHaveValue("Newer private words after returning to the editor");
  await expect.poll(() => page.evaluate(key => localStorage.getItem(key), key)).toContain("Newer private words");
  expect(posts).toHaveLength(3);
  mode = "saved-read-failed"; await save.click();
  await expect(page.getByRole("status").filter({ hasText: "Campaign draft saved." })).toContainText("Refresh unavailable");
  await expect(form.getByLabel("Campaign title", { exact: true })).toHaveValue("");
  await expect.poll(() => page.evaluate(key => localStorage.getItem(key), key)).toBeNull();
  expect(posts).toHaveLength(4);
  expect(posts[3].beats[0]).toMatchObject({ asset_id: aid, clip_start: 0.5, clip_end: 2.5, clip_end_policy: "hold" });
  expect(posts[3].beats[0]).toMatchObject({ background_audio_id: audioId, audio_start: 0.25, audio_end: 1.25, audio_gain_db: -24, audio_end_policy: "silence" });
  expect(posts[3].expected_research_fingerprint).not.toBe(old.approval.fingerprint);
  const campaigns = (await (await request.get(`${stack.api}/api/editorial/campaigns`, { headers })).json()).filter((row: any) => row.opportunity_id === oid);
  expect(campaigns).toHaveLength(2);
  expect(new Set(campaigns.map((row: any) => row.brief.title))).toEqual(new Set(["My unsaved original composition treatment", "Newer private words after returning to the editor"]));
  mode = "read-ok"; await page.getByRole("button", { name: "Refresh native campaigns", exact: true }).click();
  await expect(page.getByTestId(`campaign-${campaigns[0].id}`)).toBeVisible(); expect(posts).toHaveLength(4);
  await page.evaluate(key => {
    (window as any).__nativeDraftSetItem = Storage.prototype.setItem;
    Storage.prototype.setItem = function (name, value) { if (name === key) throw new DOMException("Synthetic storage failure", "QuotaExceededError"); return (window as any).__nativeDraftSetItem.call(this, name, value); };
  }, key);
  await form.getByLabel("Campaign title", { exact: true }).fill("Keep these words when storage fails");
  await expect(form.getByRole("alert").filter({ hasText: "Browser draft storage is unavailable" })).toBeVisible();
  await page.getByRole("button", { name: "Opportunities & calendar", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(form.getByLabel("Campaign title", { exact: true })).toHaveValue("Keep these words when storage fails");
  await form.getByRole("button", { name: "Discard unsaved changes", exact: true }).click();
  await page.getByRole("button", { name: "Opportunities & calendar", exact: true }).click();
  await page.evaluate(({ key, cached }) => {
    Storage.prototype.setItem = (window as any).__nativeDraftSetItem;
    cached.value.beats = '{"malformed":"recover these private words"}'; localStorage.setItem(key, JSON.stringify(cached));
  }, { key, cached });
  await page.getByRole("button", { name: "Native campaigns", exact: true }).click();
  await form.getByRole("button", { name: "Restore unsaved draft", exact: true }).click();
  await expect(form.getByRole("alert").filter({ hasText: "Recovered treatment data is malformed" })).toBeVisible();
  await expect(save).toBeDisabled(); expect(posts).toHaveLength(4); expect(errors).toEqual([]);
  await form.getByText("Retained treatment words", { exact: true }).click();
  await expect(form).toContainText("recover these private words");
  await form.screenshot({ path: path.join(evidence, "native-draft-recovery.png") });
  await form.getByRole("button", { name: "Discard unsaved changes", exact: true }).click();
  expect(posts).toHaveLength(4);
});

test("Asset import recovery retains private metadata, honest receipts and current catalog reads", async ({ page, request }) => {
  const errors: string[] = []; page.on("pageerror", error => errors.push(error.message));
  const files = ["asset-recovery-original", "asset-recovery-second"].map(name => path.join(stack.root, `fixtures/${name}.wav`));
  for (const [i, file] of files.entries()) execFileSync("ffmpeg", ["-v", "error", "-y", "-f", "lavfi", "-i", `sine=f=${1234 + i}:d=3`, file]);
  const key = `screen-central:${stack.api}:asset-import`;
  let reads = "fail", mode = "validation", posts = 0, lateCreated = false, slowHeld = false;
  let releaseLate: () => void = () => {}, releaseSlow: () => void = () => {};
  const lateGate = new Promise<void>(resolve => { releaseLate = resolve; }), slowGate = new Promise<void>(resolve => { releaseSlow = resolve; });
  await page.route("**/api/editorial/assets/search*", async route => {
    if (new URL(route.request().url()).searchParams.get("q") === "slow-first") { const response = await route.fetch(); slowHeld = true; await slowGate; return route.fulfill({ response }); }
    if (reads === "fail") return route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "Synthetic catalog read unavailable" }) });
    return route.continue();
  });
  await page.route("**/api/editorial/assets/media", async route => {
    if (route.request().method() !== "POST") return route.continue(); posts++;
    if (mode === "validation") return route.fulfill({ status: 422, contentType: "application/json", body: JSON.stringify({ detail: [{ loc: ["body", "description"], msg: "Repair the source description", input: "private-validation-input" }] }) });
    if (mode === "abort") return route.abort("failed");
    if (mode === "late") {
      // Browser multipart replay omits file bytes. Create the same local fixture
      // import explicitly, then delay its real receipt; direct UI upload passed above.
      const meta = await page.evaluate(key => JSON.parse(localStorage.getItem(key)!).value, key);
      const response = await request.post(route.request().url(), { headers, multipart: { kind: meta.kind, license: meta.license,
        attestation: meta.attestation, description: meta.description, tags: meta.tags, people: meta.people,
        caption_cues: JSON.stringify(JSON.parse(meta.cues).map((cue: { start: string; end: string; text: string }) => ({ start: Number(cue.start), end: Number(cue.end), text: cue.text }))),
        file: { name: path.basename(files[1]), mimeType: "audio/wav", buffer: readFileSync(files[1]) } } });
      expect(response.ok(), await response.text()).toBe(true); lateCreated = true; await lateGate; return route.fulfill({ response });
    }
    return route.continue();
  });
  const openImport = async () => { await page.getByRole("button", { name: "Asset library", exact: true }).click(); await page.getByText("Index a local video or audio asset", { exact: true }).click(); };
  await page.goto("/#editorial"); await openImport();
  await expect(page.getByRole("alert").filter({ hasText: "Asset catalog unavailable" })).toContainText("Catalog contents are unconfirmed");
  await expect(page.getByText("0 matching assets", { exact: false })).toHaveCount(0);
  reads = "normal"; await page.getByRole("button", { name: "Refresh asset catalog", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Asset catalog unavailable" })).toHaveCount(0);
  const form = page.getByRole("form", { name: "Authorized asset import", exact: true });
  await form.getByLabel("Asset kind", { exact: true }).selectOption("audio");
  await form.getByLabel("Local media file", { exact: true }).setInputFiles(files[0]);
  const rights = "Original locally generated tones, authorized for this private metadata recovery fixture.", description = "Original recoverytone signal and owner caption metadata, created for local tests.";
  await form.getByLabel("Asset rights and intended use", { exact: true }).fill(rights);
  await form.getByLabel("Asset scene description", { exact: true }).fill(description);
  await form.getByLabel("Asset tags", { exact: true }).fill("original, recoverytone");
  await form.getByLabel("People/entities (owner labels)", { exact: true }).fill("No real people");
  await form.getByRole("button", { name: "Add caption interval", exact: true }).click();
  for (const [label, value] of [["start", "0.2"], ["end", "1.8"], ["text", "My original private caption words survive navigation."]]) await form.getByLabel(`Cue 1 ${label}`, { exact: true }).fill(value);
  await page.getByRole("button", { name: "Opportunities & calendar", exact: true }).click(); await openImport();
  await form.getByRole("button", { name: "Restore unsaved draft", exact: true }).click();
  await expect(form.getByLabel("Asset rights and intended use", { exact: true })).toHaveValue(rights);
  await expect(form.getByLabel("Cue 1 text", { exact: true })).toHaveValue("My original private caption words survive navigation."); expect(posts).toBe(0);
  await page.reload(); await openImport(); await form.getByRole("button", { name: "Restore unsaved draft", exact: true }).click();
  for (const [label, value] of [["Asset kind", "audio"], ["Asset scene description", description], ["Cue 1 start", "0.2"], ["Cue 1 end", "1.8"]]) await expect(form.getByLabel(label, { exact: true })).toHaveValue(value);
  await expect(form.getByRole("status").filter({ hasText: "Choose the local file again" })).toBeVisible();
  const index = form.getByRole("button", { name: "Index local media", exact: true });
  await expect(index).toBeDisabled(); expect(posts).toBe(0);
  await form.getByLabel("Local media file", { exact: true }).setInputFiles(files[0]);
  await index.click(); await expect(page.getByRole("status").filter({ hasText: "Repair the source description" })).toBeVisible();
  await expect(page.getByText("private-validation-input", { exact: false })).toHaveCount(0);
  await expect(form.getByLabel("Asset rights and intended use", { exact: true })).toHaveValue(rights);
  mode = "abort"; await index.click(); await expect(page.getByRole("status").filter({ hasText: "could not be confirmed" })).toBeVisible(); expect(posts).toBe(2);
  mode = "real"; reads = "fail";
  const [saved] = await Promise.all([page.waitForResponse(r => r.url().endsWith("/api/editorial/assets/media") && r.request().method() === "POST" && r.status() === 200), index.click()]);
  const id = (await saved.json()).id;
  await expect(page.getByRole("status").filter({ hasText: "Media indexed locally" })).toContainText("Catalog refresh unavailable");
  await expect.poll(() => page.evaluate(key => localStorage.getItem(key), key)).toBeNull(); expect(posts).toBe(3);
  const retained = await (await request.get(`${stack.api}/api/editorial/assets/${id}`, { headers })).json();
  expect(retained.caption_cues[0]).toMatchObject({ start: 0.2, end: 1.8, text: "My original private caption words survive navigation." });
  reads = "normal"; await page.getByRole("button", { name: "Refresh asset catalog", exact: true }).click();
  await expect(page.getByTestId(`indexed-asset-${id}`)).toBeVisible(); expect(posts).toBe(3);
  await form.getByLabel("Asset scene description", { exact: true }).fill("New private metadata must not silently replace the retained duplicate's provenance.");
  await index.click(); await expect(page.getByRole("status").filter({ hasText: "Duplicate media reused" })).toContainText("form metadata has not replaced");
  await expect.poll(() => page.evaluate(key => localStorage.getItem(key), key)).toContain("New private metadata");
  expect((await (await request.get(`${stack.api}/api/editorial/assets/${id}`, { headers })).json()).provenance.description).toBe(description);
  mode = "late"; await form.getByLabel("Local media file", { exact: true }).setInputFiles(files[1]); await index.click();
  await expect.poll(() => lateCreated).toBe(true); await expect(form.getByLabel("Asset scene description", { exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Opportunities & calendar", exact: true }).click(); await openImport();
  await form.getByRole("button", { name: "Restore unsaved draft", exact: true }).click();
  const newer = "Newer private metadata after returning to the editor must survive the late receipt.";
  await form.getByLabel("Asset scene description", { exact: true }).fill(newer);
  const lateResponse = page.waitForResponse(r => r.url().endsWith("/api/editorial/assets/media") && r.request().method() === "POST" && r.status() === 200);
  releaseLate(); await (await lateResponse).finished(); await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => resolve())));
  await expect(form.getByLabel("Asset scene description", { exact: true })).toHaveValue(newer);
  await expect.poll(() => page.evaluate(key => localStorage.getItem(key), key)).toContain("Newer private metadata"); expect(posts).toBe(5);
  const cached = await page.evaluate(key => JSON.parse(localStorage.getItem(key)!), key);
  await page.evaluate(key => { (window as any).__assetDraftSetItem = Storage.prototype.setItem; Storage.prototype.setItem = function (name, value) {
    if (name === key) throw new DOMException("Synthetic storage failure", "QuotaExceededError"); return (window as any).__assetDraftSetItem.call(this, name, value);
  }; }, key);
  await form.getByLabel("Cue 1 text", { exact: true }).fill("Keep my private caption when browser storage fails.");
  await expect(form.getByRole("alert").filter({ hasText: "Browser draft storage is unavailable" })).toBeVisible();
  await page.getByRole("button", { name: "Native campaigns", exact: true }).click(); await page.getByRole("dialog").getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(form.getByLabel("Cue 1 text", { exact: true })).toHaveValue("Keep my private caption when browser storage fails.");
  await form.getByRole("button", { name: "Discard unsaved changes", exact: true }).click(); await page.getByRole("button", { name: "Opportunities & calendar", exact: true }).click();
  await page.evaluate(({ key, cached }) => { Storage.prototype.setItem = (window as any).__assetDraftSetItem; cached.value.cues = '{"malformed":"recover these private caption words"}'; localStorage.setItem(key, JSON.stringify(cached)); }, { key, cached });
  await openImport(); await form.getByRole("button", { name: "Restore unsaved draft", exact: true }).click();
  await expect(form.getByRole("alert").filter({ hasText: "Recovered asset-import data is malformed" })).toBeVisible(); await expect(index).toBeDisabled();
  await form.getByText("Retained caption words", { exact: true }).click(); await expect(form).toContainText("recover these private caption words");
  await form.screenshot({ path: path.join(evidence, "asset-import-recovery.png") });
  await form.getByRole("button", { name: "Discard unsaved changes", exact: true }).click();
  const search = page.getByRole("form", { name: "Asset search", exact: true });
  await search.getByLabel("Search asset context", { exact: true }).fill("slow-first"); await search.getByRole("button", { name: "Search indexed assets", exact: true }).click();
  await expect.poll(() => slowHeld).toBe(true);
  await search.getByLabel("Search asset context", { exact: true }).fill("recoverytone"); await search.getByRole("button", { name: "Search indexed assets", exact: true }).click();
  await expect(page.getByTestId(`indexed-asset-${id}`)).toBeVisible(); await expect(page.getByText("Last loaded search: recoverytone", { exact: false })).toBeVisible();
  const slowResponse = page.waitForResponse(r => r.url().includes("/api/editorial/assets/search") && new URL(r.url()).searchParams.get("q") === "slow-first");
  releaseSlow(); await (await slowResponse).finished(); await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => resolve())));
  await expect(page.getByTestId(`indexed-asset-${id}`)).toBeVisible(); await expect(page.getByText("Last loaded search: recoverytone", { exact: false })).toBeVisible();
  reads = "fail"; await search.getByRole("button", { name: "Refresh asset catalog", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Asset catalog unavailable" })).toContainText("Showing the last loaded results");
  await expect(page.getByTestId(`indexed-asset-${id}`)).toBeVisible(); expect(posts).toBe(5); expect(errors).toEqual([]);
  await page.screenshot({ path: path.join(evidence, "asset-catalog-read-recovery.png"), fullPage: true });
  reads = "normal"; await search.getByRole("button", { name: "Refresh asset catalog", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Asset catalog unavailable" })).toHaveCount(0); expect(posts).toBe(5);
});

test("Reviewed asset decisions preserve provenance, recover stale words and require revised native sources", async ({ page, request }) => {
  const errors: string[] = []; page.on("pageerror", e => errors.push(e.message));
  const source = path.join(stack.root, "fixtures/annotation-original.wav");
  execFileSync("ffmpeg", ["-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=1471:d=3", source]);
  // Setup is a real local import and research decision; edits/rendering below use the browser.
  const imported = await request.post(`${stack.api}/api/editorial/assets/media`, { headers, multipart: {
    kind: "audio", license: "owner_created", attestation: "Original procedural tone created for the isolated owner review fixture.",
    description: "An original archival metadata tone for source permission tests.", tags: "annotationtone", people: "No real people",
    caption_cues: "[]", file: { name: "original.wav", mimeType: "audio/wav", buffer: readFileSync(source) } } });
  expect(imported.ok(), await imported.text()).toBe(true); const asset = await imported.json(), aid = asset.id;
  const endpoint = `${stack.api}/api/editorial/assets/${aid}/metadata-review`;
  const original = await (await request.get(endpoint, { headers })).json();
  const research = await request.post(`${stack.api}/api/editorial/opportunities`, { headers, data: {
    title: "Original annotation sound study", angle: "Study three original procedural shapes and their privately reviewed sound treatment.",
    audience: "Viewers learning original motion design", formats: ["short"], evidence: [{ url: "https://example.org/annotation-original",
      excerpt: "All procedural shapes and sound were originally generated for this isolated fixture.", entity: "Synthetic originals", retrieved_at: new Date().toISOString() }] } });
  expect(research.ok(), await research.text()).toBe(true); const oid = (await research.json()).id;
  expect((await request.post(`${stack.api}/api/editorial/opportunities/${oid}/approve`, { headers })).ok()).toBe(true);
  const campaign = await request.post(`${stack.api}/api/editorial/campaigns`, { headers, data: {
    opportunity_id: oid, title: "An original sound and shape study", formats: ["short"],
    beats: ["hook", "body", "payoff"].map((role, i) => ({ role, text: ["Can an original shape change the way this story opens?", "A deliberate second frame changes the emphasis of our example.", "The final original shape completes this synthetic visual story."][i], source_refs: [1],
      ...(i === 0 ? { background_audio_id: aid, audio_metadata_version: original.version, audio_start: .25, audio_end: 1.25, audio_gain_db: -24, audio_end_policy: "silence" } : {}) })),
    short_caption: "Inspect the complete original shape and sound treatment.", carousel_caption: "", community_question: "" } });
  expect(campaign.ok(), await campaign.text()).toBe(true); const cid = (await campaign.json()).id;
  const retained = await request.post(`${stack.api}/api/editorial/campaigns/${cid}/render/short`, { headers });
  expect(retained.ok(), await retained.text()).toBe(true); const old = await retained.json();
  await page.goto("/#editorial"); await page.getByRole("button", { name: "Asset library", exact: true }).click();
  const card = page.getByTestId(`indexed-asset-${aid}`);
  await card.getByRole("button", { name: "Review metadata and use permission", exact: true }).click();
  const form = card.getByRole("form", { name: `Asset ${aid} metadata decision`, exact: true });
  const description = form.getByLabel("Reviewed description", { exact: true }), rationale = form.getByLabel("Reason for this exact decision", { exact: true });
  const ack = form.getByRole("checkbox"), save = form.getByRole("button", { name: "Save reviewed asset decision", exact: true });
  await description.fill("Private corrected violet sound labels survive navigation and stale reviews.");
  await rationale.fill("Recheck the original generated source and correct its owner labels intentionally.");
  await page.getByRole("button", { name: "Native campaigns", exact: true }).click();
  await page.getByRole("button", { name: "Asset library", exact: true }).click();
  await card.getByRole("button", { name: "Review metadata and use permission", exact: true }).click();
  await form.getByRole("button", { name: "Restore unsaved draft", exact: true }).click();
  await expect(description).toHaveValue("Private corrected violet sound labels survive navigation and stale reviews.");
  const competing = await request.post(endpoint, { headers, data: { ...original.metadata, expected_version: original.version,
    description: "A concurrent owner decision changes the source metadata before this form saves.", rationale: "An independent current review must never be overwritten by a stale form.", reviewed: true } });
  expect(competing.ok(), await competing.text()).toBe(true);
  await ack.check(); await save.click();
  await expect(form).toContainText("Asset metadata changed"); await expect(description).toHaveValue("Private corrected violet sound labels survive navigation and stale reviews.");
  await expect(ack).not.toBeChecked(); await expect(save).toBeDisabled();
  await form.getByRole("button", { name: "Use current source version with retained draft", exact: true }).click();
  await form.getByLabel("Reviewed tags", { exact: true }).fill("annotationtone, violetlabels");
  await form.getByRole("button", { name: "Add reviewed caption interval", exact: true }).click();
  await form.getByLabel("Reviewed cue 1 text", { exact: true }).fill("A violet owner caption remains unverified.");
  await ack.check(); await description.fill("Corrected violet source labels after reviewing the concurrent owner decision.");
  await expect(ack).not.toBeChecked(); await expect(save).toBeDisabled(); await ack.check(); await save.click();
  await expect(form).toContainText("Reviewed metadata saved as an immutable owner decision.");
  let current = await (await request.get(endpoint, { headers })).json(); expect(current.history_count).toBe(2); expect(current.original).toEqual(original.original);
  expect(current.metadata.caption_cues).toEqual([{ start: 0, end: 1, text: "A violet owner caption remains unverified." }]);
  await form.getByLabel("Use permission", { exact: true }).selectOption("blocked");
  await rationale.fill("Block further use pending a fresh review of the intended sound treatment."); await ack.check(); await save.click();
  await expect(card.getByText("Use permission: blocked", { exact: true })).toBeVisible();
  expect((await request.get(`${stack.api}/api/editorial/exports/${old.id}/export-review`, { headers })).status()).toBe(409);
  expect((await request.post(`${stack.api}/api/editorial/campaigns/${cid}/render/short`, { headers })).status()).toBe(409);
  await form.getByLabel("Use permission", { exact: true }).selectOption("permitted");
  await rationale.fill("Permit this original source after rechecking the exact intended private treatment."); await ack.check(); await save.click();
  await expect(card.getByText("Use permission: permitted", { exact: true })).toBeVisible();
  await form.getByText("Immutable metadata/use history (latest 20)", { exact: true }).click();
  await expect(form).toContainText("Revision 4"); await form.screenshot({ path: path.join(evidence, "asset-metadata-history.png") });
  await page.getByRole("button", { name: "Native campaigns", exact: true }).click();
  await page.getByTestId(`campaign-${cid}`).getByRole("button", { name: "Draft revised treatment", exact: true }).click();
  const draft = page.getByRole("form", { name: "Native campaign draft", exact: true });
  await expect(draft.getByRole("button", { name: "Create native campaign", exact: true })).toBeDisabled();
  await draft.getByText("Current audio provenance for beat 1", { exact: true }).click();
  await expect(draft).toContainText("Corrected violet source labels after reviewing the concurrent owner decision.");
  await draft.getByRole("button", { name: "Use current audio metadata for beat 1", exact: true }).click();
  const createdResponse = page.waitForResponse(r => r.url().endsWith("/api/editorial/campaigns") && r.request().method() === "POST" && r.status() === 200);
  await draft.getByRole("button", { name: "Create native campaign", exact: true }).click(); const revised = await (await createdResponse).json();
  current = await (await request.get(endpoint, { headers })).json(); expect(revised.brief.beats[0].audio_metadata_version).toBe(current.version);
  expect(revised.brief.beats[0]).toMatchObject({ audio_start: .25, audio_end: 1.25, audio_gain_db: -24, audio_end_policy: "silence" });
  const revisedCard = page.getByTestId(`campaign-${revised.id}`); await revisedCard.getByRole("button", { name: "Render short", exact: true }).click();
  await expect(revisedCard.getByTestId("native-export-short")).toBeVisible();
  await revisedCard.getByRole("button", { name: "Approve inspected short", exact: true }).click();
  await acceptExactExport(page, "asset-revised-native-acceptance.png");
  expect(errors).toEqual([]);
});
