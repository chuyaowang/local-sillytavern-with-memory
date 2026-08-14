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

    router.post('/add', async (req, res) => {
        try {
            const response = await fetch(`${MEM0_URL}/memories`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(req.body),
            });
            const data = await response.json();
            res.status(response.status).json(data);
        } catch (err) {
            console.error('[roleplay-memory] add request failed:', err);
            res.status(502).json({ error: 'mem0 service unreachable' });
        }
    });

    router.post('/interview', async (req, res) => {
        try {
            const response = await fetch(`${MEM0_URL}/worlds/interview`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(req.body),
            });
            const data = await response.json();
            res.status(response.status).json(data);
        } catch (err) {
            console.error('[roleplay-memory] interview request failed:', err);
            res.status(502).json({ error: 'mem0 service unreachable' });
        }
    });
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