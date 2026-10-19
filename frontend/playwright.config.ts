import { defineConfig } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";
const evidence = path.resolve(__dirname, "../artifacts/e2e");
const stack = JSON.parse(readFileSync(path.join(evidence, "stack.json"), "utf8"));
if (!stack.web.startsWith("http://127.0.0.1:")) throw new Error("Local E2E only");

export default defineConfig({
  testDir: "./e2e", timeout: 180_000, expect: { timeout: 30_000, poll: { intervals: [1000, 2000, 5000] } }, workers: 1,
  reporter: [["list"], ["json", { outputFile: path.join(evidence, "playwright-report.json") }]],
  outputDir: path.join(evidence, "browser"),
  use: { baseURL: stack.web, trace: "on", screenshot: "only-on-failure",
    actionTimeout: 15_000,
    launchOptions: process.env.E2E_CHROME_PATH ? { executablePath: process.env.E2E_CHROME_PATH } : {} },
});
