"""Church scene prototype, stage 2: python play.py [--seed N] [--model NAME]"""
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
    ap.add_argument("--model", default=llm.DEFAULT_MODEL, help="Ollama model for /act")
    ap.add_argument("--ollama", default=llm.DEFAULT_URL, help="Ollama base URL")
    args = ap.parse_args()

    state = S.load_snapshot(args.resume) if args.resume else S.load_scene(args.scene)
    run_dir = S.new_run_dir(HERE / "runs")
    game = Game(state, run_dir, S.make_rng(args.seed), llm.Client(args.model, args.ollama))
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
