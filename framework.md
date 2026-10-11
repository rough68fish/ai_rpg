# Church scene prototype: framework

A text-only prototype of one scene, built to test the character-agent design (in a separate design doc, not in this repo) before anything bigger. Python, the Ollama HTTP API, JSON files for state, one shared local model for every role.

Prompts are in [prompts.md](prompts.md). Starting state is in `modules/St_Aurel_1100/` (and the 1989 version in `modules/St_Aurel_1989/`).

## The scene

St. Aurel's, a small stone church in the village of Aurelsford, in a D&D-style medieval world around the year 1100, about forty minutes before the bell for morning mass. The player is a traveller who walks in through the front door. Rumor says the church hides a relic.

**Ground truth (GM only):** the relic is real. It is the Reliquary of St. Aurel, a palm-sized silver box holding a knuckle bone, hidden behind a loose stone in the crypt wall directly under the altar. The crypt is reached by a locked stair door in the sacristy. Father Aldric has the only key, on a cord around his neck.

| Character | Mode | What they know or believe |
|---|---|---|
| Father Aldric, senior priest | agent | Knows exactly where the relic is. Has sworn to keep it hidden. |
| Father Tomas, young curate | agent | Has heard the rumor, believes it, quietly wants to find it. Suspects Aldric knows. |
| Pip, altar boy (11) | agent | Once saw Aldric come up the crypt stairs late at night, dusty. Thinks the crypt is haunted. |
| Agnes, cleaner (70s) | GM-managed | Forty years ago saw the old priest carry a small silver box downstairs. Never told anyone. |
| Martha, cleaner (60s) | GM-managed | Knows every rumor in town, most of them wrong. Hard of hearing. |

Agnes is the hidden hook: if the player chats up the cleaners, she gets promoted and the player can learn something the priests won't say.

## Map and perception

Five rooms, each with a list of neighbours. Perception is computed by code, not by the model.

```
narthex ── nave ── sanctuary ── sacristy ── (locked) crypt
```

- Same room: you see actions and hear normal speech.
- Adjacent room: you hear `loud` speech and loud noises, but don't see who.
- Whispers: only the target hears them, plus anyone in the room who passes a WIS check.
- Subtle actions (pocketing something, trying a door quietly) carry a `subtle` flag: others in the room only notice on a WIS check.

Martha's hearing is a trait plus a penalty the engine applies to her hearing checks.

## Files

```
ai_rpg/
  framework.md        this file
  prompts.md          GM and character prompts
  modules/St_Aurel_1100/
    module.json       title and description
    world.json        setting, rooms, objects, ground truth, clock
    characters/       one record per character (agent or GM-managed)
      aldric.json  tomas.json  pip.json  agnes.json  martha.json
  runs/<module>-<timestamp>/   created per playthrough: event log + state snapshots
```

One record per character, used in both modes, as the design doc requires. Fields:

- `identity`: name, age, role, personality, voice, secrets, relationships (static, goes in every prompt)
- `self_image` (optional): `{"STR": "your knees ache and you move slowly", ...}`, the author's own wording for how the character sees an ability
- `traits`: other plain-language lines about the character ("you love to talk")
- `stats`: STR INT WIS DEX CON CHA. **Never** sent to a character prompt.

Characters know themselves in plain words, not numbers. `rpg/selfimage.py` turns every ability outside the average band (9-12) into an "About yourself" line ("you are a bit clumsy", "you are sharp and learn quickly"), using the basic D&D bands 3 / 4-5 / 6-8 / 13-15 / 16-17 / 18. A `self_image` entry replaces the default line for that ability, and is shown even when the score is average. `traits` follow after.
- `mode`: `agent` or `managed`
- `location`
- `beliefs`, `goals`: short lists the character can edit through its output
- `memory`: list of `{t, text, importance}`. Only things this character perceived.

## Turn loop

One turn is roughly one minute of game time.

1. **Player input.** Free text, e.g. `I walk up the aisle and ask the young priest if the church has any old treasures.`
2. **Resolve the player** (GM call: `resolve`). The GM turns the intent into events. If a check is needed it writes a *success branch and a failure branch* and names the stat and difficulty. Python rolls the d20 and keeps one branch. The model never sees or fakes a die roll.
3. **Perceive.** Python works out who perceived each event (rooms, volume, `subtle` + WIS checks) and appends a first-person line to each perceiver's `memory`. The player is filtered the same way.
4. **Promote or demote.** If an event targeted a managed character with player involvement (spoken to, followed, handed something), promote them: switch `mode` to `agent`. Demote an agent after 5 turns with no player contact or when the player leaves the building; run the `demote` GM call to summarise its recent memories back into the record.
5. **Characters act.** Every agent in the scene gets one call (`character` prompt) with only its own record and its new perceptions. It returns an intent, speech and private updates. Calls run one after another to start; see Performance.
6. **Resolve the characters** (GM call: `resolve`, all intents in one batch) and **tick the managed characters** in the same call. Same branch-and-roll trick, then perceive again.
7. **Narrate** (GM call: `narrate`). Prose for the player built only from events the player perceived this turn.
8. **Save.** Write the event log and all records to `runs/<timestamp>/`. Every turn is replayable and diffable, which is the main debugging tool.

So a turn is 3 GM calls + one call per active agent: 6 calls with three agents.

## Dice

Old-school roll-under: roll d20, succeed if the roll is at or under the stat plus a modifier. The GM picks the stat and a modifier from `+4` (easy) to `-6` (very hard), or says no check is needed. Python does the roll and records it in the event log, which the GM can see on the next turn but the characters never do.

## Ollama setup

- Model: the one shared model from the model thread (Gemma 4 31B), already loaded; set `keep_alive: "30m"` so it never unloads between calls.
- Use `/api/chat` with `format` set to the JSON schema for that call. Ollama constrains the output to the schema, so parsing failures mostly go away.
- `num_ctx: 8192` to start. Everything in this scene fits comfortably.
- Temperature: `0.4` for `resolve` (consistency), `0.8` for characters and `narrate` (voice).
- Put the static part (system prompt, identity sheet) first and the changing part last in each call, so prompts stay short to diff and the static prefix can be reused.

## Performance

With a 31B model on a 3090, each call is a few seconds, so a turn is likely somewhere around 20 to 40 seconds sequentially. Fine for a prototype. Easy wins later:

- Skip characters who perceived nothing new and have no pending goal (Aldric in the sacristy doesn't need a call when the player is in the narthex). This is the director's "who acts" step, done as a code rule first.
- Merge `narrate` into `resolve` if narration quality holds up.
- `OLLAMA_NUM_PARALLEL=2` to run two character calls at once, if VRAM allows the extra context.

## Debug commands

Worth building into the REPL from the start:

- `/who` show every character's location and mode
- `/mind <name>` print a character's memory, beliefs and goals (what they *think*)
- `/truth` print world.json (what *is*)
- `/log` last turn's events with rolls
- `/promote <name>`, `/demote <name>` force a mode switch

Being able to compare `/mind tomas` against `/truth` is how you check perception filtering and leakage actually work.

## What to test with this scene

1. **Leakage:** do Tomas or Pip ever act as if they know where the relic is? They shouldn't.
2. **Secrets under pressure:** does Aldric deflect a direct question, and does a CHA-driven lie succeed or fail believably?
3. **Promotion:** talk to Agnes. Does she come alive with only what she witnessed, including the silver box memory?
4. **Overhearing:** have Tomas say something loud in the sacristy. Does Pip in the sanctuary hear it, and do the cleaners in the nave not?
5. **Stat-shaped behaviour:** does Pip talk like an 11-year-old, and does Martha mishear things?

## Build order

1. State loading, room map, perception function, REPL with debug commands. No model calls yet; hand-type events.
2. Character call with one agent (Tomas) and the player. Check voice and leakage.
3. GM `resolve` with branch-and-roll.
4. All three agents plus `narrate`.
5. Managed characters, promotion and demotion.
