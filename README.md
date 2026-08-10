# Local Roleplay Agent

This is a self-hosted roleplay/chat setup that runs entirely on your own hardware — local model, local memory, local everything. Nothing goes to a cloud API. You chat through SillyTavern, from this machine or from another device over Tailscale, and the system remembers things about you and your characters across conversations.

## The pieces

- **Ollama** runs the actual language model on your GPU. One model handles both the roleplay replies and the memory extraction.
- **Qdrant** is the vector database that memories actually live in.
- **mem0** is the memory engine sitting on top of Qdrant — it decides what's worth remembering from a conversation, stores it, and pulls relevant memories back out when they're needed.
- **SillyTavern** is the chat frontend you actually talk to. It's wired up to Ollama for generation and to mem0 through a custom plugin + extension pair that injects relevant memories into the prompt and sends new facts back after each exchange.

Everything runs in Docker on one machine. Only SillyTavern is exposed beyond localhost, and only over Tailscale, with basic auth and an IP whitelist on top for access control.

![Architecture diagram](docs/architecture.svg)

## What it can actually do right now

- Chat with a local (and uncensored) model, GPU-accelerated, no internet required
- Remember things about you that carry across every character you talk to (favorite foods, your job, whatever comes up) — a shared memory layer
- Also remember things specific to one character's relationship/history with you, kept separate from that shared layer
- Automatically sort which is which — a small classification pass decides if a new fact is general-purpose or specific to the character you were talking to
- Reach the whole thing from another device (phone, laptop) over Tailscale without exposing anything to the open internet
- Browse, hand-edit, delete, or bulk find-and-replace memories through a small web UI, without touching the database directly

## Setting it up

### Before you start

You'll need a Linux box with an NVIDIA GPU, [Tailscale](https://tailscale.com) set up on it (and on whatever device you want to reach it from), and native Docker Engine — not Docker Desktop. Docker Desktop on Linux runs everything inside its own VM, which just gets in the way of GPU passthrough for no benefit here.

If you don't have native Docker Engine yet:

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

**1. Bring your own model.** The model files aren't in this repo (they're multi-gigabytes). Drop one in `models/`, then point `models/Modelfile`'s `FROM` line at its filename. It should be a Gemma-family model, and it needs `num_ctx` set to at least 16384 — the memory extraction prompt alone is around 8,000 tokens, and a smaller context window will silently truncate it and break extraction. The model tested to work with both conversation and memory extraction is [this model](https://huggingface.co/HauhauCS/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive/blob/main/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q4_K_P.gguf)

**2. Set up SillyTavern's config.** Copy the template and fill in your own values:

```bash
cp sillytavern/config/config.yaml.example sillytavern/config/config.yaml
```

Then edit that file and set a real `basicAuthUser.username`/`password`, and add the actual Tailscale IPs of whatever devices should be able to reach it to the `whitelist` array (`tailscale status` will show you those IPs).

**3. Start Ollama and import your model.** This takes a few minutes for a multi-gigabyte file:

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

SillyTavern lives at `http://<tailscale-ip-of-host>:8000`, reachable from whatever devices you whitelisted. Everything else stays local to the host: the memory manager UI is at `http://localhost:8001/ui/`, the raw mem0 API docs at `http://localhost:8001/docs`, and the Qdrant dashboard at `http://localhost:6333/dashboard`.

For everything else — why things are built the way they are, decisions that got reversed along the way, and a running list of things that failed silently and cost time to track down — see [CLAUDE.md](CLAUDE.md).
