#!/usr/bin/env python3
"""Benchmarks candidate embedding models against the one this project actually
uses (see docs/wiki/Memory-System.md's "Storage" section for the multilingual
alternatives this compares against nomic-embed-text-v1.5).

For each model, loads it standalone via a throwaway `llama-cpp:server-cuda13`
container (GPU, one model at a time -- the 6GB card can't hold more than one
of these plus the roleplay model, so this script assumes the real `llama-cpp`
container is stopped for the duration of the run), then reports:
  - VRAM footprint (idle-loaded, delta from pre-launch baseline)
  - max tokens accepted (the model's trained context length, read directly
    from GGUF metadata -- not the --ctx-size flag, which can be set higher or
    lower than what the model was actually trained on)
  - embedding dimensions (read from a real embedding response)
  - cosine similarity, per test category, against scripts/embedding-bench-cases.json
    (memory-to-memory: base fact vs. each variant, no query involved)
  - the same, but query-to-memory: an in-conversation roleplay line vs. the
    base fact and each variant, mirroring how mem0's search() is actually
    used at retrieval time (embedding a conversation turn, not one memory
    text, to find related stored memories)

Requires `docker`, GPU passthrough already configured (see CLAUDE.md's
"Architecture" section), and the models already downloaded to models/.

Usage:
  ./scripts/bench-embedding-models.py
"""
import json
import math
import struct
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from statistics import mean, stdev

REPO_ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = REPO_ROOT / "models"
CASES_FILE = REPO_ROOT / "scripts" / "embedding-bench-cases.json"
RESULTS_DIR = REPO_ROOT / "scripts" / "bench-results"

PORT = 18080
BASE_URL = f"http://127.0.0.1:{PORT}"
CONTAINER_NAME = "embed-bench"
IMAGE = "ghcr.io/ggml-org/llama.cpp:server-cuda13"

CATEGORIES = ["paraphrase", "opposite", "same_topic_different_fact", "unrelated"]
QUERY_CATEGORIES = ["base", "paraphrase", "opposite", "same_topic_different_fact", "unrelated"]

MODELS = [
    {"label": "nomic-embed-text-v1.5", "file": "nomic-embed-text-v1.5.f16.gguf"},
    {"label": "nomic-embed-text-v2-moe", "file": "nomic-embed-text-v2-moe.Q8_0.gguf"},
    {"label": "EmbeddingGemma-300M", "file": "embeddinggemma-300M-Q8_0.gguf"},
]


# --- GGUF metadata (context length, embedding dims) read directly from the
# file header -- no need to load the model to get these. GGUF v3 layout:
# magic(4) | version(u32) | tensor_count(u64) | kv_count(u64) | kv pairs.
_GGUF_SCALAR_SIZES = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}
_GGUF_SCALAR_FMT = {
    0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i",
    6: "<f", 7: "<B", 10: "<Q", 11: "<q", 12: "<d",
}


def read_gguf_metadata(path):
    with open(path, "rb") as f:
        magic = f.read(4)
        if magic != b"GGUF":
            raise ValueError(f"{path}: not a GGUF file")
        f.read(4)  # version
        f.read(8)  # tensor_count
        (kv_count,) = struct.unpack("<Q", f.read(8))

        def read_str():
            (n,) = struct.unpack("<Q", f.read(8))
            return f.read(n).decode("utf-8", errors="replace")

        def read_value(vtype):
            if vtype == 8:  # STRING
                return read_str()
            if vtype == 9:  # ARRAY
                (elem_type,) = struct.unpack("<I", f.read(4))
                (count,) = struct.unpack("<Q", f.read(8))
                return [read_value(elem_type) for _ in range(count)]
            size = _GGUF_SCALAR_SIZES[vtype]
            return struct.unpack(_GGUF_SCALAR_FMT[vtype], f.read(size))[0]

        found = {}
        for _ in range(kv_count):
            key = read_str()
            (vtype,) = struct.unpack("<I", f.read(4))
            val = read_value(vtype)
            if key == "general.architecture" or key.endswith(".context_length") or key.endswith(".embedding_length"):
                found[key] = val

        arch = found.get("general.architecture", "")
        return {
            "context_length": found.get(f"{arch}.context_length"),
            "embedding_length": found.get(f"{arch}.embedding_length"),
        }


def gpu_mem_used_mib():
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, check=True,
    ).stdout.strip().splitlines()[0]
    return int(out)


def wait_for_health(timeout_s=60):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{BASE_URL}/health", timeout=2) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(1)
    return False


def embed(text):
    req = urllib.request.Request(
        f"{BASE_URL}/v1/embeddings",
        data=json.dumps({"model": "bench", "input": text}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    return data["data"][0]["embedding"]


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb)


def docker_stop_quiet():
    subprocess.run(["docker", "stop", CONTAINER_NAME], capture_output=True)
    # --rm auto-removes on stop, but wait until it's actually gone so the
    # next model's container name/port don't collide.
    for _ in range(30):
        out = subprocess.run(
            ["docker", "ps", "-q", "-f", f"name=^{CONTAINER_NAME}$"],
            capture_output=True, text=True,
        ).stdout.strip()
        if not out:
            return
        time.sleep(0.5)


def bench_model(model):
    gguf_path = MODELS_DIR / model["file"]
    if not gguf_path.exists():
        print(f"  SKIP: {gguf_path} not found", file=sys.stderr)
        return None

    meta = read_gguf_metadata(gguf_path)
    ctx_len = meta["context_length"] or 2048

    docker_stop_quiet()
    baseline_vram = gpu_mem_used_mib()

    subprocess.run(
        [
            "docker", "run", "-d", "--rm", "--name", CONTAINER_NAME,
            "--gpus", "all", "-p", f"{PORT}:8080",
            "-v", f"{MODELS_DIR}:/models",
            IMAGE,
            "-m", f"/models/{model['file']}",
            "--embeddings", "--ctx-size", str(ctx_len), "-ngl", "99",
            "--host", "0.0.0.0",
        ],
        check=True, capture_output=True,
    )

    if not wait_for_health():
        logs = subprocess.run(["docker", "logs", CONTAINER_NAME], capture_output=True, text=True).stdout
        print(f"  FAILED to become healthy. Logs:\n{logs}", file=sys.stderr)
        docker_stop_quiet()
        return None

    loaded_vram = gpu_mem_used_mib()
    vram_footprint_mib = loaded_vram - baseline_vram

    cases = json.loads(CASES_FILE.read_text())["cases"]

    per_category_scores = {cat: [] for cat in CATEGORIES}
    per_query_category_scores = {cat: [] for cat in QUERY_CATEGORIES}
    per_case_results = []
    per_case_query_results = []
    embedding_dims = None

    for case in cases:
        base_vec = embed(case["base"])
        embedding_dims = embedding_dims or len(base_vec)
        variant_vecs = {"base": base_vec}

        row = {"id": case["id"], "scope": case["scope"]}
        for cat in CATEGORIES:
            vec = embed(case[cat])
            variant_vecs[cat] = vec
            score = cosine(base_vec, vec)
            row[cat] = round(score, 4)
            per_category_scores[cat].append(score)
        per_case_results.append(row)

        # Query-to-memory: a roleplay line that raises the base fact's topic
        # (mem0's actual search() usage -- embedding a conversation turn, not
        # one memory text, to find related stored memories), scored against
        # the base fact and every variant.
        query_vec = embed(case["query"])
        query_row = {"id": case["id"], "scope": case["scope"]}
        for cat in QUERY_CATEGORIES:
            score = cosine(query_vec, variant_vecs[cat])
            query_row[cat] = round(score, 4)
            per_query_category_scores[cat].append(score)
        per_case_query_results.append(query_row)

    averages = {cat: round(mean(scores), 4) for cat, scores in per_category_scores.items()}
    stdevs = {cat: round(stdev(scores), 4) for cat, scores in per_category_scores.items()}
    query_averages = {cat: round(mean(scores), 4) for cat, scores in per_query_category_scores.items()}
    query_stdevs = {cat: round(stdev(scores), 4) for cat, scores in per_query_category_scores.items()}

    docker_stop_quiet()

    return {
        "label": model["label"],
        "file": model["file"],
        "max_tokens_accepted": ctx_len,
        "embedding_dimensions": embedding_dims,
        "vram_footprint_mib": vram_footprint_mib,
        "vram_baseline_mib": baseline_vram,
        "vram_loaded_mib": loaded_vram,
        "average_similarity_by_category": averages,
        "stdev_similarity_by_category": stdevs,
        "per_case": per_case_results,
        "query_average_similarity_by_category": query_averages,
        "query_stdev_similarity_by_category": query_stdevs,
        "per_case_query": per_case_query_results,
    }


def print_summary(results):
    print("\n=== Model specs ===")
    print(f"{'Model':30s} {'VRAM (MiB)':>12s} {'Max tokens':>12s} {'Dimensions':>12s}")
    for r in results:
        print(f"{r['label']:30s} {r['vram_footprint_mib']:>12d} {r['max_tokens_accepted']:>12d} {r['embedding_dimensions']:>12d}")

    print("\n=== Memory-to-memory: cosine similarity vs. base memory, by category (mean +/- stdev) ===")
    header = f"{'Model':30s}" + "".join(f"{cat:>26s}" for cat in CATEGORIES)
    print(header)
    for r in results:
        row = f"{r['label']:30s}"
        for cat in CATEGORIES:
            avg = r["average_similarity_by_category"][cat]
            sd = r["stdev_similarity_by_category"][cat]
            row += f"{f'{avg:.4f} +/- {sd:.4f}':>26s}"
        print(row)

    print("\n=== Query-to-memory: cosine similarity vs. roleplay query, by category (mean +/- stdev) ===")
    header = f"{'Model':30s}" + "".join(f"{cat:>26s}" for cat in QUERY_CATEGORIES)
    print(header)
    for r in results:
        row = f"{r['label']:30s}"
        for cat in QUERY_CATEGORIES:
            avg = r["query_average_similarity_by_category"][cat]
            sd = r["query_stdev_similarity_by_category"][cat]
            row += f"{f'{avg:.4f} +/- {sd:.4f}':>26s}"
        print(row)


def main():
    RESULTS_DIR.mkdir(exist_ok=True)
    results = []
    for model in MODELS:
        print(f"\n--- Benchmarking {model['label']} ---")
        r = bench_model(model)
        if r:
            results.append(r)
            print(f"  VRAM footprint: {r['vram_footprint_mib']} MiB")
            print(f"  Max tokens accepted: {r['max_tokens_accepted']}")
            print(f"  Embedding dimensions: {r['embedding_dimensions']}")
            print(f"  Memory-to-memory averages: {r['average_similarity_by_category']}")
            print(f"  Memory-to-memory std devs:  {r['stdev_similarity_by_category']}")
            print(f"  Query-to-memory averages:  {r['query_average_similarity_by_category']}")
            print(f"  Query-to-memory std devs:   {r['query_stdev_similarity_by_category']}")

    if not results:
        print("No models benchmarked successfully.", file=sys.stderr)
        sys.exit(1)

    out_path = RESULTS_DIR / "embedding-models.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nFull results written to: {out_path}")

    print_summary(results)


if __name__ == "__main__":
    main()
