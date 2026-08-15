#!/usr/bin/env bash
set -euo pipefail

# Extracts every ```mermaid fenced block from a wiki page and renders each to
# an SVG in docs/wiki/diagrams/, reusing the exact same hand-drawn Comic Neue
# preset as docs/architecture/architecture.mmd (see
# render-architecture-diagram.sh) -- each block already carries its own
# `config:` frontmatter (diagram look/layout), matching that file's
# structure; -C below is the separate font-embedding mechanism, not that
# config block.
#
# Needs `mmdc` (@mermaid-js/mermaid-cli) on PATH: `npm install -g @mermaid-js/mermaid-cli`.
#
# Usage: ./scripts/render-wiki-diagrams.sh [source.md] [output-prefix]

cd "$(dirname "${BASH_SOURCE[0]}")/.."

SOURCE="${1:-docs/wiki/Memory-System.md}"
OUT_DIR="docs/wiki/diagrams"
PREFIX="${2:-$(basename "$SOURCE" .md | tr '[:upper:]' '[:lower:]')}"
FONT_CSS="docs/architecture/architecture-font.css"

mkdir -p "$OUT_DIR"
rm -f "$OUT_DIR/$PREFIX"-*.mmd "$OUT_DIR/$PREFIX"-*.svg

awk -v prefix="$PREFIX" -v outdir="$OUT_DIR" '
  /^```mermaid$/ { in_block=1; n++; fname=outdir "/" prefix "-" n ".mmd"; next }
  /^```$/ { if (in_block) { in_block=0; close(fname) } next }
  in_block { print > fname }
' "$SOURCE"

for mmd in "$OUT_DIR/$PREFIX"-*.mmd; do
  [ -e "$mmd" ] || continue
  svg="${mmd%.mmd}.svg"
  mmdc -i "$mmd" -o "$svg" -C "$FONT_CSS"
  echo "Rendered $svg"
done