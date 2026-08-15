function slugify(value) {
    if (!value) return null;
    return value.toLowerCase().trim().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '') || null;
}

function humanize(slug) {
    if (!slug) return slug;
    return slug.split('-').map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
}

// Mirrors mem0-service/main.py's WORLD_USER_ID sentinel -- world lore isn't
// "about" any real user, so it lives under this fixed user_id instead.
const WORLD_USER_ID = 'world';

function getIds() {
    const context = SillyTavern.getContext();
    const character = context.characters?.[context.characterId];
    return {
        userId: slugify(context.name1) || 'default-user',
        agentId: slugify(character?.name),
        // Kept alongside the slugs, purely for toast text -- the slugs are
        // what the API actually uses.
        personaName: context.name1 || 'You',
        characterName: character?.name || null,
    };
}

// Reuses ST's own "Primary Lorebook" character binding (the globe icon in
// the character panel, character.data.extensions.world) instead of a
// separate mapping -- confirmed this session that ST never rewrites the
// bound world file on its own, so it's safe to treat this field as pure
// metadata once that file's entries are emptied out. See CLAUDE.md.
function getCharacterBoundWorld() {
    const context = SillyTavern.getContext();
    const character = context.characters?.[context.characterId];
    return slugify(character?.data?.extensions?.world);
}

// ST also lets a *persona* (not just a character) bind to a lorebook --
// power_user.persona_description_lorebook, set via the persona panel's own
// lorebook picker. Confirmed exposed on getContext() as
// context.powerUserSettings (same live object, not a snapshot -- see
// st-context.js's getContext(), "powerUserSettings: power_user").
function getPersonaBoundWorld() {
    const context = SillyTavern.getContext();
    return slugify(context.powerUserSettings?.persona_description_lorebook);
}

// Persona binding takes priority over the active character's -- if neither
// is bound, the exchange isn't tied to any world at all.
function resolveWorld() {
    return getPersonaBoundWorld() || getCharacterBoundWorld();
}

// The World Creator is a plain SillyTavern character (not a special API
// flag -- ST's character panel has no UI for arbitrary custom extensions
// fields), detected by name alone. Must be named exactly this in ST.
const WORLD_CREATOR_NAME = 'world-weaver';

function isWorldCreatorActive() {
    const context = SillyTavern.getContext();
    const character = context.characters?.[context.characterId];
    return slugify(character?.name) === WORLD_CREATOR_NAME;
}

function getLastUserMessage(chat) {
    for (let i = chat.length - 1; i >= 0; i--) {
        if (chat[i].is_user) return chat[i].mes;
    }
    return '';
}

// Hardcoded because these enums are NOT exposed via SillyTavern.getContext()
// (verified against public/script.js). If ST ever changes these values this
// will need updating.
// IN_CHAT (1) does NOT merge into a flat Text Completion prompt string --
// confirmed by inspecting the actual outgoing prompt, it merges into a
// structured Chat Completion messages array instead. IN_PROMPT (0) is the
// one that actually shows up in the Text Completion prompt we build here;
// verified directly in SillyTavern's logs.
const EXTENSION_PROMPT_TYPE_IN_PROMPT = 0; // extension_prompt_types.IN_PROMPT
const EXTENSION_PROMPT_ROLE_SYSTEM = 0; // extension_prompt_roles.SYSTEM
const EXTENSION_PROMPT_KEY = 'roleplay-memory';
const EXTENSION_PROMPT_DEPTH = 0;

// Referenced by manifest.json's "generate_interceptor" field. Runs before
// every non-quiet generation. Uses SillyTavern's own setExtensionPrompt API
// (the same mechanism its built-in summarize/memory extension uses) to
// inject relevant memories at a shallow depth in the prompt -- NOT by
// mutating the `chat` array directly. That earlier approach was a bug: a
// hand-rolled message object isn't excluded from the model's own turn the
// way a real extension prompt slot is, which caused the model to treat the
// injected note as the start of its own reply and echo it back verbatim.
// Calling setExtensionPrompt again with the same key also replaces the
// previous value instead of accumulating a new note every turn.
globalThis.roleplayMemoryInterceptor = async function (chat, contextSize, abort, type) {
    if (type === 'quiet') return;

    const context = SillyTavern.getContext();

    if (isWorldCreatorActive()) {
        // World Weaver's job is building a world from a clean slate --
        // injecting memories (especially lore from an unrelated existing
        // world, which could easily be what's bound if the persona itself
        // is bound to one) would contaminate it. No memory context at all
        // for this chat. Explicitly clear the slot rather than just
        // skipping the call -- it isn't per-character, so whatever the
        // last non-World-Weaver character injected would otherwise still
        // be sitting there after switching chats.
        if (typeof context.setExtensionPrompt === 'function') {
            context.setExtensionPrompt(
                EXTENSION_PROMPT_KEY, '', EXTENSION_PROMPT_TYPE_IN_PROMPT, EXTENSION_PROMPT_DEPTH, false, EXTENSION_PROMPT_ROLE_SYSTEM,
            );
        }
        return;
    }

    const query = getLastUserMessage(chat);
    if (!query) return;

    const { userId, agentId } = getIds();
    const world = resolveWorld();

    try {
        const params = new URLSearchParams({ user_id: userId, query });
        if (agentId) params.set('agent_id', agentId);
        if (world) params.set('world', world);
        const response = await fetch(`/api/plugins/roleplay-memory/context?${params}`);
        if (!response.ok) return;
        const data = await response.json();

        const facts = [
            ...(data.shared?.results || []),
            ...(data.character?.results || []),
        ].map((r) => r.memory);
        const lore = (data.world?.results || []).map((r) => r.memory);

        const parts = [];
        if (facts.length > 0) parts.push(`Relevant memories: ${facts.join(' | ')}`);
        // Kept as a distinct labeled line from character/shared memory so
        // the model can tell setting lore apart from relationship history.
        if (lore.length > 0) parts.push(`World lore: ${lore.join(' | ')}`);
        const value = parts.join('\n');

        // Always call this, even with an empty value -- otherwise a turn
        // with no relevant facts would leave the *previous* turn's note
        // still injected, since this slot only updates on an explicit call.
        if (typeof context.setExtensionPrompt !== 'function') {
            console.error('[roleplay-memory] setExtensionPrompt is not available on context:', context);
            return;
        }
        context.setExtensionPrompt(
            EXTENSION_PROMPT_KEY,
            value,
            EXTENSION_PROMPT_TYPE_IN_PROMPT,
            EXTENSION_PROMPT_DEPTH,
            false,
            EXTENSION_PROMPT_ROLE_SYSTEM,
        );
        console.log('[roleplay-memory] setExtensionPrompt called:', { value, depth: EXTENSION_PROMPT_DEPTH });
    } catch (err) {
        console.error('[roleplay-memory] context injection failed:', err);
    }
};

// --- Push direction: batched extraction ---
//
// Rather than sending every single exchange to mem0 immediately, exchanges
// accumulate in `buffer` and get flushed (sent as one batch) when any of:
//   - the buffer's estimated size crosses TOKEN_THRESHOLD
//   - the user explicitly asks to remember something (TRIGGER_PHRASES)
//   - the conversation goes idle for IDLE_MS with nothing flushed yet
//   - the chat/character changes (flush immediately so buffered messages
//     don't get misattributed to whatever character comes next)
//
// Every step that needs an LLM completion (extraction, classification, the
// World Weaver interview) is a build-prompt / apply-result pair against the
// plugin: mem0-service builds the prompt or parses+stores a result, but the
// actual generation happens here, via SillyTavern's own generateRaw() --
// whatever backend/model is actually connected, not a hardcoded client
// inside mem0-service. jsonSchema is deliberately not used: confirmed by
// reading SillyTavern's own source that its jsonSchema extraction path
// (extractJsonFromData in public/script.js) only has a case for
// mainApi 'openai' (i.e. every chat-completion source) -- for
// 'textgenerationwebui' (llama.cpp and every other Text Completion
// backend, this project's own default) it silently returns "{}", discarding
// the real output. Plain generation + mem0-service's own tolerant
// JSON-with-fallback parsing works uniformly across every backend instead.
// trimNames is turned off since a JSON response never has a legitimate
// "CharacterName: " prefix worth stripping.

const TOKEN_THRESHOLD = 800; // approximate (chars / 4) -- not exact tokenization, just a batching heuristic
const IDLE_MS = 2 * 60 * 1000; // flush after 2 minutes of no new exchanges
const TRIGGER_PHRASES = ['remember this', 'remember that', 'memorize this', 'memorize that'];

let buffer = [];
let bufferCharCount = 0;
let idleTimer = null;

function estimateTokens(charCount) {
    return Math.ceil(charCount / 4);
}

function containsTriggerPhrase(text) {
    const lower = text.toLowerCase();
    return TRIGGER_PHRASES.some((phrase) => lower.includes(phrase));
}

async function csrfHeaders() {
    // POST requests need an X-CSRF-Token header or ST's CSRF middleware
    // rejects them with 403, even with a valid session/basic-auth.
    const { token } = await (await fetch('/csrf-token')).json();
    return { 'Content-Type': 'application/json', 'X-CSRF-Token': token };
}

async function postPlugin(path, body) {
    const headers = await csrfHeaders();
    const response = await fetch(`/api/plugins/roleplay-memory${path}`, {
        method: 'POST',
        headers,
        body: JSON.stringify(body),
    });
    if (!response.ok) {
        throw new Error(`${path} failed with status ${response.status}`);
    }
    return response.json();
}

// Generous, explicit response length -- generateRaw() otherwise falls back
// to whatever's configured for ordinary roleplay replies, which can be far
// too small here. The extraction/classification system prompts are large
// (~8,000 tokens), and at least one model this project targets (Gemma 4 E4B
// HauhauCS, see CLAUDE.md) emits a hidden "thinking" block before its real
// reply -- under a tight budget it can burn the whole thing on thinking and
// return empty visible content, which generateRaw() then treats as a hard
// failure ("No message generated") rather than an empty string.
const GENERATION_RESPONSE_LENGTH = 2048;

async function generateFor(systemPrompt, userPrompt) {
    const context = SillyTavern.getContext();
    return context.generateRaw({
        prompt: userPrompt, systemPrompt, trimNames: false, responseLength: GENERATION_RESPONSE_LENGTH,
    });
}

async function flushBuffer() {
    if (idleTimer) {
        clearTimeout(idleTimer);
        idleTimer = null;
    }
    if (buffer.length === 0) return;

    const messages = buffer;
    buffer = [];
    bufferCharCount = 0;

    const { userId, agentId, personaName, characterName } = getIds();
    const world = resolveWorld();
    const isWorldCreator = isWorldCreatorActive();

    try {
        if (isWorldCreator) {
            // The World Creator interview isn't bound to one fixed world the
            // way a roleplay character is -- mem0-service identifies world
            // name(s) from the transcript itself, and this writes
            // world-scoped memory only (no shared/character write).
            const { system_prompt, user_prompt } = await postPlugin('/worlds/interview/prompt', { messages });
            const rawResponse = await generateFor(system_prompt, user_prompt);
            const { written } = await postPlugin('/worlds/interview/apply', { raw_response: rawResponse });
            for (const entry of written || []) {
                if (entry.facts && entry.facts.length > 0) {
                    toastr.success(`World lore updated for ${humanize(entry.world)}`, 'Roleplay Memory');
                }
            }
            return;
        }

        const extractionPrompt = await postPlugin('/extraction/prompt', {
            messages, user_id: userId, agent_id: agentId || undefined,
        });
        const extractionRaw = await generateFor(extractionPrompt.system_prompt, extractionPrompt.user_prompt);
        const stored = await postPlugin('/extraction/store', {
            messages, user_id: userId, agent_id: agentId || undefined, raw_response: extractionRaw,
        });
        const results = stored.results || [];

        if (results.length > 0) {
            const label = agentId && characterName ? `${personaName} & ${characterName}` : personaName;
            toastr.success(`Memory updated for ${label}`, 'Roleplay Memory');
        }

        // Only classify when this write was character-scoped -- a call
        // already targeting the shared bucket has nothing further to route.
        if (agentId && results.length > 0) {
            const characterMemories = results
                .filter((r) => r.id && r.memory)
                .map((r) => ({ id: r.id, memory: r.memory }));
            if (characterMemories.length > 0) {
                const classifyPrompt = await postPlugin('/classification/prompt', {
                    character_memories: characterMemories, world: world || undefined,
                });
                const classifyRaw = await generateFor(classifyPrompt.system_prompt, classifyPrompt.user_prompt);
                const applied = await postPlugin('/classification/apply', {
                    character_memories: characterMemories,
                    world: world || undefined,
                    raw_response: classifyRaw,
                    user_id: userId,
                    agent_id: agentId,
                });
                if (applied.shared_count > 0) {
                    toastr.success(`Shared memory updated for ${personaName}`, 'Roleplay Memory');
                }
                if (applied.world_count > 0) {
                    toastr.success(`World lore updated for ${humanize(world)}`, 'Roleplay Memory');
                }
            }
        }
    } catch (err) {
        console.error('[roleplay-memory] flush failed:', err);
    }
}

function scheduleIdleFlush() {
    if (idleTimer) clearTimeout(idleTimer);
    idleTimer = setTimeout(flushBuffer, IDLE_MS);
}

async function onMessageReceived() {
    const context = SillyTavern.getContext();
    const chat = context.chat;
    if (!chat || chat.length < 2) return;

    const lastAssistant = chat[chat.length - 1];
    if (!lastAssistant || lastAssistant.is_user) return;

    const lastUser = [...chat].reverse().find((m) => m.is_user);
    if (!lastUser) return;

    buffer.push({ role: 'user', content: lastUser.mes });
    buffer.push({ role: 'assistant', content: lastAssistant.mes });
    bufferCharCount += lastUser.mes.length + lastAssistant.mes.length;

    // World Weaver exchanges are low-volume and deliberate -- batching them
    // the same way as ordinary roleplay risks losing an answer if the tab
    // closes before the token threshold or idle timer would have flushed
    // (the buffer only lives in this tab's memory, never persisted), so
    // flush after every single exchange instead of waiting.
    const explicitTrigger = containsTriggerPhrase(lastUser.mes);
    if (isWorldCreatorActive() || explicitTrigger || estimateTokens(bufferCharCount) >= TOKEN_THRESHOLD) {
        await flushBuffer();
    } else {
        scheduleIdleFlush();
    }
}

// --- Lorebook migration: a button in ST's own World Info editor ---
//
// Replaces the old standalone bash script -- reads an existing World Info
// file's entries the same way that script did, but runs each one through
// the same extraction/prompt + generateRaw() + extraction/store cycle
// ordinary roleplay uses (just scoped to the world instead of a
// user/character pair), so it's backend-agnostic too, and gives migration a
// real UI instead of a script needing an exported file path.

async function migrateWorldToMemory() {
    const context = SillyTavern.getContext();
    const worldName = String($('#world_editor_select').find(':selected').text() || '').trim();
    if (!worldName) {
        toastr.warning('Select a world in the editor first.', 'Roleplay Memory');
        return;
    }
    const slug = slugify(worldName);
    if (!slug) return;

    const data = await context.loadWorldInfo(worldName);
    const entries = data?.entries ? Object.values(data.entries) : [];
    const contents = entries.map((e) => e?.content).filter((c) => c && String(c).trim());
    if (contents.length === 0) {
        toastr.info(`No entries with content found in "${worldName}".`, 'Roleplay Memory');
        return;
    }

    let migrated = 0;
    for (const content of contents) {
        migrated += 1;
        toastr.info(`Migrating "${worldName}": entry ${migrated} of ${contents.length}`, 'Roleplay Memory');
        try {
            const { system_prompt, user_prompt } = await postPlugin('/extraction/prompt', {
                messages: [{ role: 'user', content }],
                user_id: WORLD_USER_ID,
                agent_id: slug,
            });
            const rawResponse = await generateFor(system_prompt, user_prompt);
            await postPlugin('/extraction/store', {
                messages: [{ role: 'user', content }],
                user_id: WORLD_USER_ID,
                agent_id: slug,
                raw_response: rawResponse,
            });
        } catch (err) {
            console.error('[roleplay-memory] migration entry failed:', err);
        }
    }

    toastr.success(`World lore updated for ${humanize(slug)}: ${migrated} entries migrated`, 'Roleplay Memory');

    const confirmed = await context.Popup.show.confirm(
        'Migrate to Memory',
        `"${worldName}" has been migrated. Clear its ${contents.length} World Info entries now, so SillyTavern's own keyword matching doesn't inject the same lore a second time?`,
    );
    if (confirmed === context.POPUP_RESULT.AFFIRMATIVE) {
        await context.saveWorldInfo(worldName, { ...data, entries: {} }, true);
        toastr.info(`Cleared entries for "${worldName}".`, 'Roleplay Memory');
    }
}

function injectMigrateButton() {
    if ($('#roleplay_memory_migrate_button').length > 0) return;
    const button = $('<div id="roleplay_memory_migrate_button" class="menu_button fa-solid fa-brain" title="Migrate this World Info to memory" data-i18n="[title]Migrate this World Info to memory"></div>');
    button.on('click', () => migrateWorldToMemory());
    $('#world_popup_delete').after(button);
}

jQuery(async () => {
    const { eventSource, event_types } = SillyTavern.getContext();
    eventSource.on(event_types.MESSAGE_RECEIVED, onMessageReceived);
    eventSource.on(event_types.CHAT_CHANGED, flushBuffer);
    injectMigrateButton();
});
