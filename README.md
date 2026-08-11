# Local SillyTavern with Memory Management

This is a self-hosted roleplay/chat setup that runs entirely on your own hardware — local model, local memory, local everything. Nothing goes to a cloud API. You chat through SillyTavern, from this machine or from another device over Tailscale, and the system remembers things about you and your characters across conversations.

## The pieces

- **Ollama** runs the actual language model on your GPU. One model handles both the roleplay replies and the memory extraction.
- **Qdrant** is the vector database that memories actually live in.
- **mem0** is the memory engine sitting on top of Qdrant — it decides what's worth remembering from a conversation, stores it, and pulls relevant memories back out when they're needed.
- **SillyTavern** is the chat frontend you actually talk to. It's wired up to Ollama for generation and to mem0 through a custom plugin + extension pair that injects relevant memories into the prompt and sends new facts back after each exchange.

Everything runs in Docker on one machine. Only SillyTavern is exposed beyond localhost — reachable locally, or from another device over Tailscale — with basic auth and an IP whitelist on top for access control.

<img src="docs/architecture.svg" alt="Architecture diagram" width="420">

## What it can actually do right now

- Chat with a local (and uncensored) model in SillyTavern, letting it role-play as different characters.
- Store memories about you locally:
  - Shared memory layer: facts about you (favorite foods, your job, whatever comes up)
  - Character-specific memory layer: things specific to one character's relationship/history with you
  - Automatically sort which is which and separately store the two layers
  - Browse, hand-edit, delete, or bulk find-and-replace memories through a small web UI, without touching the database directly
- Reach the whole thing from another device (phone, laptop) over Tailscale without exposing anything to the open internet

## Setting it up

### Before you start

You'll need a Linux box with an NVIDIA GPU, [Tailscale](https://tailscale.com) set up on it (and on whatever device you want to reach it from), and native Docker Engine — not Docker Desktop. The following commands walk through the set-up on a Ubuntu 22.04 computer. Ask a LLM to adjust them for your own hardware.

---

If you don't have native Docker Engine yet,

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker $USER
newgrp docker
```

And the NVIDIA Container Toolkit, so containers can actually see the GPU:

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

Quick sanity check that GPU passthrough actually works: `docker run --rm --gpus all nvidia/cuda:12.6.0-base-ubuntu22.04 nvidia-smi` should print your GPU info from inside the container.

### Getting the app running

**1. Bring your own model.** The model files aren't in this repo (they're multi-gigabytes). Drop one in `models/`, then point `models/Modelfile`'s `FROM` line at its filename. The model tested to work was [a quantized and abliterated Gemma 4 E4B model](https://huggingface.co/HauhauCS/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive/blob/main/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q4_K_P.gguf), which fits in the 6GB VRAM of a NVIDIA GTX 1660Ti card. The `num_ctx` in `Modelfile` needs to be at least 16384 — the memory extraction prompt alone is around 8,000 tokens, and a smaller context window will silently truncate it and break extraction.

If you ever want to switch to a different model, run `./scripts/test-model.sh <model-name>` first — it checks text generation, VRAM footprint vs. Ollama's own estimate, and whether the model breaks mem0's memory extraction before you commit to it.

**2. Set up SillyTavern's config.** Copy the template and fill in your own values:

```bash
cp sillytavern/config/config.yaml.example sillytavern/config/config.yaml
```

Then edit that file and set a real `basicAuthUser.username`/`password`, and add the actual Tailscale IPs of whatever devices should be able to reach it to the `whitelist` array (`tailscale status` will show you those IPs).

**3. Start Ollama and import your model.** This takes a few minutes for a multi-gigabyte file. The last line pulls `nomic-embed-text`, the embedding model mem0 uses to turn memories into vectors for Qdrant — it comes straight from Ollama's library, no manual download needed:

```bash
docker compose up -d ollama
docker exec -w /import ollama ollama create gemma4-e4b-hauhaucs -f Modelfile
docker exec ollama ollama pull nomic-embed-text
```

**4. Bring up everything else:**

```bash
docker compose up -d
```

**5. Point SillyTavern at Ollama.** In the SillyTavern UI, go to API Connections, set API type to Text Completion and source to Ollama, Server URL to `http://ollama:11434`, then pick your model from the dropdown. It may take a few minutes for the model to show up in the dropdown menu.

**6. Turn on the memory extension.** In SillyTavern, go to Manage Extensions tab on the top band and enable "Roleplay Memory" — that's what actually wires the chat up to mem0.

### Where things are once it's running

SillyTavern lives at `http://localhost:8000` on the local machine and can be accessed remotely at `http://<tailscale-ip-of-host>:8000` via a tailscale connection from whatever devices you whitelisted. The memory manager UI is local only at `http://localhost:8001/ui/`.

The raw mem0 API docs are at `http://localhost:8001/docs`, and the Qdrant dashboard is at `http://localhost:6333/dashboard`. These two are for debugging only, you will unlikely need to access them.

There's also an optional `docker-compose.prod.yml` overlay for running a second, fully isolated instance to separate development and actual use data — see [CLAUDE.md](CLAUDE.md) if you want to set one up. Once it exists, `make dev-up`/`make prod-up` (and `-down`) start and stop each stack without needing to remember the underlying `docker compose` commands — run bare `make` to see all of them.

## Third-party components

This repo's own code is Apache-2.0 (see [LICENSE](LICENSE)), but it wires together several separately-licensed pieces you should know about if you're redistributing or building on this:

| Component | License |
| --- | --- |
| [SillyTavern](https://github.com/SillyTavern/SillyTavern) | AGPL-3.0 |
| [Ollama](https://github.com/ollama/ollama) | MIT |
| [Qdrant](https://github.com/qdrant/qdrant) | Apache-2.0 |
| [mem0](https://github.com/mem0ai/mem0) | Apache-2.0 |
| [nomic-embed-text](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5) | Apache-2.0 |
| Gemma models (base and fine-tunes) | [Gemma Terms of Use](https://ai.google.dev/gemma/terms) — a custom license with a prohibited-use policy, not a standard OSI license |

None of these are vendored into this repo — SillyTavern, Ollama, and Qdrant run as their own upstream Docker images, and the model GGUF is something you bring yourself (see step 1 above). SillyTavern's AGPL-3.0 doesn't extend to the server plugin or client extension in this repo, since they're separate code loaded through SillyTavern's public plugin/extension API, not a modified copy of SillyTavern itself.
