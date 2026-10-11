"""Play a module: python play.py <module> [--seed N] [--model NAME] [--debug]

python play.py                 lists the modules in modules/
python play.py St_Aurel_1100   starts that module
"""
import argparse
from pathlib import Path

from rpg import llm
from rpg import state as S
from rpg.repl import Game

HERE = Path(__file__).parent
MODULES = HERE / "modules"


def print_modules():
    print("Modules (python play.py <name>):")
    for name, info in S.list_modules(MODULES).items():
        print(f"  {name:18} {info.get('title', '')}: {info.get('description', '')}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("module", nargs="?", help="module folder name under modules/, e.g. St_Aurel_1100")
    ap.add_argument("--seed", type=int, default=None, help="fix dice for a repeatable run")
    ap.add_argument("--resume", help="path to a turn_NNN.json snapshot")
    ap.add_argument("--continue", dest="cont", metavar="RUN_DIR",
                    help="carry on a run from its latest snapshot, saving into the same folder")
    ap.add_argument("--model", default=llm.DEFAULT_MODEL, help="Ollama model for /act")
    ap.add_argument("--ollama", default=llm.DEFAULT_URL, help="Ollama base URL")
    ap.add_argument("--debug", action="store_true",
                    help="plain text is speech instead of a full turn, and turns show every step")
    args = ap.parse_args()

    if args.cont:
        run_dir = Path(args.cont)
        snaps = sorted(run_dir.glob("turn_*.json"))
        if snaps:
            state = S.load_snapshot(snaps[-1])
        elif args.module:
            state = S.load_module(MODULES, args.module)
        else:
            raise SystemExit(f"no snapshots in {run_dir}; name a module to start one there")
    elif args.resume:
        state = S.load_snapshot(args.resume)
        run_dir = S.new_run_dir(HERE / "runs", state.get("module", {}).get("name", ""))
    elif args.module:
        state = S.load_module(MODULES, args.module)
        run_dir = S.new_run_dir(HERE / "runs", args.module)
    else:
        print_modules()
        return
    game = Game(state, run_dir, S.make_rng(args.seed), llm.Client(args.model, args.ollama),
                debug=args.debug)
    title = state.get("module", {}).get("title", "Old save")
    print(f"{title}, {state['world']['clock']['time']}. Model {args.model}. "
          f"Saving to {run_dir}. /help for commands.")
    game.look()
    while True:
        try:
            line = input("> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not game.handle(line):
            break
    S.save_snapshot(state, run_dir)


if __name__ == "__main__":
    main()
