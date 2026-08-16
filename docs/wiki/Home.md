# Local SillyTavern with Memory — Wiki

A self-hosted, backend-agnostic memory system for SillyTavern (mem0 + Qdrant), wired in through a plugin and extension so it works with whichever chat backend SillyTavern is already connected to. Also bundles a complete local chat stack (llama.cpp and SillyTavern) as an optional, zero-cloud-dependency way to run the whole thing.

This wiki is the detailed reference. The [README](https://github.com/chuyaowang/local-sillytavern-with-memory#readme) is the short version for anyone just browsing the repo.

## Pages

| Page | What's in it |
| --- | --- |
| [Prerequisites](Prerequisites.md) | One-time host setup the memory system needs: Docker Engine, NVIDIA Container Toolkit. |
| [Installing the Memory System](Installing-the-Memory-System.md) | Adding this to an existing SillyTavern, plus a separate dev/prod split. |
| [Configuring the Memory System](Configuring-the-Memory-System.md) | Activating the extension, binding a world, and what triggers automatic extraction. |
| [Managing Memories](Managing-Memories.md) | The three memory scopes, building world lore, and browsing/editing/moving/deleting memories through the admin UI. |
| [Memory System](Memory-System.md) | How facts get remembered and sorted automatically, storage, the embedding model, the entity store, and how it all fits together. |
| [Local-Only Setup](Local-Only-Setup.md) | An optional, fully-local alternative: the bundled llama.cpp model and SillyTavern, wired up together. |
| [Changing the Model](Changing-the-Model.md) | Swapping the local model, what the test script checks, and models verified so far. |
| [Remote Access](Remote-Access.md) | Reaching the bundled SillyTavern from another device over Tailscale. |
| [SillyTavern Troubleshooting](SillyTavern-Troubleshooting.md) | Settings and gotchas specific to this project, and why they're needed. |
| [Benchmarking](Benchmarking.md) | Why llama.cpp over Ollama and which embedding model to pick, backed by real numbers. |
| [Third-Party Components](Third-Party-Components.md) | Licenses for everything this project wires together. |

## Source of truth

These pages are written and version-controlled as plain markdown under `docs/wiki/` in the main repo. A workflow (`.github/workflows/publish-wiki.yml`) syncs them here automatically on every push to `main`. Edit the files there — edits made directly on this wiki page get overwritten on the next sync.
