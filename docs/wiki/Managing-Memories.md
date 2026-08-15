# Managing Memories

Memories build up automatically during ordinary roleplay, but they can also be browsed, edited, moved, or deleted by hand, and world lore can be built deliberately instead of picked up passively. This page covers the concepts needed to do that — the three scopes a memory can belong to — and the actual tools: the admin UI, World Weaver, and the lorebook migration button.

The examples below use two people, Nova and Amber, two characters, Seraphina and Arthuria, and two worlds, Eldoria and Camelot, to make the scoping concrete. Nova roleplays with Seraphina, who is bound to Eldoria. Amber roleplays with Arthuria, who is bound to Camelot.

## The three memory scopes

Every memory belongs to one of three scopes: character, shared, or world lore.

| Scope | Example fact | Belongs to | Visible to |
| --- | --- | --- | --- |
| **Character** | "Seraphina promised to protect Nova through the night" | Nova | only Nova, only in conversations with Seraphina |
| **Shared** | "Nova works as a veterinarian" | Nova | every character Nova talks to (optionally limited to one world, see below) |
| **World lore** | "Eldoria's forest was corrupted by the Shadowfangs" | no one in particular | anyone talking about Eldoria, Nova or Amber alike |

<p align="center">
  <img src="diagrams/memory-system-1.svg" alt="Which scopes are visible in each conversation" width="600">
</p>

Nova, in a conversation with Seraphina in the Eldoria world, has access to memories about herself in Eldoria, her interaction with Seraphina in Eldoria, and the world Eldoria. Similarly, Amber's interactions with Arthuria are memorized for the Camelot world.

When Nova talks to Cynthia in the Eldoria world, their conversation does not have access to the Seraphina memory.

When Amber talks to Seraphina in the Eldoria world, their conversation only has access to the Eldoria world memory.

Note: here we assume each character, like Seraphina, is bound to only one world, while a user, like Nova, can change between worlds.

## Building lore, two ways

- **Passively**, during ordinary roleplay: facts about the world get picked out alongside the usual shared/character memory extraction (see [Memory System's "Sorting a memory" section](Memory-System.md#sorting-a-memory-into-shared-or-world-lore)), for whichever world is currently bound. This is how Eldoria's lore built up as Nova talked to Seraphina.
- **Actively**, with a dedicated interviewer character: import `sillytavern/character-cards/world-weaver.json` into SillyTavern, keeping the name exactly **World Weaver**, and talk to it like any other character. It asks about a world one question at a time and writes straight into that world's lore. This is how Camelot's lore could be built from scratch before Amber ever starts roleplaying with Arthuria. Because it writes into the same store the passive path reads from, a lorebook built this way is available immediately in a fresh roleplay session bound to that world. It never pulls in memory context for its own replies, so building a new world stays a clean slate, and every exchange with it is saved right away.

## Migrating an existing lorebook

A world that already has lore in SillyTavern's native World Info format does not need to be re-typed through World Weaver. Open that world in SillyTavern's own World Info editor and click the brain icon next to the delete button in its toolbar. It reads every entry in that world and runs each one through the same extraction step as ordinary roleplay, writing the results straight into that world's lore, then offers to clear the World Info file's entries once it is done (see [Configuring the Memory System's "Binding a world" section](Configuring-the-Memory-System.md#binding-a-world) for why that matters).

This is a one-time backfill, not something to repeat after every change, and it can take a while for a large world, since each entry runs through a real extraction step, one at a time.

## Browsing and editing memories

The memory manager is a small admin page at `http://localhost:8001/ui/`, local only — not reachable over Tailscale, by design (see [Remote Access](Remote-Access.md)).

1. Pick a `user_id` from the first dropdown — every memory belongs to one, including world lore, which is filed under a fixed placeholder user called `world` instead of a real person.
2. Pick an `agent_id` to narrow to one scope (a character, `shared`, or a world name), or leave it on "All scopes" to see everything for that user_id at once.
3. Optionally type a search — leaving it blank lists every memory in the selected scope; typing something searches by embedding closeness instead of exact text.
4. Click **Load**.

Each card shows the memory's text alongside its scope, linked user, and world tag if it has one. **Edit** unlocks the text for hand-editing — **Save** stores the new wording and updates how it is found by search. **Delete** removes it permanently, with a confirmation prompt first.

## Moving a memory to a different scope

**Move** reassigns which user, character, or world a memory is filed under, without deleting and re-extracting it — useful for fixing a memory that landed in the wrong scope, or reattributing something after the fact. Clicking it reveals two dropdowns, populated from every user and character/world that already exists anywhere in the store, not just combinations that are already paired together, since the point of a move is often creating one that does not exist yet. Pick the target and confirm — the memory's text and how it is found by search stay exactly the same; only its labels change.

## Bulk find-and-replace

Below the memory list, **Replace All** does a plain, case-sensitive, literal text substitution across every memory in the currently-selected scope (or every scope for that user_id, if "All scopes" is selected). It only touches memories that literally contain the search text — a paraphrased mention elsewhere will not be caught. Useful for a name change or correcting a consistent typo across many memories at once; each affected memory gets its search representation updated the same way a hand edit does.
