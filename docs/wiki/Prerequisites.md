# Prerequisites

This page is the one-time host setup needed before the memory system itself will run — nothing here is specific to this project. See [Installing the Memory System](Installing-the-Memory-System.md) for the project-specific steps once these are done. [Remote Access](Remote-Access.md) has its own further prerequisite (Tailscale), needed only if reaching this from another device.

## What you need

- A Linux computer with an NVIDIA GPU.
- Native Docker Engine, installed via apt below. Docker Desktop for Linux runs everything inside an internal VM, which adds an indirection layer GPU passthrough doesn't need.

The commands below target Ubuntu 22.04. Ask an LLM to adapt them for your own distribution.

Windows isn't directly supported, but should work through WSL2: install Docker Engine inside a WSL2 Ubuntu distro, keep the repo on the WSL2 filesystem (`/mnt/c/...` paths won't work), and set up GPU passthrough via [NVIDIA's CUDA on WSL support](https://docs.nvidia.com/cuda/wsl-user-guide/index.html) before installing the NVIDIA Container Toolkit below. This project hasn't tested it — expect to adapt some steps.

## Install Docker Engine

Skip this if you already have it:

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

## Install the NVIDIA Container Toolkit

So containers can actually see the GPU:

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

## Sanity-check GPU passthrough

```bash
docker run --rm --gpus all nvidia/cuda:12.6.0-base-ubuntu22.04 nvidia-smi
```

This should print your GPU info from inside the container. Fix that before going any further if it doesn't — nothing downstream will work without it.
