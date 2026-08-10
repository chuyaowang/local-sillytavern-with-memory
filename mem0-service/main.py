import json
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel
from mem0 import Memory

OLLAMA_BASE_URL = "http://ollama:11434"

config = {
    "llm": {
        "provider": "ollama",
        "config": {
            "model": "gemma4-e4b-hauhaucs",
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
            "collection_name": "roleplay_memories",
            "host": "qdrant",
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


@app.get("/memories")
def list_memories(user_id: str, agent_id: Optional[str] = None):
    return memory.get_all(filters=_scope(user_id, agent_id))


@app.get("/memories/search")
def search_memories(query: str, user_id: str, agent_id: Optional[str] = None):
    return memory.search(query, filters=_scope(user_id, agent_id))


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