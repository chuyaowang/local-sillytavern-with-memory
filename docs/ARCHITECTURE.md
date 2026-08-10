# Architecture

```mermaid
graph TD
    %%{init: {"flowchart": {"defaultRenderer": "elk"}} }%%
    subgraph remote_machine["<b>Remote machine</b>"]
        remote["<b>Browser</b>"]
    end

    subgraph local_machine["<b>Local machine</b>"]
        direction LR
        host["<b>Admin Page</b>"]
        
        subgraph docker["<b>Docker network</b>"]
            direction LR
            editor["<b>Memory Editor</b>"]
            st["<b>SillyTavern</b><br/>chat frontend"]
            ollama["<b>Ollama</b><br/>LLM inference"]

            subgraph memory["<b>Agent memory</b>"]
            direction LR
                mem0["<b>mem0</b><br/>memory management"]
                qdrant["<b>Qdrant</b><br/>memory storage/query"]
            end
        end
    end

    remote -- Remote Access --> st
    host -- Local Access --> editor
    editor -- Management --> memory

    ollama -- Text Generation --> st
    st -- Chat History --> memory
    ollama -- Memory Extraction --> memory
    mem0 -- Memory Store --> qdrant
```

![Architecture diagram](architecture.svg)

- **SillyTavern** — the chat UI. Only component exposed beyond `127.0.0.1`, reachable over Tailscale on `:8000`.
- **Ollama** — runs the local model (one model for both roleplay replies and memory extraction) plus the embedding model.
- **mem0-service** — decides what's worth remembering, sorts it into shared-vs-per-character, stores/retrieves it, and serves a small web UI for managing memories by hand.
- **Qdrant** — where the memory vectors actually live.

Everything except SillyTavern stays on `127.0.0.1` — only reachable from the host itself, never from the Tailscale network directly.
