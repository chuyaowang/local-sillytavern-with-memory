# Configuring the Memory System

This covers turning the memory system on and shaping how it behaves: activating the SillyTavern extension, binding a character or persona to a world, and understanding what actually triggers a memory to be written. This assumes [Installing the Memory System](Installing-the-Memory-System.md) (or [Local-Only Setup](Local-Only-Setup.md)) is already done.

## Activating the extension

In SillyTavern: Manage Extensions tab on the top band → enable **Roleplay Memory**. Both directions — pulling relevant memories into a reply, and pushing new ones out to be remembered — go through this one extension.

## Binding a world

World lore (see [Memory System](Memory-System.md#world-lore)) only applies to a conversation once a world is actually bound to it:

- A **character** picks up a world through its own card: the globe icon in SillyTavern's character panel, its "Primary Lorebook" picker.
- A **persona** can also bind to a world independently, through the persona panel's own lorebook picker.
- If both are set, the persona's binding takes priority. If neither is set (not recommended), memories from that conversation are not tied to any world.

Whichever World Info file gets bound this way should stay empty of its own entries. SillyTavern's native keyword-matching still runs on a bound World Info file regardless of this memory system, so leaving entries in it means the same lore can get injected twice: once from mem0, once from SillyTavern's own matching. Keeping the binding but clearing its entries avoids that. See [Managing Memories](Managing-Memories.md#migrating-an-existing-lorebook) for moving existing entries into mem0 first if there are any.

## What triggers automatic extraction

Extractions are not sent off one at a time. SillyTavern's extension collects them in a buffer and sends the whole batch together as soon as any of these happens, whichever comes first:

- The buffer's estimated size reaches about 800 tokens, estimated from character count rather than exact tokenization.
- Two minutes pass with no new exchange added to the buffer.
- The user says something like "remember this" or "memorize that," flushing right away.
- The chat or character changes, flushing right away so buffered exchanges do not end up attributed to whatever character comes next.

None of these thresholds are currently user-configurable from SillyTavern's settings. They are fixed behavior built into the extension.
