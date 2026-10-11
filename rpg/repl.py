"""The REPL. Plain text plays a full turn (stage 4); /commands drive each step by
hand and inspect what each character perceived, remembers and is prompted with."""
import io
import json
import shlex
import sys
import textwrap
from contextlib import redirect_stdout

from . import gm
from . import llm
from . import modes
from . import perception as P
from . import prompts
from . import state as S

FIRST_PERSON = {"whisper": "whisper", "loud": "call out", "normal": "say"}

HELP = """\
Plain text            play a turn: the GM resolves what you do, characters with
                      something new act, the GM narrates (--debug: just speech)
/turn [text]          the same; with no text, you wait a minute
/trace                full debug output of the last turn
/verbose              show every step of a turn as it happens (toggle)
/say <who> [whisper|loud] [@target] <words>
                      someone speaks; <who> is an id or 'player'
/do <who> [subtle] [loud] [@target] <text>
                      someone acts; write people as {id}, e.g. /do anna {anna} drops a cup
/move <who> <room>    move to an adjacent room (locked doors stop you)
/next                 advance the clock one turn (one minute) and save
/roll <who> <STAT> [mod]  roll d20 under STAT + mod
/look [who]           the room as someone sees it (default: player)
/who                  everyone's location and mode
/mind <who>           memory, beliefs and goals (what they think)
/truth                ground truth and hidden objects (what is)
/log [n]              last n events with perception rolls (default 5)
/prompt <who>         the character prompt that would be sent, with leakage check
/act <who>            send that prompt to the model: speech is spoken, the
                      action is kept as a pending intent
/pending              intents waiting for the GM
/resolve              GM resolves all pending intents (checks, rolls, state changes)
/try <text>           the traveller attempts something; the GM resolves it
/know <who> <whom>    <who> learns <whom>'s name
/promote <who>, /demote <who>   switch agent / managed
/save                 save a snapshot now
/help, /quit"""


class Game:
    def __init__(self, state, run_dir, rng, client=None, debug=False):
        self.state, self.run_dir, self.rng = state, run_dir, rng
        self.llm = client or llm.Client()
        self.debug = debug        # plain text is speech, not a turn
        self.verbose = debug      # stream every step of a turn
        self.last_trace = ""

    # --- helpers ---------------------------------------------------------
    def _id(self, who):
        who = who.lower()
        if who == "me" or who in S.player_words(self.state):
            return S.PLAYER
        if who in self.state["chars"]:
            return who
        raise ValueError(f"unknown character '{who}'")

    def _emit(self, event):
        self._show(*P.apply(self.state, event, self.rng))

    @staticmethod
    def _show(perceptions, checks, quiet=False):
        for c in checks:
            mark = "ok" if c["success"] else "miss"
            print(f"  [roll] {c['actor']} {c['ability']} {c.get('why', '')}: "
                  f"{c['roll']} vs {c['target']} {mark}")
        for obs, line in perceptions.items():
            print(f"  -> {obs}: {line}")
        if not perceptions and not quiet:
            print("  -> nobody else perceived it")

    @staticmethod
    def _opts(words, flags):
        """Pull leading flags and an @target off the front of a word list."""
        found, target = set(), None
        while words:
            w = words[0].lower()
            if w in flags:
                found.add(w)
            elif w.startswith("@"):
                target = w[1:]
            else:
                break
            words = words[1:]
        return found, target, words

    # --- commands --------------------------------------------------------
    def say(self, who, words):
        aid = self._id(who)
        flags, target, words = self._opts(words, {"whisper", "loud"})
        volume = "whisper" if "whisper" in flags else "loud" if "loud" in flags else "normal"
        tid = self._id(target) if target else None
        if tid and S.location(self.state, tid) != S.location(self.state, aid):
            print(f"  (note: {tid} is not in the {S.location(self.state, aid)}, so they won't hear it unless it's loud)")
        self._speak(aid, " ".join(words), volume, tid)

    def _speak(self, aid, speech, volume, tid):
        verb = {"whisper": "whispers", "loud": "calls out", "normal": "says"}[volume]
        to = f" to {{{tid}}}" if tid else ""
        to_me = f" to {S.name_for(self.state, aid, tid)}" if tid else ""
        self._emit({"actor": aid, "room": S.location(self.state, aid),
                    "text": f"{{{aid}}} {verb}{to}:", "speech": speech,
                    "volume": volume, "subtle": False, "target": tid,
                    "actor_text": f'I {FIRST_PERSON[volume]}{to_me}: "{speech}"',
                    "importance": 4})

    def do(self, who, words):
        aid = self._id(who)
        flags, target, words = self._opts(words, {"subtle", "loud"})
        text = " ".join(words)
        if "{" not in text:
            text = f"{{{aid}}} {text}"
        self._emit({"actor": aid, "room": S.location(self.state, aid), "text": text,
                    "speech": None, "volume": "loud" if "loud" in flags else "silent",
                    "subtle": "subtle" in flags,
                    "target": self._id(target) if target else None, "importance": 3})

    def move(self, who, room):
        aid = self._id(who)
        here = S.location(self.state, aid)
        if room not in self.state["world"]["rooms"]:
            print(f"  no room '{room}'. Rooms: {', '.join(self.state['world']['rooms'])}")
            return
        if room not in S.adjacent(self.state, here):
            print(f"  {room} is not next to {here}")
            return
        door = S.door_between(self.state, here, room)
        if door and door.get("locked"):
            self._emit({"actor": aid, "room": here, "text": f"{{{aid}}} tries the door to the {room}, but it is locked.",
                        "actor_text": f"I tried the door to the {room}. It is locked.",
                        "volume": "silent", "subtle": False, "importance": 4})
            return
        self._emit({"actor": aid, "room": here, "text": f"{{{aid}}} leaves toward the {room}.",
                    "actor_text": f"I went from the {here} to the {room}.", "volume": "silent", "subtle": False})
        S.actor(self.state, aid)["location"] = room
        self.state["log"][-1]["moved"] = [aid, here, room]
        self._show(*P.arrive(self.state, aid, here, self.rng), quiet=True)

    def next_turn(self):
        clock = self.state["world"]["clock"]
        h, m = map(int, clock["time"].split(":"))
        m += 1
        clock["time"] = f"{h + m // 60:02d}:{m % 60:02d}"
        clock["turn"] += 1
        path = S.save_snapshot(self.state, self.run_dir)
        print(f"  turn {clock['turn']}, {clock['time']} (saved {path.name})")

    def roll(self, who, ability, mod="0"):
        c = S.roll_under(self.state, self._id(who), ability.upper(), int(mod), self.rng)
        print(f"  d20 = {c['roll']} vs {c['target']}: {'success' if c['success'] else 'failure'}")

    def look(self, who="player"):
        aid = self._id(who)
        room = S.location(self.state, aid)
        others = [S.name_for(self.state, aid, o) for o in S.in_room(self.state, room) if o != aid]
        print(f"  {S.room_name(self.state, room)} ({room}): {self.state['world']['rooms'][room]['desc']}")
        print(f"  you see: {', '.join(others) or 'nobody'}")
        print(f"  exits: {', '.join(S.adjacent(self.state, room))}")

    def who(self):
        for aid in S.all_ids(self.state):
            rec = S.actor(self.state, aid)
            mode = "player" if aid == S.PLAYER else rec["mode"]
            print(f"  {aid:8} {S.location(self.state, aid):10} {mode}")

    def mind(self, who):
        aid = self._id(who)
        rec = S.actor(self.state, aid)
        for key in ("beliefs", "goals"):
            if key in rec:
                print(f"  {key}:")
                for b in rec[key]:
                    print(f"    - {b}")
        print("  memory:")
        for m in rec.get("memory", []):
            print(f"    [{m['t']}] ({m['importance']}) {m['text']}")

    def truth(self):
        w = self.state["world"]
        for t in w["truth"]:
            print(f"  * {t}")
        for oid, o in w["objects"].items():
            hidden = " (hidden)" if o.get("hidden") else ""
            print(f"  {oid}{hidden} @ {o['location']}: {o['desc']}")
        for did, d in w["doors"].items():
            print(f"  door {did}: {'locked' if d['locked'] else 'open'}")

    def log(self, n="5"):
        for entry in self.state["log"][-int(n):]:
            e = entry["event"]
            print(f"  [{entry['turn']}] {e['actor']} @ {e['room']} ({e.get('volume')}"
                  f"{', subtle' if e.get('subtle') else ''}): {e['text']} {e.get('speech') or ''}")
            if entry.get("check"):
                c = entry["check"]
                print(f"      GM check {c['actor']} {c['ability']} {c['modifier']:+d}: "
                      f"{c['roll']} vs {c['target']}, {entry['branch']}")
            for c in entry["checks"]:
                print(f"      roll {c['actor']} {c['ability']}: {c['roll']} vs {c['target']}")
            for ch in entry.get("state_changes", []):
                print(f"      change {ch['cmd']}: {'ok' if ch['ok'] else 'rejected'} ({ch['msg']})")
            print(f"      perceived by: {', '.join(entry['perceived_by']) or 'nobody'}")

    def prompt(self, who):
        cid = self._id(who)
        if cid == S.PLAYER:
            print("  the player has no prompt")
            return
        sysm = prompts.system_message(self.state, cid)
        user = prompts.user_message(self.state, cid)
        print(textwrap.indent("=== system ===\n" + sysm + "\n\n=== user ===\n" + user, "  "))
        problems = prompts.check_leakage(self.state, cid, sysm + user)
        print("  LEAKAGE: " + "; ".join(problems) if problems else "  leakage check: clean")

    def _target_id(self, cid, text):
        """Map the model's free-text target ("Father Tomas", "the stranger") to an id."""
        if not text:
            return None
        t = text.lower().strip()
        for oid in S.all_ids(self.state):
            if oid == cid:
                continue
            rec = S.actor(self.state, oid)
            full = (rec.get("name") or rec["identity"]["name"]).lower()
            names = {oid, full, full.split()[-1].strip("()"), S.short(self.state, oid).lower(),
                     S.name_for(self.state, cid, oid).lower(), S.appearance(self.state, oid).lower()}
            if t in names or any(n in t for n in names if len(n) > 2):
                return oid
        if any(w in t for w in S.player_words(self.state) | {"visitor"}):
            return S.PLAYER
        return None

    def _misaddressed(self, cid, new, reply):
        """If someone just spoke to this character but the reply goes to a person
        who said and did nothing new, return a note for a second try."""
        speaker = None
        for m in reversed(new):  # perception lines read 'X says to me: "..."'
            by = m.get("by")
            if by and by not in (cid, "summary") and '"' in m["text"] \
                    and " me" in m["text"].split('"')[0]:
                speaker = by
                break
        tid = self._target_id(cid, reply.get("target"))
        if not speaker or not tid or tid == speaker or any(m.get("by") == tid for m in new):
            return None
        them = S.name_for(self.state, cid, speaker)
        other = S.name_for(self.state, cid, tid)
        return (f"the last person who spoke to you was {them}, but your reply is aimed at "
                f"{other}. That is fine if you deliberately turn to {other}; if you meant to "
                f"answer {them}, address {them}.")

    def act(self, who):
        """Send this character's prompt to the model and play out the reply.

        Speech is spoken straight away (no check needed to talk). The physical
        action is only recorded as a pending intent: resolving it is the GM's job
        in stage 3."""
        cid = self._id(who)
        if cid == S.PLAYER:
            print("  the player acts by typing")
            return
        rec = self.state["chars"][cid]
        if rec["mode"] != "agent":
            print(f"  ({cid} is GM-managed; acting as an agent anyway)")
        sysm = prompts.system_message(self.state, cid)
        user = prompts.user_message(self.state, cid)
        problems = prompts.check_leakage(self.state, cid, sysm + user)
        if problems:
            print("  not sent, LEAKAGE: " + "; ".join(problems))
            return
        new = rec.get("memory", [])[prompts.seen_count(rec):]
        try:
            reply, stats = self.llm.chat(sysm, user, prompts.CHARACTER_SCHEMA, llm.TEMP_CHARACTER)
            note = self._misaddressed(cid, new, reply)
            if note:
                # One slip snowballs: the wrong name lands in memory and the next
                # turn builds on it. Ask once more; keep the second answer.
                print(f"  (re-asked: {note})")
                reply, stats = self.llm.chat(sysm, user + "\n\nCheck before answering: " + note,
                                             prompts.CHARACTER_SCHEMA, llm.TEMP_CHARACTER)
        except llm.LLMError as e:
            print(f"  {cid}: {e}", file=sys.stderr)
            return
        clock = self.state["world"]["clock"]
        print(f"  ({stats['seconds']}s, {stats['prompt_tokens']} in / {stats['reply_tokens']} out)")
        print(f"  thought: {reply.get('thought')}")
        print(f"  mood:    {reply.get('mood')}")
        rec.setdefault("memory", []).append(
            {"t": clock["turn"], "time": clock["time"], "importance": 2,
             "text": f"I thought: {reply.get('thought')} (I felt {reply.get('mood')})", "by": cid})
        self._edit_list(rec, "beliefs", reply.get("new_beliefs"), reply.get("drop_beliefs"))
        self._edit_list(rec, "goals", reply.get("new_goals"), reply.get("done_goals"))

        tid = self._target_id(cid, reply.get("target"))
        if reply.get("target"):
            print(f"  target:  {reply['target']} -> {tid or '(not a person here)'}")
        if reply.get("speech"):
            print(f"  says ({reply.get('volume', 'normal')}): {reply['speech']}")
            self._speak(cid, reply["speech"], reply.get("volume") or "normal", tid)
        if reply.get("action"):
            print(f"  tries:   {reply['action']}  [pending: /resolve]")
            self.state.setdefault("pending", []).append(
                {"turn": clock["turn"], "actor": cid, "action": reply["action"],
                 "target": tid, "target_text": reply.get("target")})
        # Everything up to here (perceptions, its thought, its own speech) is now old.
        rec["memories_seen"] = len(rec["memory"])
        rec["last_acted_turn"] = clock["turn"]

    @staticmethod
    def _edit_list(rec, key, add, drop):
        items = rec.setdefault(key, [])
        for line in drop or []:
            match = next((i for i in items if i.lower().strip(" .") == line.lower().strip(" .")), None)
            if match:
                items.remove(match)
                print(f"  {key} -: {match}")
            else:
                print(f"  {key} -: (no exact match, ignored) {line}")
        for line in add or []:
            if line not in items:
                items.append(line)
                print(f"  {key} +: {line}")

    def _gm(self, intents, tick=()):
        try:
            reports, stats = gm.resolve(self.state, intents, self.llm, self.rng, tick)
        except llm.LLMError as e:
            print(f"  GM: {e}", file=sys.stderr)
            return False
        print(f"  (GM {stats['seconds']}s, {stats['prompt_tokens']} in / {stats['reply_tokens']} out,"
              f" {len(reports)} events)")
        for r in reports:
            e = r["event"]
            print(f"  * {r['actor']} @ {e['room']} ({e['volume']}{', subtle' if e['subtle'] else ''})")
            c = r["check"]
            if c:
                print(f"    check {c['ability']} {c['modifier']:+d} ({c['why']}): "
                      f"d20 = {c['roll']} vs {c['target']}, {r['branch'].upper()}")
            print(f"    {e['text']}" + (f' "{e["speech"]}"' if e["speech"] else ""))
            if c and r.get("other"):
                print(f"    (other branch: {r['other']})")
            if r.get("note"):
                print(f"    ({r['note']})")
            self._show(r["perceptions"], r["checks"])
            for ch in r["changes"]:
                print(f"    change: {ch['cmd']} -> {'ok' if ch['ok'] else 'REJECTED'}: {ch['msg']}")
                if ch["arrival"]:
                    self._show(*ch["arrival"], quiet=True)
        return True

    def try_(self, text):
        """The traveller attempts something; the GM decides what happens."""
        if not text:
            print("  /try <what you do>")
            return
        log_from = len(self.state["log"])
        if self._gm([self._player_intent(text)]):
            self._promote_engaged(log_from)

    def _player_intent(self, text):
        """The typed line, split into what the traveller does and says."""
        try:
            split = gm.split_line(self.state, text, self.llm)
        except llm.LLMError as e:
            print(f"  split: {e}", file=sys.stderr)
            split = {}
        print(f"  split: {split}")
        return {"actor": S.PLAYER, "action": text, **split}

    def resolve(self):
        intents = self.state.get("pending", [])
        if not intents:
            print("  no pending intents; /act someone first")
            return
        # framework.md, turn loop step 6: managed characters tick in the same call.
        tick = [cid for cid, rec in self.state["chars"].items() if rec["mode"] == "managed"]
        if self._gm(intents, tick):
            self.state["pending"] = []

    # --- the full turn (framework.md, Turn loop) -------------------------------
    IDLE_TURNS = 3  # an agent with no news still acts this often, so the scene moves

    def _has_news(self, cid):
        """Something from someone else since this character last acted."""
        rec = self.state["chars"][cid]
        return any(m.get("by") != cid for m in rec.get("memory", [])[prompts.seen_count(rec):])

    def _who_acts(self):
        """Agents with news, or idle too long. Those in the traveller's room go
        first, so a reply can answer what was just said."""
        turn = self.state["world"]["clock"]["turn"]
        here = S.location(self.state, S.PLAYER)
        acting = [cid for cid, rec in self.state["chars"].items() if rec["mode"] == "agent"
                  and (self._has_news(cid) or turn - rec.get("last_acted_turn", -1) >= self.IDLE_TURNS)]
        return sorted(acting, key=lambda cid: S.location(self.state, cid) != here)

    # --- promotion and demotion (stage 5) --------------------------------------
    def _switch(self, cid, mode, why=""):
        """Promote or demote with a memory summary. Returns a one-line note."""
        report = (modes.promote if mode == "agent" else modes.demote)(self.state, cid, self.llm)
        print(f"  {cid} -> {mode}{' (' + why + ')' if why else ''}: memories {report['before']}"
              f" -> {report['after']}")
        for line in report.get("summaries", []):
            print(f"    summary: {line}")
        for key in ("beliefs", "goals"):
            for line in report[key]:
                print(f"    {key} +: {line}")
        if report["error"]:
            print(f"  summary skipped for {cid}: {report['error']}", file=sys.stderr)
        verb = "takes notice of you" if mode == "agent" else "goes back to the background"
        return f"{S.short(self.state, cid)} {verb}" + (f" ({why})" if why else "")

    def _promote_engaged(self, log_from):
        return [self._switch(cid, "agent", "engaged by the traveller")
                for cid in modes.engaged(self.state, log_from)]

    def turn(self, text=""):
        player = S.actor(self.state, S.PLAYER)
        start = len(player.setdefault("memory", []))
        log_from = len(self.state["log"])
        clock = self.state["world"]["clock"]
        out = sys.stdout
        switches = []

        def step(msg):
            print(f"  ... {msg}", file=out, flush=True)

        trace = io.StringIO()
        with redirect_stdout(sys.stdout if self.verbose else trace):
            if text:
                step("the GM resolves what you do")
                print(f"\n== you: {text}")
                self._gm([self._player_intent(text)])
                # Step 4: anyone the traveller engaged comes alive before acting.
                for note in self._promote_engaged(log_from):
                    step(note)
                    switches.append(note)
            for cid in self._who_acts():
                step(f"{S.short(self.state, cid)} acts")
                print(f"\n== {cid} acts")
                self.act(cid)
            tick = [cid for cid, rec in self.state["chars"].items() if rec["mode"] == "managed"]
            if self.state.get("pending") or tick:
                step("the GM resolves everyone else")
                print("\n== GM resolves")
                if self._gm(self.state.get("pending", []), tick):
                    self.state["pending"] = []
            modes.touch_contacts(self.state, log_from)
            for cid in modes.idle_agents(self.state):
                step(f"{S.short(self.state, cid)} drifts out of the story (summarising)")
                switches.append(self._switch(cid, "managed",
                                             f"no contact for {modes.DEMOTE_AFTER} turns"))
        self.last_trace = trace.getvalue()

        lines = [m["text"] for m in player["memory"][start:]]
        step("the GM narrates")
        try:
            prose, stats = gm.narrate(self.state, lines, self.llm)
        except llm.LLMError as e:
            print(f"  GM: {e}", file=sys.stderr)
            prose = " ".join(lines) or "Nothing happens."
        prose = " ".join(prose.split())
        problems = prompts.check_leakage(self.state, S.PLAYER, prose)
        if problems:
            print("  NARRATION LEAKAGE: " + "; ".join(problems), file=sys.stderr)
        print("\n" + textwrap.fill(prose, 78, initial_indent="  ", subsequent_indent="  ") + "\n")
        self.state.setdefault("turns", []).append(
            {"turn": clock["turn"], "time": clock["time"], "input": text, "narration": prose,
             "room": S.location(self.state, S.PLAYER), "perceived": lines,
             "switches": switches})
        self.next_turn()

    def trace(self):
        print(self.last_trace or "  no turn played yet")

    def toggle_verbose(self):
        self.verbose = not self.verbose
        print(f"  verbose {'on' if self.verbose else 'off'}")

    def pending(self):
        for p in self.state.get("pending", []):
            print(f"  [{p['turn']}] {p['actor']}: {p['action']}"
                  f"{' -> ' + p['target_text'] if p.get('target_text') else ''}")
        if not self.state.get("pending"):
            print("  no pending intents")

    def know(self, who, whom):
        rec = S.actor(self.state, self._id(who))
        rec.setdefault("known", []).append(self._id(whom))
        print("  ok")

    def set_mode(self, who, mode):
        cid = self._id(who)
        if self.state["chars"][cid]["mode"] == mode:
            print(f"  {cid} is already {mode}")
            return
        self._switch(cid, mode, "by hand")

    # --- dispatch ----------------------------------------------------------
    def handle(self, line):
        line = line.strip()
        if not line:
            return True
        if not line.startswith("/"):
            if self.debug:
                self.say("player", line.split())
            else:
                self.turn(line)
            return True
        try:
            parts = shlex.split(line[1:]) if '"' in line else line[1:].split()
        except ValueError:  # unbalanced quotes, e.g. an apostrophe
            parts = line[1:].split()
        cmd, args = parts[0].lower(), parts[1:]
        rest = line[1:].split(None, 1)[1] if len(parts) > 1 else ""
        table = {
            "say": lambda: self.say(args[0], args[1:]),
            "do": lambda: self.do(args[0], args[1:]),
            "move": lambda: self.move(*args),
            "next": self.next_turn,
            "roll": lambda: self.roll(*args),
            "look": lambda: self.look(*args),
            "who": self.who,
            "mind": lambda: self.mind(*args),
            "truth": self.truth,
            "log": lambda: self.log(*args),
            "prompt": lambda: self.prompt(*args),
            "act": lambda: self.act(*args),
            "pending": self.pending,
            "try": lambda: self.try_(rest),
            "turn": lambda: self.turn(rest),
            "trace": self.trace,
            "verbose": self.toggle_verbose,
            "resolve": self.resolve,
            "know": lambda: self.know(*args),
            "promote": lambda: self.set_mode(args[0], "agent"),
            "demote": lambda: self.set_mode(args[0], "managed"),
            "save": lambda: print(f"  saved {S.save_snapshot(self.state, self.run_dir)}"),
            "help": lambda: print(HELP),
            "dump": lambda: print(json.dumps(S.actor(self.state, self._id(args[0])), indent=2)),
        }
        if cmd in ("quit", "exit", "q"):
            return False
        if cmd not in table:
            print("  unknown command, try /help")
            return True
        try:
            table[cmd]()
        except (IndexError, TypeError):
            print("  wrong arguments, try /help")
        except (ValueError, KeyError) as e:
            print(f"  {e}")
        return True
