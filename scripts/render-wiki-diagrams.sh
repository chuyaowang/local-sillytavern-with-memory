#!/usr/bin/env bash
set -euo pipefail

# Renders wiki diagrams to SVG, reusing the exact same hand-drawn Comic Neue
# preset as docs/architecture/architecture.mmd (see
# render-architecture-diagram.sh) -- each diagram already carries its own
# `config:` frontmatter (diagram look/layout), matching that file's
# structure; -C below is the separate font-embedding mechanism, not that
# config block. Two modes:
#   - Given a markdown page: extracts every ```mermaid fenced block and
#     renders each to docs/wiki/diagrams/<prefix>-N.svg.
#   - Given a single .mmd file directly: renders just that file to its
#     matching .svg, skipping extraction entirely -- for a diagram whose
#     source page has since replaced its mermaid fence with an <img> tag
#     pointing at the rendered SVG, leaving the .mmd as a standalone source
#     of truth from then on with nothing left to extract it from.
#
# Needs `mmdc` (@mermaid-js/mermaid-cli) on PATH: `npm install -g @mermaid-js/mermaid-cli`.
#
# Usage:
#   ./scripts/render-wiki-diagrams.sh [source.md] [output-prefix]
#   ./scripts/render-wiki-diagrams.sh path/to/diagram.mmd

cd "$(dirname "${BASH_SOURCE[0]}")/.."

SOURCE="${1:-docs/wiki/Memory-System.md}"
OUT_DIR="docs/wiki/diagrams"
FONT_CSS="docs/architecture/architecture-font.css"

if [[ "$SOURCE" == *.mmd ]]; then
  svg="${SOURCE%.mmd}.svg"
  mmdc -i "$SOURCE" -o "$svg" -C "$FONT_CSS"
  echo "Rendered $svg"
  exit 0
fi

PREFIX="${2:-$(basename "$SOURCE" .md | tr '[:upper:]' '[:lower:]')}"

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