#!/usr/bin/env bash
set -euo pipefail

# Regenerates docs/architecture.svg from docs/architecture.mmd.
#
# Needs `mmdc` (@mermaid-js/mermaid-cli) on PATH: `npm install -g @mermaid-js/mermaid-cli`.
#
# docs/architecture-font.css (passed via -C) carries two fixes, both
# explained in its own header comment: a single-word @font-face alias
# (Chromium/Skia fails to resolve multi-word font names inside SVG
# <foreignObject>, which is how mermaid renders node labels) and an
# `overflow: visible` override (mermaid's built-in width estimate for
# HTML labels undershoots this font's actual metrics, and foreignObject
# clips to that estimate by default). mmdc embeds -C content directly into
# the saved SVG's own <style>, so the file is self-contained -- no
# post-processing needed.

cd "$(dirname "${BASH_SOURCE[0]}")/.."

mmdc -i docs/architecture.mmd -o docs/architecture.svg -C docs/architecture-font.css

echo "Regenerated docs/architecture.svg."
