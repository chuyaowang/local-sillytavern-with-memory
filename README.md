# AutoMemory for SillyTavern

AutoMemory is a self-hosted memory system for SillyTavern. From your conversations, it automatically decides what is worth remembering and what facts to bring back without a need for manual curation of memories or setting injection rules.

## Features

- **Three memory scopes** — a **character** memory is private to one relationship between a persona and a character, a **shared** memory follows a person across every character they talk to, and **world lore** belongs to a fictional world itself, retrieved whenever a conversation happens in that world.
- **Automatic extraction and injection** — memories get pulled out of a conversation and woven back into later replies on their own. Retrieval happens via vector similarity search, improving accuracy and relevance over keyword-based approaches.
- **A management UI** — browse, hand-edit, move to a different scope, or delete any memory by hand, plus dedicated tools for building or migrating a whole world's lore.

See the [wiki](https://github.com/chuyaowang/local-sillytavern-with-memory/wiki) for exactly how all of this works.

## Example

<p align="center">
  <img src="docs/wiki/diagrams/memory-system-1.svg" alt="Which scopes are visible in each conversation" width="600">
</p>

Nova (user), in a conversation with Seraphina (character) in the Eldoria world, has access to memories about herself in Eldoria, her interaction with Seraphina in Eldoria, and the world Eldoria. Similarly, another user Amber's interactions with another character Arthuria are memorized for the Camelot world.

When Nova talks to Cynthia, another character, in the Eldoria world, their conversation does not have access to the Seraphina memory.

When Amber talks to Seraphina in the Eldoria world, their conversation only has access to the Eldoria world memory.

## Setup

Already have SillyTavern running, with a chat connection configured? Follow [Installing the Memory System](https://github.com/chuyaowang/local-sillytavern-with-memory/wiki/Installing-the-Memory-System) to add this to it — nothing about how it generates replies needs to change.

Starting from nothing, or wanting a fully local setup with zero cloud dependency instead? [Local-Only Setup](https://github.com/chuyaowang/local-sillytavern-with-memory/wiki/Local-Only-Setup) bundles a complete stack: a local model and SillyTavern itself, wired up together.

<p align="center">
  <img src="docs/architecture/architecture.svg" alt="Architecture diagram" width="420">
</p>

Either way, [Prerequisites](https://github.com/chuyaowang/local-sillytavern-with-memory/wiki/Prerequisites) covers the one-time host setup needed first.

## Limitations and future development

- Only English memories are supported now, but can be changed easily by using a multi-lingual embedding model. See [Memory System](https://github.com/chuyaowang/local-sillytavern-with-memory/wiki/Memory-System).
- Support for Group chats has not been developed but is planned.
- Designed originally for deployment on a local device with limited 6 GB VRAM, so memory extraction and conversation use the same model.

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
