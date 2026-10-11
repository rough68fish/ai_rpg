# Church scene prototype

A text-only RPG scene for testing character agents: one small church, a traveller (you), three AI-driven characters and two the GM runs, all on one local model through Ollama. Each character knows only what it has perceived; the GM sees everything and Python rolls every die.

Design: [framework.md](framework.md). Prompts: [prompts.md](prompts.md).

## Quick start

Needs Python 3.10+ (no packages) and, for the model commands, [Ollama](https://ollama.com) running locally with a model pulled.

```
python play.py                          # play; random dice, default model gemma4:26b
python play.py --seed 3                 # repeatable dice
python play.py --model gemma4:26b       # or set RPG_MODEL; --ollama / OLLAMA_URL for the server
python play.py --debug                  # plain text is just speech; drive each step by hand
python play.py --resume runs/<run>/turn_004.json
python replay.py                        # view the latest run as a chat
```

Type what the traveller does or says in plain text. Each line plays one turn (one minute) and ends with the GM's narration:

```
> I step inside and walk through to the nave.
> I walk up toward the altar, looking for a priest.
> I say hello to the boy and ask if he knows anything about the old relic.

  You greet the small boy in the white altar robe and ask him about the relic,
  but your voice seems to startle him. He pulls his chin into his collar and
  grips a candle taper tightly with both hands. He whispers to you: "I...
  um... I don't know, sir."

> /turn              # wait a minute
> /trace             # everything that happened behind that narration
> /quit
```

Then `python replay.py` to read it back. `/commands` still drive each step by hand. The ones that don't call the model (`/say`, `/do`, `/move`, `/mind` and the rest) work without Ollama:

```
/move player sacristy
/try I ask the old priest straight out where the relic of St. Aurel is kept
/act aldric        # he answers, or deflects
/act tomas         # Tomas reacts to what he overheard
/resolve           # their actions, plus Agnes and Martha
/next              # advance one minute and save
/truth             # what is actually true
```

## Commands

| Command | What it does |
|---|---|
| *plain text* | Play a full turn (with `--debug`: the traveller just says it). |
| `/turn [text]` | Play a full turn; with no text, the traveller waits. |
| `/trace` | Full debug output of the last turn. |
| `/verbose` | Show every step of a turn as it happens (toggle). |
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

`replay.py` writes `replay.html` into the run folder: a chat view of what the traveller experienced. For played turns, what you typed is on the right and the GM's narration on the left. For turns driven by hand, the left shows what the traveller heard and saw, captioned with how they know the speaker. Turn dividers and room notes mark time and place. Toggles show the dice rolls, the raw events behind each narrated turn, and, as faded bubbles, the events the traveller didn't perceive.

## Layout

```
play.py              entry point
replay.py            a run as a chat-style HTML page
rpg/state.py         load/save, lookups, objects, d20 roll-under
rpg/perception.py    who perceives an event, first-person rendering, memory writes
rpg/prompts.py       character prompts from their own record, leakage check
rpg/llm.py           Ollama /api/chat client (schema-constrained JSON replies)
rpg/gm.py            GM resolve and narrate: prompts, branch-and-roll, validated state changes
rpg/modes.py         promotion, demotion, memory summaries
rpg/repl.py          the REPL
scene/               starting state (world.json, characters/*.json)
framework.md         design: scene, perception rules, turn loop
prompts.md           GM and character prompts with schemas
```

## How it works

**Perception (code, no model).** Same room: you see actions and hear normal speech. Adjacent room: only loud speech and noises. Whispers reach the target; others need a WIS check. Subtle actions need a WIS check to notice. Martha's poor hearing adds a penalty to every hearing roll. Each perceiver gets a first-person line in their memory, naming people the way they know them ("a travel-worn stranger" until introduced).

**Characters (`/act`).** A character's prompt holds only its own record and memories, never stats, world truth or other characters' secrets. The leakage check blocks the call if any of those slip in. The reply is constrained to a JSON schema. `speech` is spoken straight away; the free-text `target` ("Father Tomas", "the stranger") is matched to an id. `action` waits for the GM. `thought` and `mood` go into the character's memory, and belief and goal edits are applied. "What just happened" is every memory added since the character's last `/act`.

**GM (`/try`, `/resolve`).** The GM sees everything. For anything uncertain it names a check and writes both a success and a failure branch; Python rolls d20 under ability + modifier, keeps one branch, runs perception on it, then applies its `state_changes`. Each change (`move`, `give`, `drop`, `unlock`/`lock`, `reveal`, `know`) is validated against the world, e.g. the crypt door only unlocks for whoever holds the key, and anything invalid is rejected and logged. Names the GM writes out are turned back into `{id}` so observers still only see names they know.

**A turn (plain text or `/turn`).** These are the steps of the turn loop in framework.md:

1. The GM resolves what the traveller typed. Any GM-managed character the traveller's action targets or names, and who perceived it, is promoted to an agent (see below).
2. Every agent with news acts, one after another. News means it perceived something from someone else since it last acted, and an agent with no news still acts every 3rd turn. Agents in the traveller's room go first, so a reply can answer what was just said.
3. The GM resolves their actions and ticks Agnes and Martha in the same call. A managed character who was spoken to replies.
4. Agents with no contact with the traveller for 5 turns are demoted to GM-managed. Contact runs both ways: they perceived the traveller, or the traveller perceived them.
5. The GM narrates, in second person, only the lines the traveller perceived this turn. The narration is checked for leaked truth.
6. The clock advances and a snapshot is saved, with the input, narration and any promotions or demotions in `turns`.

A turn takes about 10–20 s. Plain text shows only progress and the narration; `/trace` or `/verbose` shows the rest.

**Promotion and demotion (`rpg/modes.py`).** Every character has the same record in both modes, so promotion just switches who drives it. Agnes's memory of the silver box is in her record from the start, not supplied by the GM. Both switches compress the memories gathered since the last summary:

- Memories with importance 7 or more are kept word for word by code.
- The model merges the rest into at most 3 first-person lines and can add new beliefs or goals. This call sees only the character's own record and passes the leakage check.
- At promotion, memories from the current turn stay unread, so the character's first prompt still shows the traveller's question under "What just happened".

`/promote` and `/demote` run the same switch by hand.

**Learning names.** `know A B` is only accepted when B's name (full, first or surname) is in the speech of that event. The traveller has no modelled name, so any line they speak counts as an introduction. Speech whose target the GM left empty goes to the one person the event text names, if there is exactly one.

**Model settings.** `think: false` on every call: with thinking on, Gemma 4 spent about 900 hidden tokens per call. Temperature is 0.8 for characters, 0.4 for the GM, with `num_ctx` 8192 and `keep_alive` 30m. Once the model is loaded, a character call takes about 1.5 s and a GM call 2–8 s.

## Status

| Stage | | |
|---|---|---|
| 1 | built | State, map, perception, debug REPL |
| 2 | built | Character calls (`/act`) |
| 3 | built | GM `resolve` with branch-and-roll (`/try`, `/resolve`), replay viewer |
| 4 | built | Full turn loop with GM `narrate` (plain text / `/turn`), narrated replays |
| 5 | built | Promotion when the traveller engages a managed character, demotion after 5 turns without contact, memory summaries |

All five stages from framework.md's build order are in.

Known rough edges:

- The GM sometimes still writes another agent's reaction into an event ("...but {aldric} reacts with suspicion"), and occasionally calls for a check on plain talking.
- Introductions don't rename the traveller: telling Pip "I'm Will" is just speech, and nobody calls the traveller Will afterwards.
- First-person rewriting only handles a sentence-initial subject; "...but me reacts" can still appear.
- A subtle success still gets perception rolls, so the victim of a successful pickpocket can notice it anyway.
- Agnes is guarded by design. In testing she brushed off relic questions and steered Martha away from gossip. Getting the silver-box story out of her takes real persuasion, and nothing yet rewards earning her trust over several turns.
- Demotion on leaving the building isn't implemented: the map has no street exit.
- A promoted character with news acts every turn, even far from the traveller, until it's demoted. Agnes kept reacting to Martha's dusting for 5 turns after the traveller left.
- Narration sometimes pads a quiet turn with invented detail ("you sit in the nave") or rewords what was said.
- The default model is `gemma4:26b`; framework.md was planned around Gemma 4 31B.
