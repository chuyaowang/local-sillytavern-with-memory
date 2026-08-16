# AutoMemory for SillyTavern — Documentation

AutoMemory is a self-hosted, backend-agnostic memory system for SillyTavern (mem0 + Qdrant), wired in through a plugin and extension to work with whichever chat backend SillyTavern is already connected to. Also bundles a complete local chat stack (llama.cpp and SillyTavern) as an optional, zero-cloud-dependency to chat and store memory.

This wiki is the detailed reference. See the [README](https://github.com/chuyaowang/local-sillytavern-with-memory#readme) for a quick idea about this repo.

## Pages

| Page | What's in it |
| --- | --- |
| [Prerequisites](Prerequisites.md) | One-time host setup the memory system needs: Docker Engine, NVIDIA Container Toolkit. |
| [Installing the Memory System](Installing-the-Memory-System.md) | Adding this to an existing SillyTavern, plus a separate dev/prod split. |
| [Configuring the Memory System](Configuring-the-Memory-System.md) | Activating the extension, binding a world, and what triggers automatic extraction. |
| [Managing Memories](Managing-Memories.md) | The three memory scopes, building world lore, and browsing/editing/moving/deleting memories through the admin UI. |
| [Memory System Design](Memory-System.md) | How facts get remembered and sorted automatically, storage, the embedding model, the entity store, and how it all fits together. |
| [Local-Only Setup](Local-Only-Setup.md) | An optional, fully-local alternative: the bundled llama.cpp model and SillyTavern, wired up together. |
| [Changing the Local Model](Changing-the-Model.md) | Swapping the local model, what the test script checks, and models verified so far. |
| [Remote Access](Remote-Access.md) | Reaching the bundled SillyTavern from another device over Tailscale. |
| [SillyTavern Troubleshooting](SillyTavern-Troubleshooting.md) | Settings and gotchas specific to this project, and why they're needed. |
| [Benchmarking](Benchmarking.md) | Why llama.cpp over Ollama and which embedding model to pick, backed by real numbers. |
| [Third-Party Components](Third-Party-Components.md) | Licenses for everything this project wires together. |

## Source of truth

These pages are written and version-controlled as plain markdown under `docs/wiki/` in the main repo. A workflow syncs them here automatically on every push to `main`. Edits made directly on this wiki page get overwritten on the next sync.
