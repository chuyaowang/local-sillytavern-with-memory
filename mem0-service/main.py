import json
import logging
import os
import re
import threading
import uuid
from contextlib import contextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException

logger = logging.getLogger("mem0-service")
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from qdrant_client.models import FieldCondition, Filter, MatchValue
from mem0 import Memory
from mem0.memory.utils import extract_json, remove_code_blocks
from mem0.utils.entity_extraction import extract_entities_batch

OLLAMA_BASE_URL = "http://ollama:11434"

# Ollama is shared between the dev and prod stacks (GPU/VRAM is the scarce
# resource, no reason to load the model twice), but each stack needs its own
# Qdrant so memory data never crosses over -- QDRANT_HOST lets the same image
# serve either one depending on which compose service sets it.
QDRANT_HOST = os.environ.get("QDRANT_HOST", "qdrant")

# Lets a throwaway container write to a scratch collection instead of the
# real "roleplay_memories" store.
QDRANT_COLLECTION = os.environ.get("QDRANT_COLLECTION", "roleplay_memories")

# The embedder is the one piece mem0-service still calls directly -- text
# generation for every live path (extraction, classification, World Weaver)
# now runs through SillyTavern's own active connection instead (see
# extraction_prompt()/extraction_store() below), so it follows whatever
# backend the user has configured there, automatically. Embeddings can't
# follow the same mechanism: most chat-only backends don't expose an
# embedding API at all (Claude has none), so this stays its own,
# separately-configured, OpenAI-compatible endpoint -- still defaults to the
# local llama.cpp/nomic-embed-text-v1.5 setup, still swappable to any other
# OpenAI-compatible embedding endpoint.
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

# mem0's Memory.__init__ unconditionally builds an LLM client
# (LlmFactory.create), even though no live path here ever calls it directly
# -- this placeholder is never invoked (see _CapturePromptLLM/_ReplayLLM and
# _with_llm() below, which temporarily replace .llm on this one instance for
# the duration of a single extraction call) and needs no real endpoint or
# key.
_PLACEHOLDER_LLM_CONFIG = {
    "provider": "openai",
    "config": {"model": "unused", "api_key": "not-needed"},
}

# One shared instance for every route -- including extraction (see
# _with_llm() below). A fresh Memory per extraction request was tried and
# reverted: mem0's history db is a single on-disk SQLite file, and its
# locking is per-connection, not cross-connection, so multiple concurrent
# instances (e.g. two flushes landing close together) could each open their
# own connection to that same file and collide, surfacing as an unrelated
# "database is locked" error with nothing to do with the LLM swap below.
# One shared instance, one shared connection, guarded by _extraction_lock,
# avoids that entirely -- confirmed by reading mem0's SQLiteManager
# directly: its lock is created per-instance in __init__, so it only
# protects a connection from itself, never from a second instance.
memory = Memory.from_config({
    "llm": _PLACEHOLDER_LLM_CONFIG,
    "embedder": embedder_config,
    "vector_store": VECTOR_STORE_CONFIG,
})


class _PromptCaptured(Exception):
    """Sentinel raised by _CapturePromptLLM to unwind out of mem0's real
    extraction pipeline with the prompt it built, without ever completing
    generation."""

    def __init__(self, system_prompt: str, user_prompt: str):
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt


class _CapturePromptLLM:
    # Swapped in for Memory.llm to intercept the single generate_response()
    # call Memory._add_to_vector_store() makes (confirmed by reading the
    # installed mem0 package directly: this version's infer=True path is
    # exactly one LLM call per add(), with existing-memory context already
    # folded into that one prompt -- no second merge-decision call). Raising
    # here aborts before Phase 3 (embedding/storage), so nothing is
    # persisted -- Phases 0-2 (session scope, existing-memory search, prompt
    # building) all ran for real first, so the captured prompt is exactly
    # what mem0 would have sent, not a hand-reconstructed approximation.
    def generate_response(self, messages=None, response_format=None, **kwargs):
        system_prompt = next((m["content"] for m in (messages or []) if m.get("role") == "system"), "")
        user_prompt = next((m["content"] for m in (messages or []) if m.get("role") == "user"), "")
        raise _PromptCaptured(system_prompt, user_prompt)


class _ReplayLLM:
    # Swapped in for Memory.llm to replay a completion that was actually
    # generated client-side (via SillyTavern's own connection, see the
    # extension's flushBuffer()) back into the real pipeline, so Phases 3-8
    # (embedding, hash dedup, insert, history, entity linking, message
    # history) run completely unmodified -- never reimplemented by hand.
    def __init__(self, response_text: str):
        self._response_text = response_text

    def generate_response(self, messages=None, response_format=None, **kwargs):
        return self._response_text


# Guards every swap of the shared `memory` instance's .llm attribute.
# FastAPI runs sync route handlers in a thread pool, so two extraction
# requests can genuinely be in progress at once (e.g. two flushes landing
# close together) -- without this, one request's swapped-in shim could be
# overwritten by another's mid-call, silently feeding one conversation's
# extraction result to a different conversation's request. Held for the
# entire duration of the call, not just the assignment, so a second request
# waits its turn instead of racing.
_extraction_lock = threading.Lock()


@contextmanager
def _with_llm(llm):
    with _extraction_lock:
        original = memory.llm
        memory.llm = llm
        try:
            yield memory
        finally:
            memory.llm = original


def _find_captured(exc: BaseException) -> Optional[_PromptCaptured]:
    # mem0's own _add_to_vector_store wraps any exception from
    # generate_response() in LLMError ("raise LLMError(...) from e") --
    # walk __cause__ to find the sentinel regardless of that wrapping.
    seen: Optional[BaseException] = exc
    while seen is not None:
        if isinstance(seen, _PromptCaptured):
            return seen
        seen = seen.__cause__
    return None


def _parse_json_tolerant(raw: str) -> Optional[dict]:
    cleaned = remove_code_blocks(raw or "")
    if not cleaned.strip():
        return None
    try:
        return json.loads(cleaned, strict=False)
    except json.JSONDecodeError:
        try:
            return json.loads(extract_json(cleaned), strict=False)
        except json.JSONDecodeError:
            return None


app = FastAPI(title="mem0-service")


class ExtractionPromptRequest(BaseModel):
    messages: list[dict]
    user_id: str
    agent_id: Optional[str] = None


class ExtractionStoreRequest(BaseModel):
    messages: list[dict]
    user_id: str
    agent_id: Optional[str] = None
    raw_response: str
    # The world that was resolved (persona-bound, else character-bound) when
    # this exchange happened -- same value the extension already sends to
    # classification. Tagging the primary write with it (not just the
    # shared/world moves classification produces) lets the admin UI show
    # which world was active for a character-scoped memory too.
    world: Optional[str] = None


class ClassificationPromptRequest(BaseModel):
    character_memories: list[dict]  # [{"id": ..., "memory": ...}, ...]
    world: Optional[str] = None


class ClassificationApplyRequest(BaseModel):
    character_memories: list[dict]
    world: Optional[str] = None
    raw_response: str
    user_id: str
    agent_id: str


class WorldInterviewPromptRequest(BaseModel):
    messages: list[dict]


class WorldInterviewApplyRequest(BaseModel):
    raw_response: str


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
# "kivotos", ...), so multiple worlds need no per-world schema.
WORLD_USER_ID = "world"

# Shared memories get tagged with the world that was resolved (persona-bound,
# else character-bound -- see the ST extension's resolveWorld()) when they
# were written, via mem0's metadata filtering. NO_WORLD is a real sentinel
# value rather than leaving the field unset -- mem0's filter DSL has no
# "field is unset" operator, so a fact learned with no world bound still
# needs a concrete value to be queryable as "universal".
NO_WORLD = "none"


def _slugify(value: str) -> Optional[str]:
    # Mirrors sillytavern/extensions/roleplay-memory/index.js's slugify() so
    # a world name matches however the ST extension derived it client-side.
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or None


def _scope(user_id: str, agent_id: Optional[str]) -> dict:
    # Every memory gets a real agent_id -- the SHARED_AGENT_ID sentinel when
    # none is given -- so "shared" can be queried as an equality filter.
    return {"user_id": user_id, "agent_id": agent_id or SHARED_AGENT_ID}


CLASSIFICATION_PROMPT_TEMPLATE = """You are given a numbered list of memories extracted from a conversation between the
user and a character. This system organizes memories into scopes:

1. Character memory: relationship and history specific to this one character, private
to conversations with them -- these memories are already stored in that scope.
2. Shared memory: general facts about the user, true regardless of which character
they are talking to -- personal preferences, biographical details, opinions, or traits.
Examples: favorite food, job, hobbies, fears, birthday.
{world_section}
Classify each memory in the list below: does it ALSO belong in shared memory, in world
memory, or neither? A character's own personality, job, habits, or backstory, in-story
events, and relationship dynamics between the user and this one character belong to
neither -- they are not facts about the user or about the setting.

Return ONLY valid JSON in this exact shape, using the memory numbers from the list:
{{"shared": [1, 3], "world": [2]}}

If no memory belongs in a given scope, return an empty list for it.
"""

WORLD_SECTION_TEMPLATE = """
3. World memory, for the world "{world}": lore about the fictional setting itself --
geography, history, factions, cultures, rules of magic or technology. Not about the
user, and not about any one character's own personality or traits.
"""

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


def _link_entities(mem: "Memory", memory_id: str, text: str, filters: dict) -> None:
    # mem0's own entity-store linking (named entities -> the memories that
    # mention them, used for search-relevance boosting) only runs as part of
    # its infer=True extraction pipeline. The shared/world moves below use
    # infer=False (the text is already an atomic fact, re-extracting it
    # would risk rewording it), which means they'd otherwise never get
    # entity links at all. This replicates that step by calling mem0's own
    # private entity-store helpers directly. Best-effort -- any failure here
    # doesn't affect the memory write itself.
    try:
        entities = extract_entities_batch([text])[0]
    except Exception:
        return
    if not entities:
        return

    search_filters = {k: v for k, v in filters.items() if k in ("user_id", "agent_id", "run_id") and v}
    try:
        exact_matches = mem._existing_entities_by_text(search_filters)
    except Exception:
        exact_matches = {}

    for entity_type, entity_text in entities:
        try:
            key = mem._normalize_entity_text(entity_text)
            vector = mem.embedding_model.embed(entity_text, "add")
        except Exception:
            continue

        match = exact_matches.get(key)
        if not match:
            try:
                results = mem.entity_store.search_batch(
                    queries=[entity_text], vectors_list=[vector], top_k=1, filters=search_filters,
                )
                candidates = results[0] if results else []
                if candidates and candidates[0].score >= 0.95:
                    match = candidates[0]
            except Exception:
                pass

        if match:
            try:
                payload = match.payload or {}
                linked = set(payload.get("linked_memory_ids", []))
                linked.add(memory_id)
                payload["linked_memory_ids"] = sorted(linked)
                mem.entity_store.update(vector_id=match.id, vector=None, payload=payload)
            except Exception:
                pass
        else:
            try:
                mem.entity_store.insert(
                    vectors=[vector],
                    ids=[str(uuid.uuid4())],
                    payloads=[{
                        "data": entity_text,
                        "entity_type": entity_type,
                        "linked_memory_ids": [memory_id],
                        **search_filters,
                    }],
                )
            except Exception:
                pass


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/extraction/prompt")
def extraction_prompt(req: ExtractionPromptRequest):
    with _with_llm(_CapturePromptLLM()) as mem:
        try:
            mem.add(req.messages, **_scope(req.user_id, req.agent_id))
        except Exception as e:
            captured = _find_captured(e)
            if captured is None:
                # Some real exception happened before the pipeline ever
                # reached the LLM call -- log the full traceback, since a
                # raised HTTPException doesn't get one printed by default
                # (it's "handled", so FastAPI doesn't treat it as a crash),
                # which made an earlier bug here silently undiagnosable
                # from the access log line alone.
                logger.exception("extraction/prompt failed for user_id=%s agent_id=%s", req.user_id, req.agent_id)
                raise HTTPException(status_code=500, detail=f"prompt capture failed: {e}") from e
            return {"system_prompt": captured.system_prompt, "user_prompt": captured.user_prompt}
    logger.error("extraction/prompt: pipeline returned without calling the LLM for user_id=%s agent_id=%s", req.user_id, req.agent_id)
    raise HTTPException(status_code=500, detail="extraction pipeline did not reach the LLM call")


@app.post("/extraction/store")
def extraction_store(req: ExtractionStoreRequest):
    with _with_llm(_ReplayLLM(req.raw_response)) as mem:
        return mem.add(
            req.messages, **_scope(req.user_id, req.agent_id), metadata={"world": req.world or NO_WORLD},
        )


@app.post("/classification/prompt")
def classification_prompt(req: ClassificationPromptRequest):
    if not req.character_memories:
        raise HTTPException(status_code=400, detail="character_memories must be non-empty")
    memory_list = "\n".join(f"{i + 1}. {m['memory']}" for i, m in enumerate(req.character_memories))
    world_section = WORLD_SECTION_TEMPLATE.format(world=req.world) if req.world else ""
    system_prompt = CLASSIFICATION_PROMPT_TEMPLATE.format(world_section=world_section)
    return {"system_prompt": system_prompt, "user_prompt": memory_list}


@app.post("/classification/apply")
def classification_apply(req: ClassificationApplyRequest):
    # A memory only ever lives in one scope at a time: once a fact is
    # confirmed moved into shared or world scope, its character-scoped
    # original is deleted -- never mirrored/kept in both places. Collected
    # here and deleted only after every move below has actually succeeded,
    # so a failed move never loses data.
    data = _parse_json_tolerant(req.raw_response) or {}
    n = len(req.character_memories)

    def _lookup(indices) -> list[dict]:
        return [req.character_memories[i - 1] for i in indices if isinstance(i, int) and 1 <= i <= n]

    shared_facts = _lookup(data.get("shared", []))
    world_facts = _lookup(data.get("world", [])) if req.world else []

    moved_ids: set[str] = set()

    if shared_facts:
        shared_messages = [{"role": "user", "content": f["memory"]} for f in shared_facts]
        shared_filters = {"user_id": req.user_id, "agent_id": SHARED_AGENT_ID}
        shared_result = memory.add(
            shared_messages,
            user_id=req.user_id,
            agent_id=SHARED_AGENT_ID,
            infer=False,
            metadata={"world": req.world or NO_WORLD},
        )
        for r in shared_result.get("results", []):
            if r.get("id") and r.get("memory"):
                _link_entities(memory, r["id"], r["memory"], shared_filters)
        moved_ids.update(f["id"] for f in shared_facts)

    if world_facts:
        world_messages = [{"role": "user", "content": f["memory"]} for f in world_facts]
        world_filters = {"user_id": WORLD_USER_ID, "agent_id": req.world}
        world_result = memory.add(world_messages, user_id=WORLD_USER_ID, agent_id=req.world, infer=False)
        for r in world_result.get("results", []):
            if r.get("id") and r.get("memory"):
                _link_entities(memory, r["id"], r["memory"], world_filters)
        moved_ids.update(f["id"] for f in world_facts)

    for memory_id in moved_ids:
        try:
            memory.delete(memory_id)
        except Exception:
            pass

    return {"shared_count": len(shared_facts), "world_count": len(world_facts)}


@app.post("/worlds/interview/prompt")
def worlds_interview_prompt(req: WorldInterviewPromptRequest):
    # The World Creator character isn't bound to one fixed world the way a
    # roleplay character is -- the interview conversation itself establishes
    # what's being built, so this identifies the world name(s) from the
    # transcript rather than being told one, unlike extraction_prompt().
    transcript = "\n".join(f"{m.get('role', 'user')}: {m.get('content', '')}" for m in req.messages)
    return {"system_prompt": WORLD_INTERVIEW_PROMPT, "user_prompt": transcript}


@app.post("/worlds/interview/apply")
def worlds_interview_apply(req: WorldInterviewApplyRequest):
    data = _parse_json_tolerant(req.raw_response) or {}
    worlds = data.get("worlds", [])

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
    # A shared fact learned with no world bound (NO_WORLD) is universal and
    # always included; one learned within a specific world only surfaces
    # again for that same world, not for an unrelated one -- otherwise a
    # personal detail like "the user's mount is the Aetherian Stride"
    # (learned while bound to Eldoria) would leak into a conversation with a
    # character from a completely different setting.
    world_filter = {"in": [world, NO_WORLD]} if world else NO_WORLD
    shared = memory.search(query, filters={"user_id": user_id, "agent_id": SHARED_AGENT_ID, "world": world_filter})
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
