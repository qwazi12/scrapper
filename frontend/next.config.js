/** @type {import('next').NextConfig} */
// Every build carries its own version (the git commit in CI), so an open page can
// tell that a newer one is live and offer a reload (2026-10-06: phones kept an old copy).
const build = process.env.GITHUB_SHA || process.env.VERCEL_GIT_COMMIT_SHA || String(Date.now());

const nextConfig = {
  reactStrictMode: true,
  env: { NEXT_PUBLIC_BUILD_ID: build.slice(0, 12) },
};
module.exports = nextConfig;
