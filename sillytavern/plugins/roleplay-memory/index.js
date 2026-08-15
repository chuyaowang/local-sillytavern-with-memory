const MEM0_URL = process.env.MEM0_URL || 'http://mem0:8001';

async function init(router) {
    router.get('/context', async (req, res) => {
        const { user_id, agent_id, query, world } = req.query;
        if (!user_id || !query) {
            return res.status(400).json({ error: 'user_id and query are required' });
        }
        try {
            const params = new URLSearchParams({ user_id, query });
            if (agent_id) params.set('agent_id', agent_id);
            if (world) params.set('world', world);
            const response = await fetch(`${MEM0_URL}/memories/context?${params}`);
            const data = await response.json();
            res.status(response.status).json(data);
        } catch (err) {
            console.error('[roleplay-memory] context request failed:', err);
            res.status(502).json({ error: 'mem0 service unreachable' });
        }
    });

    // Every LLM-needing step is a build-prompt / apply-result pair: mem0
    // builds the prompt (or parses+stores a result), but the actual
    // generation happens client-side via the extension's generateRaw()
    // call, using whatever backend/model SillyTavern is actually connected
    // to -- not a hardcoded llama.cpp client inside mem0-service. This
    // plugin only relays each leg to mem0-service, same thin
    // fetch-and-relay pattern as /context above.
    const relay = (path) => async (req, res) => {
        try {
            const response = await fetch(`${MEM0_URL}${path}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(req.body),
            });
            const data = await response.json();
            res.status(response.status).json(data);
        } catch (err) {
            console.error(`[roleplay-memory] ${path} request failed:`, err);
            res.status(502).json({ error: 'mem0 service unreachable' });
        }
    };

    router.post('/extraction/prompt', relay('/extraction/prompt'));
    router.post('/extraction/store', relay('/extraction/store'));
    router.post('/classification/prompt', relay('/classification/prompt'));
    router.post('/classification/apply', relay('/classification/apply'));
    router.post('/worlds/interview/prompt', relay('/worlds/interview/prompt'));
    router.post('/worlds/interview/apply', relay('/worlds/interview/apply'));
}

async function exit() {}

module.exports = {
    init,
    exit,
    info: {
        id: 'roleplay-memory',
        name: 'Roleplay Memory Bridge',
        description: 'Bridges SillyTavern to the local mem0 memory service over the internal Docker network.',
    },
};