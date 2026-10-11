"""Play a game that `python play.py <Module> --web` is hosting, from any shell.
Each line waits until it has been played, then prints what it produced, so a
script (or Claude) can play while someone watches the web viewer.

    python send.py "I walk up to the altar"     play a turn
    python send.py /who /truth                  several lines, in order
    python send.py --trace "I wait"             also print the turn's details
    python send.py --watch                      follow everything, from anyone
"""
import argparse
import json
import os
import time
import urllib.error
import urllib.request

from rpg.repl import print_event


def call(url, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url + path, data, {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=3600) as resp:
            return json.load(resp)
    except urllib.error.URLError as e:
        raise SystemExit(f"can't reach the game at {url} ({e}). Is play.py running with --web?")


def show(ev, trace):
    if ev["kind"] == "trace":
        ev = {**ev, "verbose": trace}
    if ev["kind"] == "input":
        print(f"[{ev.get('source')}] > {ev['text']}")
    else:
        print_event(ev)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("lines", nargs="*", help="what to type, one argument per line")
    ap.add_argument("--url", default=os.environ.get("RPG_URL", "http://127.0.0.1:8000"))
    ap.add_argument("--trace", action="store_true", help="print turn details too")
    ap.add_argument("--watch", action="store_true", help="follow the game's output")
    ap.add_argument("--as", dest="source", default="cli", help="name shown for these lines")
    args = ap.parse_args()
    url = args.url.rstrip("/")

    for line in args.lines:
        for ev in call(url, "/api/input", {"text": line, "source": args.source, "wait": True})["events"]:
            show(ev, args.trace)
    if args.watch:
        since = len(call(url, "/api/events?since=0"))
        while True:
            for ev in call(url, f"/api/events?since={since}"):
                show(ev, args.trace)
                since = ev["seq"] + 1
            time.sleep(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
