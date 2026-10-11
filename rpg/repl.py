"""Stage 1 REPL: no model calls. You type events by hand and inspect what each
character perceived, remembers and would be prompted with."""
import json
import shlex
import textwrap

from . import perception as P
from . import prompts
from . import state as S

FIRST_PERSON = {"whisper": "whisper", "loud": "call out", "normal": "say"}

HELP = """\
Plain text            the traveller says it out loud (normal volume)
/say <who> [whisper|loud] [@target] <words>
                      someone speaks; <who> is an id or 'player'
/do <who> [subtle] [loud] [@target] <text>
                      someone acts; write people as {id}, e.g. /do pip {pip} drops a candle
/move <who> <room>    move to an adjacent room (locked doors stop you)
/next                 advance the clock one turn (one minute) and save
/roll <who> <STAT> [mod]  roll d20 under STAT + mod
/look [who]           the room as someone sees it (default: player)
/who                  everyone's location and mode
/mind <who>           memory, beliefs and goals (what they think)
/truth                ground truth and hidden objects (what is)
/log [n]              last n events with perception rolls (default 5)
/prompt <who>         the character prompt that would be sent, with leakage check
/know <who> <whom>    <who> learns <whom>'s name
/promote <who>, /demote <who>   switch agent / managed
/save                 save a snapshot now
/help, /quit"""


class Game:
    def __init__(self, state, run_dir, rng):
        self.state, self.run_dir, self.rng = state, run_dir, rng

    # --- helpers ---------------------------------------------------------
    def _id(self, who):
        who = who.lower()
        if who in ("player", "me", "traveller"):
            return S.PLAYER
        if who in self.state["chars"]:
            return who
        raise ValueError(f"unknown character '{who}'")

    def _emit(self, event):
        perceptions, checks = P.apply(self.state, event, self.rng)
        for c in checks:
            mark = "ok" if c["success"] else "miss"
            print(f"  [roll] {c['actor']} {c['ability']} {c.get('why', '')}: "
                  f"{c['roll']} vs {c['target']} {mark}")
        for obs, line in perceptions.items():
            print(f"  -> {obs}: {line}")
        if not perceptions:
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
        speech = " ".join(words)
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
        perceptions, _ = P.perceive(self.state, {"actor": aid, "room": room,
                                    "text": f"{{{aid}}} comes in from the {here}.",
                                    "volume": "silent", "subtle": False}, self.rng)
        clock = self.state["world"]["clock"]
        for obs, line in perceptions.items():
            S.actor(self.state, obs)["memory"].append(
                {"t": clock["turn"], "time": clock["time"], "text": line, "importance": 3})
            print(f"  -> {obs}: {line}")

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
        print(f"  {room}: {self.state['world']['rooms'][room]['desc']}")
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
            for c in entry["checks"]:
                print(f"      roll {c['actor']} {c['ability']}: {c['roll']} vs {c['target']}")
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

    def know(self, who, whom):
        rec = S.actor(self.state, self._id(who))
        rec.setdefault("known", []).append(self._id(whom))
        print("  ok")

    def set_mode(self, who, mode):
        self.state["chars"][self._id(who)]["mode"] = mode
        print(f"  {who} is now {mode}")

    # --- dispatch ----------------------------------------------------------
    def handle(self, line):
        line = line.strip()
        if not line:
            return True
        if not line.startswith("/"):
            self.say("player", line.split())
            return True
        parts = shlex.split(line[1:]) if '"' in line else line[1:].split()
        cmd, args = parts[0].lower(), parts[1:]
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
