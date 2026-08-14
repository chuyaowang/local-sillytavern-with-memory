# SillyTavern settings changes

Non-default settings applied by hand in the ST UI (not tracked anywhere else,
since `sillytavern/data/` and `sillytavern-prod/data/` are gitignored runtime
state — this doc is the only record of *why* they're set this way). Applied
identically in both the dev (`:8000`) and prod (`:8010`) instances.

## Custom Stopping Strings (2026-08-14)

**Setting**: User Settings → Custom Stopping Strings

```json
["{{char}}'s next reply (in chat format):"]
```

`Custom Stopping Strings (Macro)` left checked (the default) so `{{char}}`
resolves to the active character's actual name at generation time — this
generalizes across all characters instead of hardcoding one name.

**Why**: With `gemma4-e4b-hauhaucs` (the fine-tune this project standardized
on — see CLAUDE.md), replies occasionally ran past their actual turn and
appended a hallucinated continuation, e.g.:

```
...a world fighting against the encroaching darkness."

Seraphina's next reply (in chat format):
```

That exact phrase doesn't appear anywhere in ST's config, templates, or
source (checked — no match in the active instruct/context/sysprompt
templates or in ST's own source inside the container), so it isn't something
ST inserts. It's the model itself: prod's connection has instruct mode
disabled (`power_user.instruct.enabled: false`) and
`textgenerationwebui_settings.stopping_strings` empty, so ST streams back
raw model output and appends it verbatim until the model's own EOS/end-of-turn
token fires or `amount_gen` is hit. This fine-tune doesn't always emit that
token cleanly at the configured sampling settings (temp 1.0 / top_p 0.95 /
top_k 64 — fairly loose), and once generation runs past the real reply it
starts reproducing what looks like a chat-log formatting artifact from its
own RP-log-style fine-tuning data — the same class of unreliability already
noted for this model in CLAUDE.md's "Lessons learned" (e.g. the
`reasoning_content` token-budget issue).

`custom_stopping_strings` was picked over the Text Completion connection's
own `stopping_strings` field because `getStoppingStrings()` in ST's source
(`public/script.js`) always includes it regardless of API type or whether
instruct mode is on — confirmed by reading the function directly rather than
assuming — so it's the one mechanism guaranteed to apply here.

**Verified**: `power_user.custom_stopping_strings` present with the value
above and `custom_stopping_strings_macro: true` in both
`sillytavern/data/default-user/settings.json` and
`sillytavern-prod/data/default-user/settings.json`.