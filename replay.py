"""Replay a run as a chat: the game on the left, the traveller on the right.

    python replay.py                      # latest run
    python replay.py runs/20261010-215635 # a run folder
    python replay.py runs/<run>/turn_002.json -o out.html --no-open

Writes replay.html into the run folder and opens it in the browser. Toggles on
the page show the GM's dice rolls and the events the traveller didn't perceive.
"""
import argparse
import html
import webbrowser
from pathlib import Path

from rpg import perception as P
from rpg import state as S

HERE = Path(__file__).parent


def find_snapshot(arg):
    """A snapshot path from a file, a run folder, or nothing (latest run)."""
    if arg and Path(arg).is_file():
        return Path(arg)
    runs = [Path(arg)] if arg else sorted((HERE / "runs").glob("*"), reverse=True)
    for run in runs:
        snaps = sorted(run.glob("turn_*.json"))
        if snaps:
            return snaps[-1]
    raise SystemExit(f"no snapshots found in {arg or HERE / 'runs'}")


def esc(text):
    return html.escape(str(text))


def roll_note(entry):
    c = entry.get("check")
    if not c:
        return ""
    ok = entry.get("branch") == "success"
    return (f'<div class="roll {"ok" if ok else "bad"}">{esc(c["actor"])} · {esc(c["ability"])} '
            f'{c["modifier"]:+d}{": " + esc(c["why"]) if c.get("why") else ""} · rolled {c["roll"]} '
            f'vs {c["target"]} · {"success" if ok else "failure"}</div>')


def perception_rolls(entry):
    rolls = [c for c in entry.get("checks", []) if c["actor"] == S.PLAYER]
    return "".join(
        f'<div class="roll {"ok" if c["success"] else "bad"}">your WIS ({esc(c.get("why", ""))}) · '
        f'rolled {c["roll"]} vs {c["target"]} · {"noticed" if c["success"] else "missed"}</div>'
        for c in rolls)


def left(line, speech):
    """Something the traveller perceived. Heard speech becomes a message bubble
    with the speaker's description as its caption; anything else is narration."""
    quoted = f'"{speech}"' if speech else None
    if quoted and quoted in line:
        caption = line.replace(quoted, "").strip().rstrip(":").strip()
        return (f'<div class="msg in"><div class="cap">{esc(caption)}</div>'
                f'<div class="bubble">{esc(speech)}</div></div>')
    return f'<div class="msg in"><div class="bubble narr">{esc(line)}</div></div>'


def right(event):
    """Something the traveller did or said."""
    out = []
    speech = event.get("speech")
    own = event.get("actor_text") or ""
    # Plain /say lines are just the words; GM-resolved attempts also have an action.
    if own and not own.startswith(("I say", "I whisper", "I call out")):
        out.append(f'<div class="bubble narr">{esc(own)}</div>')
    if speech:
        out.append(f'<div class="bubble">{esc(speech)}</div>')
    return f'<div class="msg out">{"".join(out)}</div>' if out else ""


def build(state, title):
    rooms = state["world"]["rooms"]
    log = state["log"]
    first_move = next((e["moved"] for e in log if e.get("moved", [None])[0] == S.PLAYER), None)
    start = first_move[1] if first_move else state["world"]["player"]["location"]

    parts = [f'<div class="room"><b>{esc(start)}</b><span>{esc(rooms[start]["desc"])}</span></div>']
    turn = None
    for i, entry in enumerate(log):
        e = entry["event"]
        if entry["turn"] != turn:
            turn = entry["turn"]
            parts.append(f'<div class="turn">Turn {turn} · {esc(entry["time"])}</div>')
        mine = e["actor"] == S.PLAYER
        seen = entry["perceived_by"].get(S.PLAYER)

        if mine and entry.get("moved"):  # /move: show the room entered
            room = entry["moved"][2]
            parts.append(f'<div class="room"><b>{esc(room)}</b><span>{esc(rooms[room]["desc"])}</span></div>')
            continue
        if mine and e["text"].startswith("{player} comes in from"):
            if i and log[i - 1].get("moved", [None])[0] == S.PLAYER:
                continue  # already shown from the /move entry
            room = e["room"]  # a GM-resolved move
            parts.append(f'<div class="room"><b>{esc(room)}</b><span>{esc(rooms[room]["desc"])}</span></div>')
            continue
        if mine:
            parts.append(roll_note(entry) + right(e))
            continue
        if seen:
            parts.append(perception_rolls(entry) + left(seen, e.get("speech")))
            continue
        # Not perceived: hidden unless the toggle is on. Shown with true names.
        text = P.render(state, None, e["text"])
        if e.get("speech"):
            text += f' "{e["speech"]}"'
        parts.append(f'<div class="unseen">{perception_rolls(entry)}{roll_note(entry)}'
                     f'<div class="msg in"><div class="cap">{esc(e["actor"])} · {esc(e["room"])}'
                     f'</div><div class="bubble ghost">{esc(text)}</div></div></div>')

    body = "\n".join(parts)
    return PAGE.replace("{{title}}", esc(title)).replace("{{body}}", body)


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{title}}</title>
<style>
:root {
  --bg: #f4f1ec; --surface: #ffffff; --text: #23201c; --muted: #7a7268;
  --out: #3b5bdb; --out-text: #ffffff; --line: #ddd6cc;
  --ok: #2f7d4f; --bad: #b4462f; --ghost: #ebe6de;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #17150f; --surface: #26231d; --text: #ece6dc; --muted: #a39a8c;
    --out: #4c6ef5; --out-text: #ffffff; --line: #3a352c;
    --ok: #6fc28f; --bad: #e98a73; --ghost: #1f1c17;
  }
}
:root[data-theme="dark"] {
  --bg: #17150f; --surface: #26231d; --text: #ece6dc; --muted: #a39a8c;
  --out: #4c6ef5; --out-text: #ffffff; --line: #3a352c;
  --ok: #6fc28f; --bad: #e98a73; --ghost: #1f1c17;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
header { position: sticky; top: 0; background: var(--bg); border-bottom: 1px solid var(--line);
  padding: 12px 16px; display: flex; flex-wrap: wrap; gap: 8px 20px; align-items: center; z-index: 1; }
header h1 { font-size: 16px; margin: 0; flex: 1 1 auto; }
header label { font-size: 13px; color: var(--muted); cursor: pointer; }
main { max-width: 720px; margin: 0 auto; padding: 16px; display: flex; flex-direction: column; gap: 6px; }
.msg { display: flex; flex-direction: column; max-width: 78%; gap: 3px; }
.msg.in { align-self: flex-start; }
.msg.out { align-self: flex-end; align-items: flex-end; }
.cap { font-size: 12px; color: var(--muted); padding: 0 10px; }
.bubble { padding: 8px 12px; border-radius: 16px; background: var(--surface);
  border: 1px solid var(--line); white-space: pre-wrap; overflow-wrap: anywhere; }
.in .bubble { border-bottom-left-radius: 4px; }
.out .bubble { background: var(--out); color: var(--out-text); border-color: var(--out);
  border-bottom-right-radius: 4px; }
.bubble.narr { font-style: italic; }
.in .bubble.narr { background: transparent; color: var(--muted); }
.out .bubble.narr { background: transparent; color: var(--muted); border-color: var(--line); }
.bubble.ghost { background: var(--ghost); color: var(--muted); border-style: dashed; font-size: 13px; }
.turn { align-self: center; font-size: 12px; color: var(--muted); margin: 14px 0 4px;
  text-transform: uppercase; letter-spacing: .06em; }
.room { align-self: center; text-align: center; max-width: 90%; margin: 8px 0; font-size: 13px;
  color: var(--muted); border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); padding: 6px 12px; }
.room b { display: block; color: var(--text); text-transform: capitalize; }
.roll { font: 12px/1.4 ui-monospace, Consolas, monospace; color: var(--muted); align-self: center;
  border-left: 3px solid var(--muted); padding: 2px 8px; max-width: 90%; }
.roll.ok { border-color: var(--ok); } .roll.bad { border-color: var(--bad); }
.unseen { display: none; flex-direction: column; gap: 3px; }
body:not(.rolls) .roll { display: none; }
body.unseen-on .unseen { display: flex; }
</style></head>
<body class="rolls">
<header>
  <h1>{{title}}</h1>
  <label><input type="checkbox" id="rolls" checked> Dice rolls</label>
  <label><input type="checkbox" id="unseen"> Events you didn't perceive</label>
</header>
<main>
{{body}}
</main>
<script>
const toggle = (id, cls) => {
  const box = document.getElementById(id);
  const apply = () => document.body.classList.toggle(cls, box.checked);
  box.addEventListener("change", apply); apply();
};
toggle("rolls", "rolls");
toggle("unseen", "unseen-on");
</script>
</body></html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run", nargs="?", help="run folder or turn_NNN.json (default: latest run)")
    ap.add_argument("-o", "--out", help="output file (default: replay.html in the run folder)")
    ap.add_argument("--no-open", action="store_true", help="don't open the browser")
    args = ap.parse_args()

    snap = find_snapshot(args.run)
    state = S.load_snapshot(snap)
    title = f"St. Aurel's · run {snap.parent.name}"
    out = Path(args.out) if args.out else snap.parent / "replay.html"
    out.write_text(build(state, title), encoding="utf-8")
    print(f"{len(state['log'])} events from {snap} -> {out}")
    if not args.no_open:
        webbrowser.open(out.resolve().as_uri())


if __name__ == "__main__":
    main()
