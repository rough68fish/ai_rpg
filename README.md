# Church scene prototype

Design: [framework.md](framework.md). Prompts: [prompts.md](prompts.md).

## Stage 1 (built): state, map, perception, debug REPL

No model calls yet and no dependencies beyond Python 3.10+. Copy this whole `prototype/` folder to your PC and run:

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
/prompt pip        # the exact prompt stage 2 will send, plus a leakage check
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
rpg/repl.py          the debug REPL
scene/               starting state (world.json, characters/*.json)
```

## Next: stage 2

Add `rpg/llm.py` (a call to Ollama `/api/chat` with `format` set to `prompts.CHARACTER_SCHEMA`) and an `/act <who>` command that sends `/prompt <who>` to the model and prints the intent. That needs Ollama, so it's best built and run on your machine.
