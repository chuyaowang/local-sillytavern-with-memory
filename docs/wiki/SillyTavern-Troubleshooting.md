# SillyTavern Troubleshooting

Non-default SillyTavern settings and gotchas specific to this project, applied by hand in the UI. These settings live in `sillytavern/data/` and `sillytavern-prod/data/`, which are gitignored, so this page is the only record of why they're set this way. Settings below are applied identically in both the dev (`:8000`) and prod (`:8010`) instances.

## A reply runs past its turn and echoes a fake "next reply" header

**Symptom**: a character's reply ends normally, then continues with something like:

```
...a world fighting against the encroaching darkness."

Seraphina's next reply (in chat format):
```

**Cause**: this project's default model (`gemma4-e4b-hauhaucs`, see [Changing the Model](Changing-the-Model.md)) doesn't always cleanly emit its end-of-turn token at the configured sampling settings. When that happens, SillyTavern keeps streaming whatever the model produces, including a hallucinated continuation that mimics a chat-log formatting artifact from its own fine-tuning data.

**Fix**: User Settings → **Custom Stopping Strings**:

```json
["{{char}}'s next reply (in chat format):"]
```

Leave **Custom Stopping Strings (Macro)** checked (the default) — it substitutes `{{char}}` with the active character's real name at generation time, so this works for every character instead of only the one it was first observed on.

This is a narrow guard against the exact phrasing observed. If the model starts hallucinating a differently-worded continuation, this won't catch it, since a stop string only fires on an exact substring match against what's actually generated.

## Edited the server plugin and nothing changed (or a route 404s)

**Symptom**: you change `sillytavern/plugins/roleplay-memory/index.js`, reload the page, and the new behavior isn't there, or a new route returns 404.

**Cause**: SillyTavern's server plugin only loads its routes once, at container startup. The client extension (`sillytavern/extensions/`) is different — the browser fetches it fresh on a reload. The plugin runs inside the Node process and needs that process restarted to pick up code changes.

**Fix**:

```bash
docker compose restart sillytavern
```

Restart `sillytavern-prod` too if you're running that stack — they share the same plugin source. Confirm it actually reloaded:

```bash
docker logs sillytavern | grep -i plugin
```

should show `Initializing plugin from /home/node/app/plugins/roleplay-memory/index.js`.
