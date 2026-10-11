"""Replay a run in three lanes: the game master on the left, each character's
speech and actions in the centre (labelled and coloured by character), and the
player on the right.

    python replay.py                      # latest run
    python replay.py runs/20261010-215635 # a run folder
    python replay.py runs/<run>/turn_002.json -o out.html --no-open
    python replay.py runs/<run> --live    # reloads itself, follows the newest turn

Writes replay.html into the run folder and opens it in the browser. Toggles on
the page show the dice rolls, the player's perceptions line by line, and the
events the player didn't perceive.
"""
import argparse
import html
import re
import webbrowser
from pathlib import Path

from rpg import perception as P
from rpg import state as S

HERE = Path(__file__).parent
SPEECH_TAG = re.compile(r"\{\w+\} (says|whispers|calls out|tells|asks|replies)( to \{\w+\})?:?")
PALETTE = 6  # .c0 ... .c5 in the stylesheet


def find_snapshot(arg):
    """A snapshot path from a file, a run folder, or nothing (latest run)."""
    if arg and Path(arg).is_file():
        return Path(arg)
    runs = [Path(arg)] if arg else sorted((HERE / "runs").glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for run in runs:
        snaps = sorted(run.glob("turn_*.json"))
        if snaps:
            return snaps[-1]
    raise SystemExit(f"no snapshots found in {arg or HERE / 'runs'}")


def esc(text):
    return html.escape(str(text))


# --- small pieces ---------------------------------------------------------------

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


def gm(text, cls=""):
    """Far left: the game master's telling."""
    return (f'<div class="lane gm {cls}"><div class="label">Game master</div>'
            f'<div class="bubble">{esc(text)}</div></div>')


def you(state, said, action=None):
    """Right: what the player typed, or did and said in a hand-driven turn."""
    body = ""
    if action:
        body += f'<div class="bubble narr">{esc(action)}</div>'
    if said:
        body += f'<div class="bubble">{esc(said)}</div>'
    return (f'<div class="lane you"><div class="label">You · {esc(S.short(state, S.PLAYER))}</div>'
            f'{body}</div>') if body else ""


def card(state, entry, colours, perceived=None, ghost=False):
    """Centre: one character's action or speech, labelled with who it was."""
    e = entry["event"]
    cid = e["actor"]
    name = S.short(state, cid)
    looks = S.appearance(state, cid)
    head = [f'<span class="who">{esc(name)}</span>']
    if perceived and name not in perceived and looks.lower() in perceived.lower():
        head.append(f'<span class="as">seen as {esc(looks)}</span>')
    if e.get("target") and e["target"] != cid:
        to = "you" if e["target"] == S.PLAYER else S.short(state, e["target"])
        head.append(f'<span class="to">to {esc(to)}</span>')
    if e.get("speech") and e.get("volume") in ("whisper", "loud"):
        head.append(f'<span class="tag">{esc(e["volume"])}</span>')

    body = ""
    text = e.get("text", "").strip()
    if text and not SPEECH_TAG.fullmatch(text):
        body += f'<p class="act">{esc(P.render(state, None, text))}</p>'
    if e.get("speech"):
        heard = ghost or perceived is None or f'"{e["speech"]}"' in perceived
        body += f'<p class="say{"" if heard else " missed"}">“{esc(e["speech"])}”</p>'
        if not heard:
            body += '<p class="note">you couldn\'t make out the words</p>'
    return (f'<div class="lane char c{colours.get(cid, 0)}{" ghost" if ghost else ""}">'
            f'<div class="label">{"".join(head)}</div><div class="bubble">{body}</div></div>')


# --- the page --------------------------------------------------------------------

def build(state, title, live=0):
    rooms = state["world"]["rooms"]
    log = state["log"]
    colours = {cid: i % PALETTE for i, cid in enumerate(state["chars"])}
    first_move = next((e["moved"] for e in log if e.get("moved", [None])[0] == S.PLAYER), None)
    start = first_move[1] if first_move else state["world"]["player"]["location"]

    def room_note(room):
        return f'<div class="room"><b>{esc(room)}</b><span>{esc(rooms[room]["desc"])}</span></div>'

    told = {t["turn"]: t for t in state.get("turns", [])}
    by_turn = {}
    for i, entry in enumerate(log):
        by_turn.setdefault(entry["turn"], []).append((i, entry))

    parts = [room_note(start)]
    for turn in sorted(set(by_turn) | set(told)):
        t = told.get(turn)
        entries = by_turn.get(turn, [])
        time = t["time"] if t else entries[0][1]["time"]
        where = f" · {esc(t['room'])}" if t and t.get("room") else ""
        parts.append(f'<div class="turn">Turn {turn} · {esc(time)}{where}</div>')

        # A played turn reads: your line, where you went and your rolls, the GM's
        # telling, then each character's part in order. A hand-driven turn has
        # no telling, so everything stays in the order it happened.
        mine, centre, raw = [], [], []
        for i, entry in entries:
            e, seen = entry["event"], entry["perceived_by"].get(S.PLAYER)
            actor = e["actor"]
            if actor == S.PLAYER:
                if entry.get("moved"):
                    (mine if t else centre).append(room_note(entry["moved"][2]))
                elif e["text"].startswith("{player} comes in from"):
                    if not (i and log[i - 1].get("moved", [None])[0] == S.PLAYER):
                        (mine if t else centre).append(room_note(e["room"]))
                elif t:
                    mine.append(roll_note(entry))
                    if e.get("actor_text"):
                        raw.append(f'<div class="rawline">{esc(e["actor_text"])}</div>')
                else:
                    own = e.get("actor_text") or ""
                    action = own if own and not own.startswith(("I say", "I whisper", "I call out")) else None
                    centre.append(roll_note(entry) + you(state, e.get("speech"), action))
            elif actor in state["chars"]:
                rolls = perception_rolls(entry) + roll_note(entry)
                if seen:
                    centre.append(rolls + card(state, entry, colours, seen))
                    raw.append(f'<div class="rawline">{esc(seen)}</div>')
                else:
                    centre.append(f'<div class="unseen">{rolls}{card(state, entry, colours, ghost=True)}</div>')
            else:  # nobody's doing: a bell, a draught. The GM's lane.
                centre.append(gm(seen) if seen else
                              f'<div class="unseen">{gm(P.render(state, None, e["text"]), "ghost")}</div>')

        if t:
            parts.append(you(state, t["input"]) if t["input"] else you(state, None, "(you wait)"))
            parts += mine
            parts.append(gm(t["narration"], "story"))
            parts += [f'<div class="roll">{esc(note)}</div>' for note in t.get("switches", [])]
        parts += centre
        if raw:
            parts.append(f'<div class="raw"><div class="label">What you perceived, line by line</div>'
                         f'{"".join(raw)}</div>')

    cast = "".join(f'<span class="castname c{colours[cid]}">{esc(S.short(state, cid))}</span>'
                   for cid in state["chars"])
    page = (PAGE.replace("{{title}}", esc(title)).replace("{{cast}}", cast)
            .replace("{{body}}", "\n".join(p for p in parts if p)))
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
  --you: #3b5bdb; --you-text: #ffffff; --line: #ddd6cc; --gm: #efe9df;
  --ok: #2f7d4f; --bad: #b4462f; --ghost: #ebe6de;
  --c0: #b4532a; --c1: #2a6fb4; --c2: #2d8655; --c3: #8a4bb4; --c4: #a07a12; --c5: #b42f6b;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #17150f; --surface: #26231d; --text: #ece6dc; --muted: #a39a8c;
    --you: #4c6ef5; --you-text: #ffffff; --line: #3a352c; --gm: #201d17;
    --ok: #6fc28f; --bad: #e98a73; --ghost: #1f1c17;
    --c0: #ec8a5e; --c1: #6fa8ec; --c2: #6cc591; --c3: #bf8ced; --c4: #dcb54a; --c5: #ec79a8;
  }
}
:root[data-theme="dark"] {
  --bg: #17150f; --surface: #26231d; --text: #ece6dc; --muted: #a39a8c;
  --you: #4c6ef5; --you-text: #ffffff; --line: #3a352c; --gm: #201d17;
  --ok: #6fc28f; --bad: #e98a73; --ghost: #1f1c17;
  --c0: #ec8a5e; --c1: #6fa8ec; --c2: #6cc591; --c3: #bf8ced; --c4: #dcb54a; --c5: #ec79a8;
}
.c0 { --c: var(--c0); } .c1 { --c: var(--c1); } .c2 { --c: var(--c2); }
.c3 { --c: var(--c3); } .c4 { --c: var(--c4); } .c5 { --c: var(--c5); }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
header { position: sticky; top: 0; background: var(--bg); border-bottom: 1px solid var(--line);
  padding: 10px 16px; display: flex; flex-wrap: wrap; gap: 6px 18px; align-items: center; z-index: 1; }
header h1 { font-size: 16px; margin: 0; flex: 1 1 auto; }
header label { font-size: 13px; color: var(--muted); cursor: pointer; }
.cast { flex: 1 1 100%; display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 13px; }
.castname { color: var(--c); font-weight: 600; }
.castname::before { content: ""; display: inline-block; width: 9px; height: 9px; border-radius: 50%;
  background: var(--c); margin-right: 5px; }
main { max-width: 820px; margin: 0 auto; padding: 16px; display: flex; flex-direction: column; gap: 8px; }

.lane { display: flex; flex-direction: column; gap: 3px; max-width: 80%; }
.label { font-size: 12px; color: var(--muted); padding: 0 4px; display: flex; flex-wrap: wrap;
  gap: 2px 8px; align-items: baseline; }
.bubble { padding: 8px 12px; border-radius: 14px; background: var(--surface);
  border: 1px solid var(--line); overflow-wrap: anywhere; }
.bubble p { margin: 0; }
.bubble p + p { margin-top: 4px; }

/* far left: the game master */
.gm { align-self: flex-start; }
.gm .label { text-transform: uppercase; letter-spacing: .06em; font-size: 11px; }
.gm .bubble { background: var(--gm); border-radius: 4px 14px 14px 14px;
  font-family: Georgia, "Times New Roman", serif; line-height: 1.55; }
.gm.story { max-width: 88%; }
.gm.story .bubble { font-size: 16px; }

/* centre: the characters */
.char { align-self: center; width: min(76%, 560px); max-width: none; }
.char .bubble { border-left: 4px solid var(--c); border-radius: 6px 14px 14px 6px; }
.char .who { color: var(--c); font-weight: 700; font-size: 13px; }
.char .as { font-style: italic; }
.char .tag { border: 1px solid var(--line); border-radius: 8px; padding: 0 6px; font-size: 11px; }
.act { font-style: italic; color: var(--muted); }
.say { font-size: 15px; }
.say.missed { opacity: .5; }
.note { font-size: 12px; color: var(--bad); }
.ghost { opacity: .65; }
.ghost .bubble { border-style: dashed; background: var(--ghost); }

/* right: you */
.you { align-self: flex-end; align-items: flex-end; }
.you .bubble { background: var(--you); color: var(--you-text); border-color: var(--you);
  border-radius: 14px 4px 14px 14px; }
.you .bubble.narr { background: transparent; color: var(--muted); border-color: var(--line); font-style: italic; }

.turn { align-self: center; font-size: 12px; color: var(--muted); margin: 18px 0 2px;
  text-transform: uppercase; letter-spacing: .06em; }
.room { align-self: center; text-align: center; max-width: 90%; margin: 4px 0; font-size: 13px;
  color: var(--muted); border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); padding: 6px 12px; }
.room b { display: block; color: var(--text); text-transform: capitalize; }
.roll { font: 12px/1.4 ui-monospace, Consolas, monospace; color: var(--muted); align-self: center;
  border-left: 3px solid var(--muted); padding: 2px 8px; max-width: 90%; }
.roll.ok { border-color: var(--ok); } .roll.bad { border-color: var(--bad); }
.raw { display: none; flex-direction: column; gap: 3px; align-self: flex-start; max-width: 88%;
  margin: 2px 0; padding-left: 10px; border-left: 2px dotted var(--line); }
.rawline { font-size: 13px; color: var(--muted); }
.unseen { display: none; flex-direction: column; gap: 3px; }
body.raw-on .raw { display: flex; }
body:not(.rolls) .roll { display: none; }
body.unseen-on .unseen { display: flex; }
@media (max-width: 600px) {
  .lane, .gm.story { max-width: 94%; }
  .char { width: 94%; }
}
</style></head>
<body class="rolls">
<header>
  <h1>{{title}}</h1>
  <label><input type="checkbox" id="rolls" checked> Dice rolls</label>
  <label><input type="checkbox" id="raw"> Your perceptions</label>
  <label><input type="checkbox" id="unseen"> Events you didn't perceive</label>
  <div class="cast">{{cast}}</div>
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
    module = state.get("module", {}).get("title", "St. Aurel's")
    title = f"{module} · run {snap.parent.name}"
    out = Path(args.out) if args.out else snap.parent / "replay.html"
    out.write_text(build(state, title, args.live), encoding="utf-8")
    print(f"{len(state['log'])} events from {snap} -> {out}")
    if not args.no_open:
        webbrowser.open(out.resolve().as_uri())


if __name__ == "__main__":
    main()
