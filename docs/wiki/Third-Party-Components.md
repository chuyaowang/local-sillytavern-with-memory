# Third-Party Components

This repo's own code is Apache-2.0 (see [LICENSE](https://github.com/chuyaowang/local-sillytavern-with-memory/blob/main/LICENSE)), but it wires together several separately-licensed pieces worth knowing about if you're redistributing or building on this.

| Component | License |
| --- | --- |
| [SillyTavern](https://github.com/SillyTavern/SillyTavern) | AGPL-3.0 |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) | MIT |
| [Qdrant](https://github.com/qdrant/qdrant) | Apache-2.0 |
| [mem0](https://github.com/mem0ai/mem0) | Apache-2.0 |
| [nomic-embed-text](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5) | Apache-2.0 |
| Gemma models (base and fine-tunes) | [Gemma Terms of Use](https://ai.google.dev/gemma/terms), a custom license with a prohibited-use policy, not a standard OSI license |

SillyTavern, llama.cpp, and Qdrant run as their own upstream Docker images, and the model file is something you bring yourself (see [Local-Only Setup](Local-Only-Setup.md)) — none of these are vendored into this repo. SillyTavern's AGPL-3.0 covers SillyTavern itself; the server plugin and client extension in this repo are separate code loaded through SillyTavern's public plugin/extension API, and carry this repo's own Apache-2.0 license.
