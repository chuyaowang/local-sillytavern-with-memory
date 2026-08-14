# Architecture

```mermaid
---
config:
  look: handDrawn
  theme: neutral
  themeVariables:
    fontFamily: Comic Neue
  layout: elk
  elk:
    nodePlacementStrategy: BRANDES_KOEPF
---
graph TD
    subgraph remote_machine["<b>Remote machine</b>"]
        remote["<b>Browser</b>"]
    end

    subgraph local_machine["<b>Local machine</b>"]
        direction LR
        host["<b>Admin Page</b>"]
        local["<b>Browser"]
        
        subgraph docker["<b>Docker network</b>"]
            direction LR
            editor["<b>Memory Editor</b>"]
            st["<b>SillyTavern</b><br/>chat frontend"]
            llamacpp["<b>llama.cpp</b><br/>LLM inference"]

            subgraph memory["<b>Agent memory</b>"]
            direction LR
                mem0["<b>mem0</b><br/>memory management"]
                qdrant["<b>Qdrant</b><br/>memory storage/query"]
            end
        end
    end

    remote -- Remote Access --> st
    host -- User Interface --> editor
    local -- Local Access --> st
    editor -- Management --> memory

    llamacpp -- Text Generation --> st
    st -- Chat History --> memory
    llamacpp -- Memory Extraction --> memory
    mem0 -- Memory Store --> qdrant
```

![Architecture diagram](architecture.svg)

The source above is kept in sync with `architecture.mmd`, and both rely on
`architecture-font.css` (see its header comment): the actual Comic Neue font
bytes are embedded there directly, so the diagram renders correctly for
anyone opening it regardless of whether they have that font installed, plus
a fix for mermaid underestimating this font's label width. Regenerate the
SVG with:

```sh
./scripts/render-architecture-diagram.sh
```

That's just `mmdc -i docs/architecture.mmd -o docs/architecture.svg -C
docs/architecture-font.css` -- mmdc embeds `-C` content directly into the
saved SVG's own `<style>`, so the file is self-contained and the script
mainly exists so the exact command doesn't need re-deriving each time.

- **SillyTavern** — the chat UI. Only component exposed beyond `127.0.0.1`, reachable over Tailscale on `:8000`.
- **llama.cpp** — runs the local model (one model for both roleplay replies and memory extraction) plus the embedding model.
- **mem0-service** — decides what's worth remembering, sorts it into shared-vs-per-character, stores/retrieves it, and serves a small web UI for managing memories by hand.
- **Qdrant** — where the memory vectors actually live.

Everything except SillyTavern stays on `127.0.0.1` — only reachable from the host itself, never from the Tailscale network directly.
