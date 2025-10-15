#!/usr/bin/env bash
set -e
python scanner.py --min-vol 500000 --min-price 0.01 --limit 180

# Push CSV về GitHub nếu cấu hình token
if [ -n "$GH_TOKEN" ]; then
  git config user.name  "${GH_USERNAME:-render-bot}"
  git config user.email "${GH_EMAIL:-render-bot@noreply}"
  git remote set-url origin https://${GH_TOKEN}@github.com/${GH_REPO}.git
  git add scan_results.csv || true
  git commit -m "Render cron: update scan_results.csv $(date -u +'%Y-%m-%dT%H:%M:%SZ')" || true
  git push origin ${GH_BRANCH:-main} || true
fi
