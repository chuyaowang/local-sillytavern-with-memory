# Local SillyTavern with Memory — Wiki

A self-hosted roleplay/chat setup: local LLM inference (llama.cpp), a persistent multi-layered memory system (mem0 + Qdrant), and SillyTavern as the chat frontend, all wired together to run entirely on your own hardware.

This wiki is the detailed reference. The [README](https://github.com/chuyaowang/local-sillytavern-with-memory#readme) is the short version for anyone just browsing the repo.

## Pages

| Page | What's in it |
| --- | --- |
| [Prerequisites](Prerequisites.md) | One-time host setup: Docker Engine, NVIDIA Container Toolkit, Tailscale. |
| [Running the Stack](Running-the-Stack.md) | Getting this repo running: cloning it, the model files, SillyTavern config, bringing the stack up, and setting up a separate dev/prod split. |
| [Changing the Model](Changing-the-Model.md) | Swapping the model, what the test script checks, and models verified so far. |
| [Memory System](Memory-System.md) | The three memory scopes, how facts get remembered and sorted, storage, the embedding model, the entity store, world lore, and how it all fits together. |
| [SillyTavern Troubleshooting](SillyTavern-Troubleshooting.md) | Settings and gotchas specific to this project, and why they're needed. |
| [Third-Party Components](Third-Party-Components.md) | Licenses for everything this project wires together. |

## Source of truth

These pages are written and version-controlled as plain markdown under `docs/wiki/` in the main repo. A workflow (`.github/workflows/publish-wiki.yml`) syncs them here automatically on every push to `main`. Edit the files there — edits made directly on this wiki page get overwritten on the next sync.
