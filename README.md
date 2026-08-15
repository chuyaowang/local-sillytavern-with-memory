# Local SillyTavern with Memory Management

A self-hosted memory system for SillyTavern: it remembers facts about you, your characters, and your fictional worlds across conversations, sorted automatically into the right scope. It works with whichever backend SillyTavern is already connected to — a cloud API, an aggregator like OpenRouter, or a local model — so there is nothing extra to configure just for memory. This repo also bundles a complete local chat stack as the easiest way to try the whole thing with zero cloud dependency, but that bundled stack is not required — point SillyTavern at a different backend instead and the memory system works the same way.

## The pieces

- **mem0 on Qdrant** is the memory engine, and the actual point of this project — it decides what is worth remembering from a conversation, stores it, and pulls relevant memories back out when they are needed.
- **SillyTavern**, wired up to mem0 through a plugin and extension, is the chat frontend. Memory extraction runs through whatever connection SillyTavern already has active, so there is no separate model to configure just for memory.
- **llama.cpp**, bundled and optional, runs a local model on your own GPU. It handles chat generation if nothing else is connected, and always serves the memory system's own embedding model, so the whole thing works with zero cloud dependency out of the box if that is what is wanted.

The memory services here — Qdrant, mem0, and llama.cpp — run in Docker, bound to localhost only. The bundled SillyTavern, if used, is the one piece exposed beyond localhost, reachable locally or from another device over Tailscale, with basic auth and an IP whitelist on top.

<p align="center">
  <img src="docs/architecture/architecture.svg" alt="Architecture diagram" width="420">
</p>

## What it can actually do right now

- Chat in SillyTavern with whatever backend is already connected, cloud or local, while remembering facts about you, per-character relationships, and lore about fictional worlds, sorted into the right layer automatically.
- Try the whole thing with zero cloud dependency using the bundled local stack, including an uncensored local model by default.
- Browse, hand-edit, delete, or bulk find-and-replace memories through a small web UI.
- Reach the whole thing from another device over Tailscale, without exposing anything to the open internet.

See the [wiki](https://github.com/chuyaowang/local-sillytavern-with-memory/wiki) for how the memory system actually works.

## Quick start

1. Read [Prerequisites](https://github.com/chuyaowang/local-sillytavern-with-memory/wiki/Prerequisites): a Linux computer with an NVIDIA GPU and Docker Engine.
2. Follow [Installing the Memory System](https://github.com/chuyaowang/local-sillytavern-with-memory/wiki/Installing-the-Memory-System) to add this to a SillyTavern that is already running, with its own chat connection already configured — clone the repo, get the embedding model, and bring up the memory services:

   ```bash
   git clone https://github.com/chuyaowang/local-sillytavern-with-memory.git
   cd local-sillytavern-with-memory
   docker compose up -d qdrant mem0 llama-cpp
   ```

No existing SillyTavern, or a fully local setup with zero cloud dependency is wanted instead? [Local-Only Setup](https://github.com/chuyaowang/local-sillytavern-with-memory/wiki/Local-Only-Setup) covers that as a further, optional step.

## Wiki

| Page | What's in it |
| --- | --- |
| [Prerequisites](https://github.com/chuyaowang/local-sillytavern-with-memory/wiki/Prerequisites) | Host setup the memory system needs: Docker Engine, NVIDIA Container Toolkit. |
| [Installing the Memory System](https://github.com/chuyaowang/local-sillytavern-with-memory/wiki/Installing-the-Memory-System) | Adding this to an existing SillyTavern, plus a separate dev/prod split. |
| [Configuring the Memory System](https://github.com/chuyaowang/local-sillytavern-with-memory/wiki/Configuring-the-Memory-System) | Activating the extension, binding a world, and what triggers automatic extraction. |
| [Managing Memories](https://github.com/chuyaowang/local-sillytavern-with-memory/wiki/Managing-Memories) | The three memory scopes, building world lore, and browsing/editing/moving/deleting memories through the admin UI. |
| [Memory System](https://github.com/chuyaowang/local-sillytavern-with-memory/wiki/Memory-System) | How facts get remembered and sorted automatically, behind the scenes. |
| [Local-Only Setup](https://github.com/chuyaowang/local-sillytavern-with-memory/wiki/Local-Only-Setup) | An optional, fully-local alternative: the bundled llama.cpp model and SillyTavern, wired up together. |
| [Changing the Model](https://github.com/chuyaowang/local-sillytavern-with-memory/wiki/Changing-the-Model) | Swapping the local model and testing it. |
| [Remote Access](https://github.com/chuyaowang/local-sillytavern-with-memory/wiki/Remote-Access) | Reaching the bundled SillyTavern from another device over Tailscale. |
| [SillyTavern Troubleshooting](https://github.com/chuyaowang/local-sillytavern-with-memory/wiki/SillyTavern-Troubleshooting) | Settings and gotchas specific to this project. |
| [Third-Party Components](https://github.com/chuyaowang/local-sillytavern-with-memory/wiki/Third-Party-Components) | Licenses for everything this project wires together. |

## License

This repo's own code is Apache-2.0 (see [LICENSE](LICENSE)). It wires together several separately-licensed components: SillyTavern (AGPL-3.0), llama.cpp (MIT), Qdrant and mem0 (Apache-2.0), and whichever model you bring yourself. See [Third-Party Components](https://github.com/chuyaowang/local-sillytavern-with-memory/wiki/Third-Party-Components) for the full list.
