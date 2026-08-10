import json
import os
from typing import Optional

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from qdrant_client.models import FieldCondition, Filter, MatchValue
from mem0 import Memory

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

config = {
    "llm": {
        "provider": "ollama",
        "config": {
            "model": MEM0_LLM_MODEL,
            "ollama_base_url": OLLAMA_BASE_URL,
        },
    },
    "embedder": {
        "provider": "ollama",
        "config": {
            "model": "nomic-embed-text",
            "ollama_base_url": OLLAMA_BASE_URL,
        },
    },
    "vector_store": {
        "provider": "qdrant",
        "config": {
            "collection_name": QDRANT_COLLECTION,
            "host": QDRANT_HOST,
            "port": 6333,
            "embedding_model_dims": 768,  # nomic-embed-text output size
        },
    },
}

memory = Memory.from_config(config)

app = FastAPI(title="mem0-service")


class AddMemoryRequest(BaseModel):
    messages: list[dict]
    user_id: str
    agent_id: Optional[str] = None


class UpdateMemoryRequest(BaseModel):
    text: str


class BulkReplaceRequest(BaseModel):
    user_id: str
    agent_id: Optional[str] = None
    all_agents: bool = False
    find: str
    replace: str


SHARED_AGENT_ID = "shared"


def _scope(user_id: str, agent_id: Optional[str]) -> dict:
    # Every memory gets a real agent_id -- the SHARED_AGENT_ID sentinel when
    # none is given -- so "shared" can be queried as an equality filter.
    # mem0's filter DSL has no "field is unset" operator, only equality/
    # comparison on values that exist, so leaving agent_id unset would make
    # a true shared-only query impossible.
    return {"user_id": user_id, "agent_id": agent_id or SHARED_AGENT_ID}


CLASSIFICATION_PROMPT = """You are analyzing a conversation to identify general facts about the user.

A general fact is true about the user regardless of who they are talking to -- personal
preferences, biographical details, opinions, or traits. Examples: favorite food, job,
hobbies, fears, birthday.

NOT a general fact: anything specific to this particular conversation partner or
storyline -- shared experiences, in-story events, relationship dynamics, or things that
only make sense in the context of this specific character.

Return ONLY valid JSON in this exact shape:
{"shared_facts": ["fact one", "fact two"]}

If there are no general facts, return {"shared_facts": []}.
"""


def classify_shared_facts(messages: list[dict]) -> list[str]:
    # Best-effort: this is a secondary enrichment step layered on top of the
    # primary (always-succeeds) character-scoped write below, so any failure
    # here is swallowed rather than breaking the add() call.
    transcript = "\n".join(f"{m.get('role', 'user')}: {m.get('content', '')}" for m in messages)
    try:
        response = memory.llm.generate_response(
            messages=[
                {"role": "system", "content": CLASSIFICATION_PROMPT},
                {"role": "user", "content": transcript},
            ],
            response_format={"type": "json_object"},
        )
        data = json.loads(response)
        facts = data.get("shared_facts", [])
        return [f for f in facts if isinstance(f, str) and f.strip()]
    except Exception:
        return []


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/memories")
def add_memory(req: AddMemoryRequest):
    result = memory.add(req.messages, **_scope(req.user_id, req.agent_id))

    # Only classify when this write was character-scoped -- a call already
    # targeting the shared bucket has nothing further to route.
    if req.agent_id:
        shared_facts = classify_shared_facts(req.messages)
        if shared_facts:
            shared_messages = [{"role": "user", "content": fact} for fact in shared_facts]
            memory.add(shared_messages, user_id=req.user_id, agent_id=SHARED_AGENT_ID, infer=False)

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
def get_context(query: str, user_id: str, agent_id: Optional[str] = None):
    shared = memory.search(query, filters={"user_id": user_id, "agent_id": SHARED_AGENT_ID})
    character = memory.search(query, filters={"user_id": user_id, "agent_id": agent_id}) if agent_id else []
    return {"shared": shared, "character": character}


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