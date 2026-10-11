"""Play a module: python play.py <module> [--seed N] [--model NAME] [--debug] [--web]

python play.py                       lists the modules in modules/
python play.py St_Aurel_1100         starts that module
python play.py St_Aurel_1100 --web   the same, watchable at http://127.0.0.1:8000;
                                     lines can come from the terminal, the page or send.py
"""
import argparse
import time
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
    ap.add_argument("--web", action="store_true", help="also serve the web viewer")
    ap.add_argument("--port", type=int, default=8000, help="web viewer port")
    ap.add_argument("--bind", default="127.0.0.1",
                    help="web viewer address; 0.0.0.0 to watch from another device")
    ap.add_argument("--no-terminal", action="store_true",
                    help="with --web: don't read the terminal (input only from the page or send.py)")
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
    try:
        if args.web:
            host_game(game, run_dir, args)
        else:
            game.look()
            game.status()
            while True:
                try:
                    line = input("> ")
                except EOFError:
                    break
                if not game.handle(line):
                    break
    except KeyboardInterrupt:
        pass
    print()
    S.save_snapshot(state, run_dir)


def host_game(game, run_dir, args):
    """Serve the game: the terminal, the web page and send.py share one queue."""
    from rpg.host import Host
    from rpg.repl import print_event
    from rpg.web import serve

    host = Host(game, run_dir)
    host.listeners.append(print_event)
    serve(host, args.bind, args.port)
    print(f"Web viewer: http://{'127.0.0.1' if args.bind == '0.0.0.0' else args.bind}:{args.port}")
    wait(host.submit("/look", "start"))
    if not args.no_terminal:
        while True:
            try:
                line = input("> ")
            except EOFError:
                break
            if not line.strip():
                continue
            job = host.submit(line, "terminal")
            wait(job)
            if not job.keep_going:
                return
    print("Not reading the terminal; serving until Ctrl-C.")
    while True:
        time.sleep(1)


def wait(job):
    # A timeout keeps Ctrl-C working on Windows while a turn runs.
    while not job.done.wait(0.5):
        pass


if __name__ == "__main__":
    main()
