#!/bin/bash
# Downloads the Olist Brazilian E-Commerce dataset from Kaggle into data/olist/.
#
# The dataset (https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)
# is licensed CC BY-NC-SA 4.0 by Olist — non-commercial use, attribution
# required. Rather than redistributing a copy of the data in this repo
# (and outside Kaggle's own terms for the file itself), each user pulls it
# themselves via the official Kaggle API, authenticated with their own
# Kaggle account. data/olist/ is gitignored — nothing downloaded here is
# ever committed.
set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_DIR="$REPO_ROOT/data/olist"
ENV_FILE="$REPO_ROOT/ai-analytics/.env"
DATASET="olistbr/brazilian-ecommerce"

if ! command -v kaggle >/dev/null 2>&1; then
    echo "error: the 'kaggle' CLI is not installed." >&2
    echo "  Install it with: pip install kaggle" >&2
    exit 1
fi

# KAGGLE_USERNAME / KAGGLE_KEY live in ai-analytics/.env alongside the rest
# of this project's config, so there's one file to fill in rather than a
# separate ~/.kaggle/kaggle.json. Already-exported shell env vars take
# precedence over the .env file, same as python-dotenv's default elsewhere
# in this project.
_prior_kaggle_username="${KAGGLE_USERNAME:-}"
_prior_kaggle_key="${KAGGLE_KEY:-}"
if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi
[ -n "$_prior_kaggle_username" ] && KAGGLE_USERNAME="$_prior_kaggle_username"
[ -n "$_prior_kaggle_key" ] && KAGGLE_KEY="$_prior_kaggle_key"

if [ -z "${KAGGLE_USERNAME:-}" ] || [ -z "${KAGGLE_KEY:-}" ]; then
    cat >&2 <<EOF
error: Kaggle API credentials not found.

Get them at https://www.kaggle.com/settings -> API -> "Create New Token"
(copy the username and key shown), then set them in $ENV_FILE:

  KAGGLE_USERNAME=your-username
  KAGGLE_KEY=your-key

(or export them directly in your shell instead, if you prefer not to
store them in the .env file).
EOF
    exit 1
fi

mkdir -p "$TARGET_DIR"

echo "=== Downloading $DATASET from Kaggle into $TARGET_DIR ==="
kaggle datasets download --dataset "$DATASET" --path "$TARGET_DIR" --unzip --force

echo ""
echo "=== Done. Files: ==="
ls -la "$TARGET_DIR"/*.csv

cat <<'EOF'

Dataset: "Brazilian E-Commerce Public Dataset by Olist"
Source:  https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce
License: CC BY-NC-SA 4.0 (attribution, non-commercial, share-alike)

Next: run `docker compose up -d` from the repo root — ClickHouse will
load these CSVs into the `olist` database automatically on first start.
EOF
