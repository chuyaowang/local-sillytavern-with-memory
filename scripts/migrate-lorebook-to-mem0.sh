#!/usr/bin/env bash
set -euo pipefail

# One-time backfill: reads an existing SillyTavern World Info JSON file's
# entries[*].content and POSTs each to mem0-service's low-level
# POST /worlds/{world}/memories, so pre-existing lore isn't lost when that
# file's entries get emptied out in favor of mem0-based lore (see
# CLAUDE.md, "Memory scoping" -- world lore layer).
#
# Usage: ./scripts/migrate-lorebook-to-mem0.sh <world-json-file> <world-name> [mem0-url]
#
#   <world-json-file>  Path to the ST World Info JSON (e.g.
#                       sillytavern/data/default-user/worlds/Eldoria.json)
#   <world-name>        The world's agent_id, e.g. "eldoria" -- gets
#                       slugified the same way the ST extension/mem0-service
#                       slugify a world name, so use the plain name and let
#                       this script lowercase it, not a pre-slugified one.
#   [mem0-url]          Defaults to http://127.0.0.1:8001 (dev). Pass
#                       http://127.0.0.1:8011 for prod.

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <world-json-file> <world-name> [mem0-url]" >&2
  exit 1
fi

WORLD_JSON="$1"
WORLD_NAME_RAW="$2"
MEM0_URL="${3:-http://127.0.0.1:8001}"

for cmd in curl jq; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing required command: $cmd" >&2
    exit 1
  fi
done

if [[ ! -f "$WORLD_JSON" ]]; then
  echo "File not found: $WORLD_JSON" >&2
  exit 1
fi

# Matches mem0-service/main.py's _slugify() / the ST extension's slugify().
WORLD_NAME=$(echo "$WORLD_NAME_RAW" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/-/g; s/^-+|-+$//g')

if [[ -z "$WORLD_NAME" ]]; then
  echo "World name slugified to empty string: '${WORLD_NAME_RAW}'" >&2
  exit 1
fi

# -c keeps each entry's content as one JSON-encoded (still-escaped) string
# per line, so multi-line RP content doesn't break the line-based loop
# below -- decoded back to raw text per-line with `jq -r '.'` before use.
encoded_contents=$(jq -c '.entries[] | .content' "$WORLD_JSON")

if [[ -z "$encoded_contents" ]]; then
  echo "No entries with content found in $WORLD_JSON -- nothing to migrate." >&2
  exit 0
fi

count=0
while IFS= read -r encoded; do
  [[ -z "$encoded" ]] && continue
  content=$(jq -r '.' <<< "$encoded")
  [[ -z "$content" ]] && continue
  payload=$(jq -n --arg content "$content" '{content: $content}')
  if curl -sf -m 30 -X POST "${MEM0_URL}/worlds/${WORLD_NAME}/memories" \
      -H "Content-Type: application/json" -d "$payload" >/dev/null; then
    count=$((count + 1))
  else
    echo "Failed to migrate one entry (see content below):" >&2
    echo "$content" >&2
  fi
done <<< "$encoded_contents"

echo "Migrated ${count} entries from ${WORLD_JSON} into world '${WORLD_NAME}' at ${MEM0_URL}."