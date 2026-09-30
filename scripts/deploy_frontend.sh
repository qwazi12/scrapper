#!/usr/bin/env bash
# Deploy the frontend to Vercel production
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO/frontend"

echo "Deploying frontend to Vercel production..."
vercel --prod --yes
echo "✓ Frontend deployed to https://scrapper.nodepilot.dev"
