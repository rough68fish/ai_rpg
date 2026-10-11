"""Church scene prototype: python play.py [--seed N] [--model NAME] [--debug]"""
import argparse
from pathlib import Path

from rpg import llm
from rpg import state as S
from rpg.repl import Game

HERE = Path(__file__).parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=None, help="fix dice for a repeatable run")
    ap.add_argument("--scene", default=HERE / "scene")
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
        state = S.load_snapshot(snaps[-1]) if snaps else S.load_scene(args.scene)
    else:
        state = S.load_snapshot(args.resume) if args.resume else S.load_scene(args.scene)
        run_dir = S.new_run_dir(HERE / "runs")
    game = Game(state, run_dir, S.make_rng(args.seed), llm.Client(args.model, args.ollama),
                debug=args.debug)
    print(f"St. Aurel's, {state['world']['clock']['time']}. Model {args.model}. "
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
