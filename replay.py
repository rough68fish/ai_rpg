"""Replay a run as a chat: the game on the left, the traveller on the right.

    python replay.py                      # latest run
    python replay.py runs/20261010-215635 # a run folder
    python replay.py runs/<run>/turn_002.json -o out.html --no-open

Writes replay.html into the run folder and opens it in the browser. Toggles on
the page show the GM's dice rolls, the raw events behind each narrated turn, and
the events the traveller didn't perceive.
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


def build(state, title, live=0):
    rooms = state["world"]["rooms"]
    log = state["log"]
    first_move = next((e["moved"] for e in log if e.get("moved", [None])[0] == S.PLAYER), None)
    start = first_move[1] if first_move else state["world"]["player"]["location"]

    def room_note(room):
        return f'<div class="room"><b>{esc(room)}</b><span>{esc(rooms[room]["desc"])}</span></div>'

    # Raw events per turn: what the traveller perceived, did, and (toggle) missed.
    raw, times = {}, {}
    for i, entry in enumerate(log):
        e = entry["event"]
        parts = raw.setdefault(entry["turn"], [])
        times.setdefault(entry["turn"], entry["time"])
        mine = e["actor"] == S.PLAYER
        seen = entry["perceived_by"].get(S.PLAYER)

        if mine and entry.get("moved"):  # /move: show the room entered
            parts.append(room_note(entry["moved"][2]))
        elif mine and e["text"].startswith("{player} comes in from"):
            if not (i and log[i - 1].get("moved", [None])[0] == S.PLAYER):
                parts.append(room_note(e["room"]))  # a GM-resolved move
        elif mine:
            parts.append(roll_note(entry) + right(e))
        elif seen:
            parts.append(perception_rolls(entry) + left(seen, e.get("speech")))
        else:
            # Not perceived: hidden unless the toggle is on. Shown with true names.
            text = P.render(state, None, e["text"])
            if e.get("speech"):
                text += f' "{e["speech"]}"'
            parts.append(f'<div class="unseen">{perception_rolls(entry)}{roll_note(entry)}'
                         f'<div class="msg in"><div class="cap">{esc(e["actor"])} · {esc(e["room"])}'
                         f'</div><div class="bubble ghost">{esc(text)}</div></div></div>')

    # Played turns (stage 4) carry the typed input and the GM's narration.
    told = {t["turn"]: t for t in state.get("turns", [])}
    for t in told.values():
        times.setdefault(t["turn"], t["time"])

    parts = [room_note(start)]
    for turn in sorted(set(raw) | set(told)):
        t = told.get(turn)
        where = f" · {esc(t['room'])}" if t and t.get("room") else ""
        parts.append(f'<div class="turn">Turn {turn} · {esc(times[turn])}{where}</div>')
        events = "\n".join(raw.get(turn, []))
        if not t:
            parts.append(events)
            continue
        said = esc(t["input"]) if t["input"] else "(you wait)"
        parts.append(f'<div class="msg out"><div class="bubble{"" if t["input"] else " narr"}">'
                     f'{said}</div></div>')
        parts += [roll_note(e) for e in log if e["turn"] == turn and e["event"]["actor"] == S.PLAYER]
        parts.append(f'<div class="msg in story"><div class="bubble">{esc(t["narration"])}</div></div>')
        parts += [f'<div class="roll">{esc(note)}</div>' for note in t.get("switches", [])]
        parts.append(f'<div class="raw">{events}</div>')

    body = "\n".join(parts)
    page = PAGE.replace("{{title}}", esc(title)).replace("{{body}}", body)
    if live:
        # Re-read the file every few seconds and stay pinned to the newest turn.
        page = page.replace('<meta charset="utf-8">',
                            f'<meta charset="utf-8">\n<meta http-equiv="refresh" content="{live}">')
        page = page.replace("/*LIVE*/", "window.scrollTo(0, document.body.scrollHeight);")
    return page


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
.story { max-width: 88%; }
.story .bubble { font-family: Georgia, "Times New Roman", serif; font-size: 16px; line-height: 1.55; }
.raw { display: none; flex-direction: column; gap: 6px; margin: 4px 0 4px 14px; padding-left: 10px;
  border-left: 2px dotted var(--line); }
body.raw-on .raw { display: flex; }
body:not(.rolls) .roll { display: none; }
body.unseen-on .unseen { display: flex; }
</style></head>
<body class="rolls">
<header>
  <h1>{{title}}</h1>
  <label><input type="checkbox" id="rolls" checked> Dice rolls</label>
  <label><input type="checkbox" id="raw"> Raw events</label>
  <label><input type="checkbox" id="unseen"> Events you didn't perceive</label>
</header>
<main>
{{body}}
</main>
<script>
const toggle = (id, cls) => {
  const box = document.getElementById(id);
  // Remembered, so a live page keeps your toggles across refreshes.
  try { const v = localStorage.getItem("replay-" + id); if (v !== null) box.checked = v === "1"; } catch (e) {}
  const apply = () => {
    document.body.classList.toggle(cls, box.checked);
    try { localStorage.setItem("replay-" + id, box.checked ? "1" : "0"); } catch (e) {}
  };
  box.addEventListener("change", apply); apply();
};
/*LIVE*/
toggle("rolls", "rolls");
toggle("unseen", "unseen-on");
toggle("raw", "raw-on");
</script>
</body></html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run", nargs="?", help="run folder or turn_NNN.json (default: latest run)")
    ap.add_argument("-o", "--out", help="output file (default: replay.html in the run folder)")
    ap.add_argument("--no-open", action="store_true", help="don't open the browser")
    ap.add_argument("--live", type=int, nargs="?", const=4, default=0, metavar="SECONDS",
                    help="page reloads itself (default every 4 s) and follows the newest turn")
    args = ap.parse_args()

    snap = find_snapshot(args.run)
    state = S.load_snapshot(snap)
    title = f"St. Aurel's · run {snap.parent.name}"
    out = Path(args.out) if args.out else snap.parent / "replay.html"
    out.write_text(build(state, title, args.live), encoding="utf-8")
    print(f"{len(state['log'])} events from {snap} -> {out}")
    if not args.no_open:
        webbrowser.open(out.resolve().as_uri())


if __name__ == "__main__":
    main()
