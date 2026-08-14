import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Optional

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from qdrant_client.models import FieldCondition, Filter, MatchValue
from mem0 import Memory
from mem0.configs.prompts import ADDITIVE_EXTRACTION_PROMPT, generate_additive_extraction_prompt
from mem0.memory.utils import extract_json, parse_messages, remove_code_blocks

OLLAMA_BASE_URL = "http://ollama:11434"

# Ollama is shared between the dev and prod stacks (GPU/VRAM is the scarce
# resource, no reason to load the model twice), but each stack needs its own
# Qdrant so memory data never crosses over -- QDRANT_HOST lets the same image
# serve either one depending on which compose service sets it.
QDRANT_HOST = os.environ.get("QDRANT_HOST", "qdrant")

# Model is swappable per-container the same way -- lets a throwaway
# container (e.g. scripts/test-model.sh) evaluate a candidate model without
# touching the real dev/prod mem0 containers or their config.
MEM0_LLM_MODEL = os.environ.get("MEM0_LLM_MODEL", "gemma4-e4b-hauhaucs")

# Same idea for the collection name -- a throwaway container can write to a
# scratch collection instead of the real "roleplay_memories" store.
QDRANT_COLLECTION = os.environ.get("QDRANT_COLLECTION", "roleplay_memories")

# Lets a throwaway container (e.g. scripts/test-model-llama-cpp.sh) point the
# LLM at a different backend entirely -- llama.cpp's server speaks the
# OpenAI-compatible API, not Ollama's, so this needs its own provider and
# base URL rather than just swapping the model name. Defaults preserve the
# existing Ollama-only behavior for the real dev/prod containers.
MEM0_LLM_PROVIDER = os.environ.get("MEM0_LLM_PROVIDER", "ollama")
MEM0_LLM_BASE_URL = os.environ.get("MEM0_LLM_BASE_URL", OLLAMA_BASE_URL)

def _llm_config(model: str) -> dict:
    if MEM0_LLM_PROVIDER == "openai":
        return {
            "provider": "openai",
            "config": {
                "model": model,
                "openai_base_url": MEM0_LLM_BASE_URL,
                # llama.cpp's server doesn't check this, but the openai client
                # library requires a non-empty key to be set.
                "api_key": "not-needed",
            },
        }
    return {
        "provider": "ollama",
        "config": {
            "model": model,
            "ollama_base_url": MEM0_LLM_BASE_URL,
        },
    }


# Same swap for the embedder -- lets a throwaway container point it at a
# llama.cpp server running the same nomic-embed-text-v1.5 GGUF instead of
# Ollama's copy, so an all-llama.cpp pipeline can be tested without Ollama
# in the loop at all. Defaults preserve the existing Ollama-only behavior.
MEM0_EMBEDDER_PROVIDER = os.environ.get("MEM0_EMBEDDER_PROVIDER", "ollama")
MEM0_EMBEDDER_BASE_URL = os.environ.get("MEM0_EMBEDDER_BASE_URL", OLLAMA_BASE_URL)
MEM0_EMBEDDER_MODEL = os.environ.get(
    "MEM0_EMBEDDER_MODEL", "nomic-embed-text-v1.5" if MEM0_EMBEDDER_PROVIDER == "openai" else "nomic-embed-text"
)

if MEM0_EMBEDDER_PROVIDER == "openai":
    embedder_config = {
        "provider": "openai",
        "config": {
            "model": MEM0_EMBEDDER_MODEL,
            "openai_base_url": MEM0_EMBEDDER_BASE_URL,
            "api_key": "not-needed",
            "embedding_dims": 768,
        },
    }
else:
    embedder_config = {
        "provider": "ollama",
        "config": {
            "model": MEM0_EMBEDDER_MODEL,
            "ollama_base_url": MEM0_EMBEDDER_BASE_URL,
        },
    }

VECTOR_STORE_CONFIG = {
    "provider": "qdrant",
    "config": {
        "collection_name": QDRANT_COLLECTION,
        "host": QDRANT_HOST,
        "port": 6333,
        "embedding_model_dims": 768,  # nomic-embed-text output size
    },
}


def _discover_llm_models() -> list[str]:
    # Roleplay and extraction always share one model (see CLAUDE.md), and
    # with llama.cpp's router mode SillyTavern can pick a different one
    # per-connection at any time. Rather than hardcoding which models exist
    # anywhere (this deployment's models are whatever the user put in
    # llama-cpp/models-preset.ini -- not fixed names this code should
    # assume), ask the router itself what it has via its OpenAI-compatible
    # GET /models. Only meaningful for the openai provider (llama.cpp);
    # Ollama's setup here never had multiple switchable models.
    if MEM0_LLM_PROVIDER != "openai":
        return [MEM0_LLM_MODEL]

    models_url = MEM0_LLM_BASE_URL.rstrip("/")
    if models_url.endswith("/v1"):
        models_url = models_url[: -len("/v1")]
    models_url += "/models"

    # Retries: mem0 can start before llama-cpp's HTTP server is actually
    # up (depends_on only waits for container start, not readiness).
    for _ in range(10):
        try:
            with urllib.request.urlopen(models_url, timeout=3) as resp:
                data = json.loads(resp.read())
            ids = [
                m["id"]
                for m in data.get("data", [])
                if m.get("id") and m["id"] != MEM0_EMBEDDER_MODEL
            ]
            if ids:
                if MEM0_LLM_MODEL not in ids:
                    ids.append(MEM0_LLM_MODEL)
                return ids
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            pass
        time.sleep(3)

    # Discovery never succeeded -- fall back to just the configured
    # default rather than refusing to start.
    return [MEM0_LLM_MODEL]


# One Memory instance per discovered model -- identical except for
# llm.config.model, so a per-request model choice is a dict lookup, not a
# mutation of shared state. Cheap: embedder/vector_store are lightweight
# HTTP-based clients, not persistent expensive connections.
memories_by_model: dict[str, "Memory"] = {
    model_id: Memory.from_config({
        "llm": _llm_config(model_id),
        "embedder": embedder_config,
        "vector_store": VECTOR_STORE_CONFIG,
    })
    for model_id in _discover_llm_models()
}

memory = memories_by_model[MEM0_LLM_MODEL]  # default/fallback instance


def _pick_memory(model: Optional[str]) -> "Memory":
    if model and model in memories_by_model:
        return memories_by_model[model]
    return memory


app = FastAPI(title="mem0-service")


class AddMemoryRequest(BaseModel):
    messages: list[dict]
    user_id: str
    agent_id: Optional[str] = None
    # SillyTavern's active model, passed through by the extension -- picks
    # which of memories_by_model handles this call. Falls back to
    # MEM0_LLM_MODEL if unset or unrecognized.
    model: Optional[str] = None
    # The world the active character is bound to (from its ST card's
    # extensions.world field, reused rather than a separate mapping -- see
    # CLAUDE.md). When set, classify_facts() also checks this exchange for
    # world-relevant facts, alongside the existing shared-fact check.
    world: Optional[str] = None


class ExtractionRawRequest(BaseModel):
    messages: list[dict]
    model: Optional[str] = None


class AddWorldLoreRequest(BaseModel):
    content: str


class WorldInterviewRequest(BaseModel):
    messages: list[dict]
    model: Optional[str] = None


class UpdateMemoryRequest(BaseModel):
    text: str


class BulkReplaceRequest(BaseModel):
    user_id: str
    agent_id: Optional[str] = None
    all_agents: bool = False
    find: str
    replace: str


SHARED_AGENT_ID = "shared"

# World lore isn't "about" any real user at all, so it gets its own user_id
# sentinel rather than reusing the agent_id axis SHARED_AGENT_ID occupies --
# a world name is just an agent_id value under this sentinel ("eldoria",
# "kivotos", ...), so multiple worlds need no per-world schema. Same
# collision precedent already accepted for SHARED_AGENT_ID: a real user
# literally slugifying to "world" would collide, same acceptable edge case.
WORLD_USER_ID = "world"


def _slugify(value: str) -> str:
    # Mirrors sillytavern/extensions/roleplay-memory/index.js's slugify() so
    # a world name matches however the ST extension derived it client-side.
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or None


def _scope(user_id: str, agent_id: Optional[str]) -> dict:
    # Every memory gets a real agent_id -- the SHARED_AGENT_ID sentinel when
    # none is given -- so "shared" can be queried as an equality filter.
    # mem0's filter DSL has no "field is unset" operator, only equality/
    # comparison on values that exist, so leaving agent_id unset would make
    # a true shared-only query impossible.
    return {"user_id": user_id, "agent_id": agent_id or SHARED_AGENT_ID}


CLASSIFICATION_PROMPT_TEMPLATE = """You are analyzing a conversation to identify two kinds of facts.

1. General facts about the user: true about the user regardless of who they are talking
to -- personal preferences, biographical details, opinions, or traits. Examples:
favorite food, job, hobbies, fears, birthday.

{world_section}

NOT either category: anything specific to this particular character relationship --
shared experiences, in-story events, relationship dynamics, or a character's own
personality/traits, none of which are facts about the user or about the setting.

Return ONLY valid JSON in this exact shape:
{{"shared_facts": ["fact one", "fact two"], "world_facts": ["fact one", "fact two"]}}

If there are no facts of a given kind, return an empty list for it.
"""

WORLD_SECTION_TEMPLATE = """2. Facts about the setting/world "{world}": geography, history, factions, cultures,
rules of magic or technology -- lore about the world itself, not about the user or
about any one character's personality."""


def classify_facts(mem: "Memory", messages: list[dict], world: Optional[str] = None) -> dict:
    # Best-effort: this is a secondary enrichment step layered on top of the
    # primary (always-succeeds) character-scoped write below, so any failure
    # here is swallowed rather than breaking the add() call.
    transcript = "\n".join(f"{m.get('role', 'user')}: {m.get('content', '')}" for m in messages)
    world_section = WORLD_SECTION_TEMPLATE.format(world=world) if world else ""
    prompt = CLASSIFICATION_PROMPT_TEMPLATE.format(world_section=world_section)
    try:
        response = mem.llm.generate_response(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": transcript},
            ],
            response_format={"type": "json_object"},
        )
        data = json.loads(response)
        shared_facts = data.get("shared_facts", [])
        world_facts = data.get("world_facts", []) if world else []
        return {
            "shared_facts": [f for f in shared_facts if isinstance(f, str) and f.strip()],
            "world_facts": [f for f in world_facts if isinstance(f, str) and f.strip()],
        }
    except Exception:
        return {"shared_facts": [], "world_facts": []}


WORLD_INTERVIEW_PROMPT = """You are analyzing a world-building interview conversation between a user and an
assistant helping them design a fictional setting.

Identify every distinct world discussed (usually just one) and the facts established
about each -- geography, history, factions, cultures, rules of magic or technology,
tone. Only include facts the user actually stated or confirmed, not questions the
assistant asked.

Return ONLY valid JSON in this exact shape:
{"worlds": [{"name": "World Name", "facts": ["fact one", "fact two"]}]}

If no world name was established yet, return {"worlds": []}.
"""


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/memories")
def add_memory(req: AddMemoryRequest):
    mem = _pick_memory(req.model)
    result = mem.add(req.messages, **_scope(req.user_id, req.agent_id))

    # Only classify when this write was character-scoped -- a call already
    # targeting the shared bucket has nothing further to route.
    if req.agent_id:
        facts = classify_facts(mem, req.messages, world=req.world)
        if facts["shared_facts"]:
            shared_messages = [{"role": "user", "content": fact} for fact in facts["shared_facts"]]
            mem.add(shared_messages, user_id=req.user_id, agent_id=SHARED_AGENT_ID, infer=False)
        if facts["world_facts"]:
            world_messages = [{"role": "user", "content": fact} for fact in facts["world_facts"]]
            mem.add(world_messages, user_id=WORLD_USER_ID, agent_id=req.world, infer=False)

    return result


@app.post("/worlds/{world}/memories")
def add_world_lore(world: str, req: AddWorldLoreRequest):
    # Low-level manual write -- used by the one-time lorebook backfill
    # script, not by either automatic extraction path below. infer=False:
    # this is already-curated text to embed directly, not something to run
    # fact-extraction on.
    return memory.add([{"role": "user", "content": req.content}], user_id=WORLD_USER_ID, agent_id=world, infer=False)


@app.post("/worlds/interview")
def world_interview(req: WorldInterviewRequest):
    # The World Creator character (see CLAUDE.md) isn't bound to one fixed
    # world the way a roleplay character is -- the interview conversation
    # itself establishes what's being built, so this identifies the world
    # name(s) from the transcript rather than being told one, unlike
    # classify_facts() above.
    mem = _pick_memory(req.model)
    transcript = "\n".join(f"{m.get('role', 'user')}: {m.get('content', '')}" for m in req.messages)
    try:
        response = mem.llm.generate_response(
            messages=[
                {"role": "system", "content": WORLD_INTERVIEW_PROMPT},
                {"role": "user", "content": transcript},
            ],
            response_format={"type": "json_object"},
        )
        data = json.loads(response)
        worlds = data.get("worlds", [])
    except Exception:
        worlds = []

    written = []
    for entry in worlds:
        name = entry.get("name") if isinstance(entry, dict) else None
        facts = entry.get("facts") if isinstance(entry, dict) else None
        slug = _slugify(name) if isinstance(name, str) else None
        if not slug or not isinstance(facts, list):
            continue
        fact_texts = [f for f in facts if isinstance(f, str) and f.strip()]
        if not fact_texts:
            continue
        fact_messages = [{"role": "user", "content": fact} for fact in fact_texts]
        memory.add(fact_messages, user_id=WORLD_USER_ID, agent_id=slug, infer=False)
        written.append({"world": slug, "facts": fact_texts})

    return {"written": written}


@app.post("/debug/extraction-raw")
def extraction_raw(req: ExtractionRawRequest):
    # Mirrors mem0's own extraction call (Memory._add_to_vector_store) so this
    # exercises the same prompt/parse path a real /memories call takes.
    # Surfaced separately because mem0 catches a JSON parse failure here and
    # silently turns it into an empty result -- identical to "the model found
    # nothing worth remembering". This endpoint reports the parse failure
    # instead of swallowing it.
    mem = _pick_memory(req.model)
    parsed_messages = parse_messages(req.messages)
    user_prompt = generate_additive_extraction_prompt(new_messages=parsed_messages)

    raw_response = mem.llm.generate_response(
        messages=[
            {"role": "system", "content": ADDITIVE_EXTRACTION_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
    )

    cleaned = remove_code_blocks(raw_response)
    result = {"raw_response": raw_response, "cleaned_response": cleaned}

    if not cleaned or not cleaned.strip():
        result.update(valid_json=False, used_fallback=False, error="empty response", memory_count=0)
        return result

    try:
        parsed = json.loads(cleaned, strict=False)
        result.update(valid_json=True, used_fallback=False)
    except json.JSONDecodeError:
        try:
            parsed = json.loads(extract_json(cleaned), strict=False)
            result.update(valid_json=True, used_fallback=True)
        except json.JSONDecodeError as e2:
            result.update(valid_json=False, used_fallback=True, error=str(e2), memory_count=0)
            return result

    memory_list = parsed.get("memory") if isinstance(parsed, dict) else None
    result.update(
        has_memory_key=isinstance(memory_list, list),
        memory_count=len(memory_list) if isinstance(memory_list, list) else 0,
    )
    return result


def _distinct_field_values(field: str, filter_key: Optional[str] = None, filter_value: Optional[str] = None) -> list[str]:
    # Scrolls the store collecting distinct values of `field`, optionally
    # restricted to points where `filter_key` == `filter_value`. No Qdrant
    # aggregation feature assumed -- plain scroll + collect, fine at this
    # scale (a personal memory store, not a production-sized index).
    client = memory.vector_store.client
    collection = memory.collection_name

    qdrant_filter = None
    if filter_key and filter_value:
        qdrant_filter = Filter(must=[FieldCondition(key=filter_key, match=MatchValue(value=filter_value))])

    values: set[str] = set()
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=collection,
            scroll_filter=qdrant_filter,
            with_payload=[field],
            with_vectors=False,
            limit=200,
            offset=offset,
        )
        for point in points:
            value = (point.payload or {}).get(field)
            if value:
                values.add(value)
        if offset is None:
            break
    return sorted(values)


@app.get("/scopes")
def get_scopes(user_id: Optional[str] = None, agent_id: Optional[str] = None):
    # Cross-filtered: agent_ids returned only co-occur with the given
    # user_id (if any), and vice versa -- so the two dropdowns in the admin
    # UI only ever show combinations that actually exist together, rather
    # than two independent global lists that could pair into an empty
    # (but not obviously invalid-looking) combination.
    return {
        "user_ids": _distinct_field_values("user_id", "agent_id", agent_id),
        "agent_ids": _distinct_field_values("agent_id", "user_id", user_id),
    }


@app.get("/memories")
def list_memories(user_id: str, agent_id: Optional[str] = None, all_agents: bool = False):
    # all_agents=true skips resolving a single agent_id, matching every
    # scope (shared and every character) for this user_id -- useful for an
    # admin view that shows everything at once rather than one scope at a
    # time. mem0's filter DSL matches any value for a field left out of the
    # filter dict entirely, which is what makes this work.
    filters = {"user_id": user_id} if all_agents else _scope(user_id, agent_id)
    return memory.get_all(filters=filters)


@app.get("/memories/search")
def search_memories(query: str, user_id: str, agent_id: Optional[str] = None, all_agents: bool = False):
    filters = {"user_id": user_id} if all_agents else _scope(user_id, agent_id)
    return memory.search(query, filters=filters)


@app.get("/memories/context")
def get_context(query: str, user_id: str, agent_id: Optional[str] = None, world: Optional[str] = None):
    shared = memory.search(query, filters={"user_id": user_id, "agent_id": SHARED_AGENT_ID})
    character = memory.search(query, filters={"user_id": user_id, "agent_id": agent_id}) if agent_id else []
    lore = memory.search(query, filters={"user_id": WORLD_USER_ID, "agent_id": world}) if world else []
    return {"shared": shared, "character": character, "world": lore}


@app.put("/memories/{memory_id}")
def update_memory(memory_id: str, req: UpdateMemoryRequest):
    return memory.update(memory_id, req.text)


@app.delete("/memories/{memory_id}")
def delete_memory(memory_id: str):
    memory.delete(memory_id)
    return {"status": "deleted"}


@app.post("/memories/bulk-replace")
def bulk_replace(req: BulkReplaceRequest):
    # Plain case-sensitive literal substring replace -- predictable and
    # auditable, not a regex/fuzzy match that could touch unintended text.
    # Goes through the same memory.update() used by the single-memory PUT
    # endpoint for each affected memory, so embeddings and entity_store links
    # get refreshed correctly for every one of them -- this does not, and
    # cannot, reconcile memories in other scopes or catch paraphrased
    # mentions that don't literally contain `find`.
    filters = {"user_id": req.user_id} if req.all_agents else _scope(req.user_id, req.agent_id)
    all_memories = memory.get_all(filters=filters)["results"]

    updated = []
    for item in all_memories:
        text = item.get("memory") or ""
        if req.find in text:
            new_text = text.replace(req.find, req.replace)
            memory.update(item["id"], text=new_text)
            updated.append({"id": item["id"], "before": text, "after": new_text})

    return {"updated_count": len(updated), "updated": updated}


# Mounted at /ui (not /) so it doesn't shadow the API routes above -- the
# page's own JS calls those routes at the app's actual root.
app.mount("/ui", StaticFiles(directory="static", html=True), name="ui")