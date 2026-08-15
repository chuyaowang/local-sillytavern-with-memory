# Local SillyTavern with Memory Management

A self-hosted roleplay/chat setup that runs entirely on your own hardware: local model, local memory, local everything. Nothing goes to a cloud API. You chat through SillyTavern, from this machine or another device over Tailscale, and the system remembers things about you, your characters, and your worlds across conversations.

## The pieces

- **llama.cpp** runs the language model on your GPU. One model handles both roleplay replies and memory extraction.
- **Qdrant** is the vector database that memories live in.
- **mem0** is the memory engine on top of Qdrant. It decides what's worth remembering from a conversation, stores it, and pulls relevant memories back out when they're needed.
- **SillyTavern** is the chat frontend, wired up to llama.cpp for generation and to mem0 through a custom plugin and extension.

Everything runs in Docker on one machine. Only SillyTavern is exposed beyond localhost, reachable locally or from another device over Tailscale, with basic auth and an IP whitelist on top.

<p align="center">
  <img src="docs/architecture/architecture.svg" alt="Architecture diagram" width="420">
</p>

## What it can actually do right now

- Chat with a local, uncensored model in SillyTavern, letting it role-play as different characters.
- Remember facts about you, per-character relationships, and lore about fictional worlds, sorted into the right layer automatically.
- Browse, hand-edit, delete, or bulk find-and-replace memories through a small web UI.
- Reach the whole thing from another device over Tailscale, without exposing anything to the open internet.

See the [wiki](https://github.com/chuyaowang/local-sillytavern-with-memory/wiki) for how the memory system actually works.

## Quick start

1. Read [Prerequisites](https://github.com/chuyaowang/local-sillytavern-with-memory/wiki/Prerequisites): a Linux computer with an NVIDIA GPU, Docker Engine, and (optionally) Tailscale.
2. Follow [Running the Stack](https://github.com/chuyaowang/local-sillytavern-with-memory/wiki/Running-the-Stack) to clone the repo, add a model, and bring everything up:

   ```bash
   git clone https://github.com/chuyaowang/local-sillytavern-with-memory.git
   cd local-sillytavern-with-memory
   docker compose up -d
   ```

That's the short version. The wiki page covers the model file, SillyTavern config, and connecting everything together in full.

## Wiki

| Page | What's in it |
| --- | --- |
| [Prerequisites](https://github.com/chuyaowang/local-sillytavern-with-memory/wiki/Prerequisites) | Host setup: Docker Engine, NVIDIA Container Toolkit, Tailscale. |
| [Running the Stack](https://github.com/chuyaowang/local-sillytavern-with-memory/wiki/Running-the-Stack) | Getting this repo running, plus a separate dev/prod split. |
| [Changing the Model](https://github.com/chuyaowang/local-sillytavern-with-memory/wiki/Changing-the-Model) | Swapping the model and testing it. |
| [Memory System](https://github.com/chuyaowang/local-sillytavern-with-memory/wiki/Memory-System) | How the three memory scopes, extraction, storage, and world lore work. |
| [SillyTavern Troubleshooting](https://github.com/chuyaowang/local-sillytavern-with-memory/wiki/SillyTavern-Troubleshooting) | Settings and gotchas specific to this project. |
| [Third-Party Components](https://github.com/chuyaowang/local-sillytavern-with-memory/wiki/Third-Party-Components) | Licenses for everything this project wires together. |

## License

This repo's own code is Apache-2.0 (see [LICENSE](LICENSE)). It wires together several separately-licensed components: SillyTavern (AGPL-3.0), llama.cpp (MIT), Qdrant and mem0 (Apache-2.0), and whichever model you bring yourself. See [Third-Party Components](https://github.com/chuyaowang/local-sillytavern-with-memory/wiki/Third-Party-Components) for the full list.
