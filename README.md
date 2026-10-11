# Church scene prototype

A text-only RPG scene for testing character agents: one small church, a traveller (you), three AI-driven characters and two the GM runs, all on one local model through Ollama. Each character knows only what it has perceived; the GM sees everything and Python rolls every die.

Design: [framework.md](framework.md). Prompts: [prompts.md](prompts.md).

## Quick start

Needs Python 3.10+ (no packages) and, for the model commands, [Ollama](https://ollama.com) running locally with a model pulled.

```
python play.py                          # random dice, default model gemma4:26b
python play.py --seed 3                 # repeatable dice
python play.py --model gemma4:26b       # or set RPG_MODEL; --ollama / OLLAMA_URL for the server
python play.py --resume runs/<run>/turn_004.json
python replay.py                        # view the latest run as a chat
```

Plain text means the traveller speaks. Everything else is a `/command`. The commands that don't call the model (`/say`, `/do`, `/move`, `/mind` and the rest) work without Ollama.

A short session using the model:

```
/move player nave
/move player sanctuary
/move player sacristy
/try I ask the old priest straight out where the relic of St. Aurel is kept
/act aldric        # he answers, or deflects
/act tomas         # Tomas reacts to what he overheard
/resolve           # their actions, plus Agnes and Martha
/next              # advance one minute and save
/try I quietly try to lift the key from around the old priest's neck
/truth             # did the key move?
/quit
```

Then `python replay.py` to read it back.

## Commands

| Command | What it does |
|---|---|
| *plain text* | The traveller says it out loud (normal volume). |
| `/try <text>` | The traveller attempts something; the GM resolves it (model). |
| `/act <who>` | Sends that character's prompt to the model. Speech is spoken at once; the action becomes a pending intent. |
| `/resolve` | The GM resolves all pending intents and ticks the managed characters (model). |
| `/pending` | Intents waiting for `/resolve`. |
| `/say <who> [whisper\|loud] [@target] <words>` | Puppet someone's speech by hand. |
| `/do <who> [subtle] [loud] [@target] <text>` | Puppet an action; write people as `{id}`, e.g. `{pip} drops a candle`. |
| `/move <who> <room>` | Move to an adjacent room; locked doors stop you. |
| `/next` | Advance the clock one turn (one minute) and save a snapshot. |
| `/look [who]`, `/who` | A room as someone sees it; everyone's location and mode. |
| `/mind <who>` | Memory, beliefs and goals: what they *think*. |
| `/truth` | Ground truth, objects and doors: what *is*. |
| `/log [n]` | Last n events with perception rolls, GM checks and state changes. |
| `/prompt <who>` | The exact prompt `/act` would send, with the leakage check. |
| `/roll <who> <STAT> [mod]` | Roll d20 under a stat by hand. |
| `/know <who> <whom>` | `<who>` learns `<whom>`'s name. |
| `/promote <who>`, `/demote <who>` | Switch between agent and GM-managed. |
| `/save`, `/help`, `/quit` | |

## Replays

Every `/next` (and `/quit`) saves the full state to `runs/<timestamp>/turn_NNN.json`, so you can diff turns or resume from one. `runs/` is git-ignored.

```
python replay.py                          # latest run, opens in the browser
python replay.py runs/20261010-215635     # a run folder
python replay.py runs/<run>/turn_002.json -o out.html --no-open
```

`replay.py` writes `replay.html` into the run folder: a chat view of what the traveller experienced. The traveller's speech and actions are on the right. What they heard and saw is on the left, captioned with how they know the speaker. Turn dividers and room descriptions mark time and place. Toggles show the dice rolls and, as faded bubbles, the events the traveller didn't perceive.

## Layout

```
play.py              entry point
replay.py            a run as a chat-style HTML page
rpg/state.py         load/save, lookups, objects, d20 roll-under
rpg/perception.py    who perceives an event, first-person rendering, memory writes
rpg/prompts.py       character prompts from their own record, leakage check
rpg/llm.py           Ollama /api/chat client (schema-constrained JSON replies)
rpg/gm.py            GM resolve: prompt, branch-and-roll, validated state changes
rpg/repl.py          the REPL
scene/               starting state (world.json, characters/*.json)
framework.md         design: scene, perception rules, turn loop
prompts.md           GM and character prompts with schemas
```

## How it works

**Perception (code, no model).** Same room: you see actions and hear normal speech. Adjacent room: only loud speech and noises. Whispers reach the target; others need a WIS check. Subtle actions need a WIS check to notice. Martha's poor hearing adds a penalty to every hearing roll. Each perceiver gets a first-person line in their memory, naming people the way they know them ("a travel-worn stranger" until introduced).

**Characters (`/act`).** A character's prompt holds only its own record and memories, never stats, world truth or other characters' secrets. The leakage check blocks the call if any of those slip in. The reply is constrained to a JSON schema. `speech` is spoken straight away; the free-text `target` ("Father Tomas", "the stranger") is matched to an id. `action` waits for the GM. `thought` and `mood` go into the character's memory, and belief and goal edits are applied. "What just happened" is every memory added since the character's last `/act`.

**GM (`/try`, `/resolve`).** The GM sees everything. For anything uncertain it names a check and writes both a success and a failure branch; Python rolls d20 under ability + modifier, keeps one branch, runs perception on it, then applies its `state_changes`. Each change (`move`, `give`, `drop`, `unlock`/`lock`, `reveal`, `know`) is validated against the world, e.g. the crypt door only unlocks for whoever holds the key, and anything invalid is rejected and logged. Names the GM writes out are turned back into `{id}` so observers still only see names they know.

**Model settings.** `think: false` on every call: with thinking on, Gemma 4 spent about 900 hidden tokens per call. Temperature is 0.8 for characters, 0.4 for the GM, with `num_ctx` 8192 and `keep_alive` 30m. Once the model is loaded, a character call takes about 1.5 s and a GM call 2–8 s.

## Status

| Stage | | |
|---|---|---|
| 1 | built | State, map, perception, debug REPL |
| 2 | built | Character calls (`/act`) |
| 3 | built | GM `resolve` with branch-and-roll (`/try`, `/resolve`), replay viewer |
| 4 | next | `/turn`: the full loop with all agents plus GM `narrate`, skipping agents with nothing new to react to |
| 5 | | Managed characters, promotion and demotion with memory summaries |

Known rough edges:

- The GM sometimes still writes another agent's reaction into an event ("...but {aldric} reacts with suspicion"), and occasionally calls for a check on plain talking.
- Introductions don't rename the traveller: telling Pip "I'm Will" is just speech, and nobody calls the traveller Will afterwards.
- First-person rewriting only handles a sentence-initial subject; "...but me reacts" can still appear.
- A subtle success still gets perception rolls, so the victim of a successful pickpocket can notice it anyway.
- The default model is `gemma4:26b`; framework.md was planned around Gemma 4 31B.
