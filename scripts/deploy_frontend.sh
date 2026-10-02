#!/usr/bin/env bash
# Deploy the frontend to Vercel production
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO/frontend"

TOKEN="${VERCEL_TOKEN:-${VERCEL_API_TOKEN:-}}"
TOKEN_FLAG=""
if [ -n "$TOKEN" ]; then
  TOKEN_FLAG="--token $TOKEN"
fi

echo "Deploying frontend to Vercel production..."
vercel --prod --yes $TOKEN_FLAG
echo "✓ Frontend deployed to https://scrapper.nodepilot.dev"
