// The version of the site that is live right now (same value the page was built with).
export const dynamic = "force-static";

export function GET() {
  return Response.json({ build: process.env.NEXT_PUBLIC_BUILD_ID || "dev" });
}
