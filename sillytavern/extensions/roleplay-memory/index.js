function slugify(value) {
    if (!value) return null;
    return value.toLowerCase().trim().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '') || null;
}

function getIds() {
    const context = SillyTavern.getContext();
    const character = context.characters?.[context.characterId];
    return {
        userId: slugify(context.name1) || 'default-user',
        agentId: slugify(character?.name),
    };
}

// Reuses ST's own "Primary Lorebook" character binding (the globe icon in
// the character panel, character.data.extensions.world) instead of a
// separate mapping -- confirmed this session that ST never rewrites the
// bound world file on its own, so it's safe to treat this field as pure
// metadata once that file's entries are emptied out. See CLAUDE.md.
function getBoundWorld() {
    const context = SillyTavern.getContext();
    const character = context.characters?.[context.characterId];
    return slugify(character?.data?.extensions?.world);
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

// Roleplay and memory extraction are supposed to always share one model
// (see CLAUDE.md). Sending this with every flush is what makes that
// automatic instead of a manually-maintained convention -- mem0-service
// picks the matching model itself (see mem0-service/main.py's
// memories_by_model) rather than trusting a fixed config value that could
// drift from whatever's actually selected here.
// context.textCompletionSettings / .mainApi verified directly against
// SillyTavern's own source (public/scripts/st-context.js's getContext()),
// same as the extension_prompt_types constants below -- not part of the
// documented extension API.
function getActiveModel() {
    const context = SillyTavern.getContext();
    if (context.mainApi === 'textgenerationwebui' && context.textCompletionSettings?.type === 'llamacpp') {
        return context.textCompletionSettings.llamacpp_model || null;
    }
    return null;
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

    const query = getLastUserMessage(chat);
    if (!query) return;

    const { userId, agentId } = getIds();
    const world = getBoundWorld();
    const context = SillyTavern.getContext();

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

async function flushBuffer() {
    if (idleTimer) {
        clearTimeout(idleTimer);
        idleTimer = null;
    }
    if (buffer.length === 0) return;

    const messages = buffer;
    buffer = [];
    bufferCharCount = 0;

    const { userId, agentId } = getIds();
    const world = getBoundWorld();
    const model = getActiveModel();
    const isWorldCreator = isWorldCreatorActive();

    try {
        // POST requests need an X-CSRF-Token header or ST's CSRF middleware
        // rejects them with 403, even with a valid session/basic-auth.
        const { token } = await (await fetch('/csrf-token')).json();
        if (isWorldCreator) {
            // The World Creator interview isn't bound to one fixed world the
            // way a roleplay character is -- the plugin/mem0-service side
            // identifies world name(s) from the transcript itself, and this
            // writes world-scoped memory only (no shared/character write).
            await fetch('/api/plugins/roleplay-memory/interview', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': token },
                body: JSON.stringify({ messages, model: model || undefined }),
            });
        } else {
            await fetch('/api/plugins/roleplay-memory/add', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': token },
                body: JSON.stringify({
                    messages,
                    user_id: userId,
                    agent_id: agentId || undefined,
                    model: model || undefined,
                    world: world || undefined,
                }),
            });
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

    const explicitTrigger = containsTriggerPhrase(lastUser.mes);
    if (explicitTrigger || estimateTokens(bufferCharCount) >= TOKEN_THRESHOLD) {
        await flushBuffer();
    } else {
        scheduleIdleFlush();
    }
}

jQuery(async () => {
    const { eventSource, event_types } = SillyTavern.getContext();
    eventSource.on(event_types.MESSAGE_RECEIVED, onMessageReceived);
    eventSource.on(event_types.CHAT_CHANGED, flushBuffer);
});