import { test, expect } from "@playwright/test";
import { readFileSync, writeFileSync, copyFileSync } from "node:fs";
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
  await context.route("**/*", route => {
    const hostname = new URL(route.request().url()).hostname;
    return ["127.0.0.1", "localhost"].includes(hostname) ? route.continue() : route.abort("blockedbyclient");
  });
  await page.addInitScript(({ api, token }) => {
    localStorage.setItem("scrapper_api_base", api);
    localStorage.setItem("scrapper_token", token);
  }, { api: stack.api, token: stack.token });
});

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
  await page.getByRole("button", { name: "Confirm titles", exact: true }).click();
  await page.getByRole("button", { name: "▶ Make segments", exact: true }).click();
  await expect.poll(async () => (await getCountdown()).segments_made, { timeout: 180_000 }).toBe(5);
  await expect(page.getByRole("button", { name: "Render video", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Render video", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "OK", exact: true }).click();
  await expect(page.getByTestId("countdown-export-quality")).toContainText("decoded", { timeout: 120_000 });
  const completed = await getCountdown();
  expect(completed.render.quality.errors).toEqual([]);
  const final = path.join(evidence, "countdown-top5-final.mp4");
  copyFileSync(completed.render.file, final);
  const probe = JSON.parse(execFileSync("ffprobe", ["-v", "error", "-show_streams", "-show_format", "-of", "json", final], { encoding: "utf8" }));
  expect(probe.streams.some((s: any) => s.codec_type === "audio")).toBe(true);
  expect(probe.streams.find((s: any) => s.codec_type === "video").height).toBe(1080);
  execFileSync("ffmpeg", ["-v", "error", "-xerror", "-i", final, "-f", "null", "-"]);
  writeFileSync(path.join(evidence, "countdown-probe.json"), JSON.stringify(probe, null, 2));
  writeFileSync(path.join(evidence, "countdown-quality.json"), JSON.stringify(completed.render.quality, null, 2));
  await page.screenshot({ path: path.join(evidence, "countdown-review.png"), fullPage: true });
  await page.getByRole("button", { name: "Approve inspected countdown", exact: true }).click();
  await page.getByRole("button", { name: "📤 Send to Countdowns queue", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "OK", exact: true }).click();
  await expect.poll(async () => !!(await getCountdown()).queue_item_id).toBe(true);
  await page.screenshot({ path: path.join(evidence, "countdown-queued.png"), fullPage: true });
  writeFileSync(path.join(evidence, "countdown-browser-errors.json"), JSON.stringify(errors, null, 2));
  const isolation = await (await request.get(`${stack.api}/api/e2e/isolation`)).json();
  writeFileSync(path.join(evidence, "final-isolation.json"), JSON.stringify(isolation, null, 2));
  expect(isolation.blocked_network_attempts).toBe(0);
  expect(isolation.scheduler_threads).toEqual([]);
  expect(isolation.fake_publish_receipts).toBe(3);  // only the explicit LongForm submit; no auto-posting
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
  await page.getByLabel("Short/Reel caption", { exact: true }).fill("Watch our original shape shift attention across the frame. Which composition would you keep?");
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
    await page.getByRole("dialog").getByRole("button", { name: "OK", exact: true }).click();
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
