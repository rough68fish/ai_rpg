# Church scene prototype

Design: [framework.md](framework.md). Prompts: [prompts.md](prompts.md).

## Stage 1 (built): state, map, perception, debug REPL

No dependencies beyond Python 3.10+. Run from this folder:

```
python play.py            # random dice
python play.py --seed 3   # repeatable dice
python play.py --resume runs/<run>/turn_004.json
```

You play the traveller (plain text = you speak) and puppet everyone else with commands. `/help` lists them. A tour that exercises the rules:

```
/move player nave
Hello? Is anyone here?
/say martha @player Oh hello dear, are you here for mass?
/say tomas loud Pip! Have you lit the candles yet?      # Pip hears it through the wall
/do tomas subtle {tomas} slips the parish register under his arm   # Aldric rolls WIS to notice
/move tomas crypt                                     # locked
/next
/mind martha       # what she thinks happened (her hearing rolls can fail)
/prompt pip        # the exact prompt /act sends, plus a leakage check
/log
/truth
```

Every `/next` saves a snapshot to `runs/<timestamp>/`, so you can diff turns or resume one.

## Layout

```
play.py              entry point
rpg/state.py         load/save, lookups, d20 roll-under
rpg/perception.py    who perceives an event, writes it into their memory
rpg/prompts.py       builds character prompts from their own record, leakage check
rpg/llm.py           Ollama /api/chat client (schema-constrained JSON replies)
rpg/repl.py          the debug REPL
scene/               starting state (world.json, characters/*.json)
```

## Stage 2 (built): character calls

Needs [Ollama](https://ollama.com) running locally with the model pulled. Default model is `gemma4:26b`; change it with `--model` or the `RPG_MODEL` environment variable, and the server with `--ollama` or `OLLAMA_URL`.

```
python play.py --model gemma4:26b
```

`/act <who>` sends that character's prompt (the one `/prompt` shows) to the model, after the leakage check passes. From the reply:

- `speech` is spoken straight away as an event, so others perceive it by the normal rules. The model's free-text `target` ("Father Tomas", "the stranger") is matched to an id.
- `action` is stored as a pending intent (`/pending`). Nothing resolves it until the GM in stage 3.
- `thought` and `mood` go into the character's own memory; belief and goal edits are applied to the record.

A character's "What just happened" is every memory added since its last `/act` (tracked by `memories_seen` in its record).

Calls use `think: false`, which matters for thinking models such as Gemma 4: with thinking on, a call spent ~900 hidden tokens; with it off, ~120 tokens and about 1.5 s once the model is loaded.

```
/move player sanctuary
Good morning. I heard this church has an old relic. Is that true?
/act pip           # Pip answers the stranger, in his voice
/act tomas         # Tomas is in the sacristy and didn't hear it
/pending
/mind pip
```

## Next: stage 3

GM `resolve` with branch-and-roll: turn pending intents into events, with Python rolling the checks (see framework.md, Turn loop).
