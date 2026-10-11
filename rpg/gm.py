"""The GM's `resolve` call (see prompts.md, sections 1 and 2).

The GM sees everything and turns intents into events. For an uncertain action it
writes a success and a failure branch and names a check; Python rolls the d20,
keeps one branch, runs perception, then applies the branch's state changes after
validating each one. The model never sees a roll before writing both branches.
"""
import re

from . import llm
from . import perception as P
from . import state as S

ABILITIES = ("STR", "INT", "WIS", "DEX", "CON", "CHA")
VOLUMES = ("silent", "whisper", "normal", "loud")
WORLD = "world"  # actor id for things nobody does: a bell rings, a draught slams a door

SYSTEM = """You are the Game Master of a text role-playing game. You run the world, every
character the players are not directly dealing with, and the rules. You see
the whole truth of the world, including secrets and every character's hidden
ability scores. The players and the characters never see what you see.

Your duties:
1. Turn intentions into events. Players and characters only say what they
   TRY to do. You decide what actually happens, consistent with the world
   state, physical sense, and each actor's abilities.
2. Call for checks when the outcome is uncertain. You do not roll dice. You
   name the ability (STR, INT, WIS, DEX, CON, CHA), a modifier from +4 (easy)
   to -6 (very hard), and write BOTH a success and a failure version of what
   happens. The engine rolls d20 under ability + modifier and picks one.
   Trivial actions need no check. Impossible actions simply fail.
3. Keep secrets where they are. Never reveal hidden objects or another
   character's private knowledge unless an event in this turn actually
   uncovers it. A character's words can only contain what that character
   knows.
4. Run the background characters (mode "managed"). Move them, advance their
   goals, and give them small actions that fit who they are. Anything that
   happens to or near them must be something they could witness.
5. Keep the scene moving, toward the event named under Time if there is one. Let small,
   natural things happen (something dropped, a noise outside, someone calling
   a name) when the scene stalls, but never force a revelation.
6. Keep everything true to the Setting in the world state: objects, tools,
   clothing, how people speak and what they believe. Nothing from outside
   that time and place.

Writing events:
- Each event is one thing that happened, in plain objective third person,
  one or two sentences.
- Refer to people by id in braces, e.g. "{anna} hands {ben} a cup" if their
  ids are anna and ben. The
  engine rewrites this for each observer (a name if they know the person, a
  description if not, "me" for themselves).
- Give every event a room, a volume ("silent", "whisper", "normal", "loud")
  and set "subtle": true for things that are easy to miss.
- If someone speaks, put their exact words in "speech".
- Ability scores shape outcomes and how people come across, but never
  mention numbers or ability names in event text.

Answer only with JSON in the requested format."""

TASK = """Resolve every intent in order, then give each character listed under
MANAGED CHARACTERS TO TICK one small action (none if the list is empty).
If someone spoke to a managed character in RECENT EVENTS and they heard it,
that action is their spoken reply, in their own voice and using only what
they know. Return events, usually one per intent.

- An event is what its actor does and says, and how that turns out. If the
  intent includes speaking, put the actor's exact words in "speech" and the
  person spoken to in "target". Do not write how other agents react or what
  they say back: they decide that themselves on their own turn.
- The traveller's typed line is something they do, something they say, or
  both. A line with no verb like "I walk" or "I try" is usually just words
  spoken aloud. Put any words they say in "speech", as close to how they
  typed them as you can. A description such as "nods" never replaces what
  the traveller says.
- Talking, asking, answering, agreeing, looking around and walking somewhere
  need no check. Save checks for things that can really go wrong: lying,
  sneaking, stealing, forcing, searching, persuading someone against their
  interest.
- Speech in RECENT EVENTS has already been said; do not repeat it.
- "actor_text" is the actor's own first-person memory of what happened.

state_changes are commands the engine checks and applies, one per string.
If an outcome moves a person, changes who holds an object, opens a door or
uncovers something hidden, you MUST list the matching command, or it did not
happen. Commands:
  move <id> <room>      go to an adjacent room (the door must be unlocked)
  give <object> <id>    <id> now holds the object (picked up, handed over, stolen)
  drop <object>         the holder puts it down in the room they are in
  unlock <door>         the actor must hold the door's key; also: lock <door>
  reveal <object>       a hidden object is found; the actor must be in its room
  know <id> <id>        the first learns the second's name, e.g. after an introduction
Examples, with <...> standing for the real ids from WORLD STATE: "I take the
book" -> ["give <book id> player"]; "I go back to the hall" -> ["move player
<hall id>"]; "{<id>} pockets the key he was handed" -> ["give <key id> <id>"].
Use [] when nothing changes."""

_OUTCOME = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "speech": {"type": ["string", "null"]},
        "room": {"type": "string"},
        "volume": {"enum": list(VOLUMES)},
        "subtle": {"type": "boolean"},
        "target": {"type": ["string", "null"]},
        "actor_text": {"type": "string"},
        "importance": {"type": "integer", "minimum": 1, "maximum": 10},
        "state_changes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["text", "room", "volume", "subtle", "actor_text", "state_changes"],
}

RESOLVE_SCHEMA = {
    "type": "object",
    "properties": {
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "actor": {"type": "string"},
                    "check": {"type": ["object", "null"], "properties": {
                        "ability": {"enum": list(ABILITIES)},
                        "modifier": {"type": "integer", "minimum": -6, "maximum": 4},
                        "why": {"type": "string"}},
                        "required": ["ability", "modifier", "why"]},
                    "success": _OUTCOME,
                    "failure": _OUTCOME,
                },
                "required": ["actor", "check", "success"],
            },
        }
    },
    "required": ["events"],
}


# --- the prompt --------------------------------------------------------------

def _bullets(lines, empty="none"):
    return "\n".join(f"- {l}" for l in lines) if lines else f"- {empty}"


def describe_event(entry):
    """One log entry as a line for the GM (raw {id} text, with rolls)."""
    e = entry["event"]
    flags = e.get("volume", "silent") + (", subtle" if e.get("subtle") else "")
    line = f"[turn {entry['turn']}] {e['actor']} @ {e['room']} ({flags}): {e['text']}"
    if e.get("speech"):
        line += f' "{e["speech"]}"'
    c = entry.get("check")
    if c:
        line += (f" | check {c['ability']} {c['modifier']:+d}: rolled {c['roll']} vs {c['target']},"
                 f" {'success' if c['success'] else 'failure'}")
    line += f" | perceived by: {', '.join(entry['perceived_by']) or 'nobody'}"
    return line


def resolve_message(state, intents, tick=()):
    w = state["world"]
    rooms = [f"{r} (next to: {', '.join(d['adjacent'])}): {d['desc']}" for r, d in w["rooms"].items()]
    doors = [f"{d}: {'locked' if v['locked'] else 'unlocked'}, key: {v.get('key', 'none')}"
             f"{'. ' + v['noise'] if v.get('noise') else ''}" for d, v in w.get("doors", {}).items()]
    objects = []
    for oid, o in w["objects"].items():
        extra = " HIDDEN" if o.get("hidden") else ""
        if o.get("concealed"):
            extra += " CONCEALED (worn out of sight: nobody else can see it, so no event mentions it" \
                     " unless an event this turn actually exposes it)"
        if o.get("find"):
            extra += f" (to find: {o['find']})"
        objects.append(f"{oid} @ {o['location']}{extra}: {o['desc']}")

    chars = []
    for cid, rec in state["chars"].items():
        st = rec["stats"]
        chars.append(
            f"{rec['identity']['name']} ({cid}) | mode: {rec['mode']} | in: {rec['location']}\n"
            f"  stats: {' '.join(f'{a} {st[a]}' for a in ABILITIES)}\n"
            f"  traits: {'; '.join(rec.get('traits', []))}\n"
            f"  goals: {'; '.join(rec.get('goals', [])) or 'none'}\n"
            f"  carrying: {', '.join(S.carrying(state, cid)) or 'nothing'}")
    player = w["player"]
    known = ", ".join(player.get("known", [])) or "nobody"

    turn = w["clock"]["turn"]
    recent = [describe_event(e) for e in state["log"] if e["turn"] >= turn - 2]

    lines = []
    for it in intents:
        target = f" (toward: {it['target_text']})" if it.get("target_text") else ""
        verb = "typed" if it["actor"] == S.PLAYER else "tries"
        line = f"{it['actor']} (in {S.location(state, it['actor'])}) {verb}: \"{it['action']}\"{target}"
        if it.get("does") or it.get("says"):  # the traveller's line, already split
            line += (f"\n  does: {it.get('does') or 'nothing physical'}"
                     f"\n  says (exact words for \"speech\"): {it.get('says') or 'nothing'}")
        lines.append(line)

    return _pc(state, f"""WORLD STATE
Setting: {S.setting(state, 'gm')}
Time: {S.event_line(state)}, turn {turn}
Rooms:
{_bullets(rooms)}
Doors:
{_bullets(doors)}
Objects:
{_bullets(objects)}
Ground truth:
{_bullets(w.get('truth', []))}

CHARACTERS
{_bullets(chars)}
The player ("the traveller", id: player) is in: {player['location']}, carrying: \
{', '.join(S.carrying(state, S.PLAYER)) or 'nothing'}, knows the names of: {known}

RECENT EVENTS (last 3 turns, with rolls)
{_bullets(recent)}

INTENTS TO RESOLVE
{_bullets(lines)}

MANAGED CHARACTERS TO TICK
{_bullets(list(tick))}

{TASK}""")


# --- normalising the model's events ------------------------------------------

def _pc(state, text):
    """The engine's prompts call the player's character "the traveller"; use the
    module's own name for them instead."""
    name = S.player_name(state)
    return text.replace("the traveller", name).replace("The traveller", name[:1].upper() + name[1:])


def _name_patterns(state):
    """(regex, id) pairs that turn names the GM wrote out back into {id}, so each
    observer still sees only the names they know. Longest names first."""
    pc = S.player_name(state)
    pairs = [(pc, S.PLAYER), (pc[:1].upper() + pc[1:], S.PLAYER)]
    for cid, rec in state["chars"].items():
        idn = rec["identity"]
        names = {idn["name"], idn["short"], idn["short"].split()[-1]}
        pairs += [(n, cid) for n in names]
    pairs.sort(key=lambda p: -len(p[0]))
    return [(re.compile(r"(?<!\{)\b" + re.escape(n) + r"\b(?!\})"), cid) for n, cid in pairs]


def _braced(state, text):
    for pat, cid in _name_patterns(state):
        text = pat.sub("{" + cid + "}", text)
    return text


def _to_id(state, who):
    if not who:
        return None
    w = str(who).strip().strip("{}").lower()
    if w in S.player_words(state):
        return S.PLAYER
    if w in state["chars"]:
        return w
    for cid, rec in state["chars"].items():
        if w in (rec["identity"]["short"].lower(), rec["identity"]["name"].lower()):
            return cid
    return None


def _implied_target(state, actor, out, speech):
    """Speech with no target, in text that names exactly one other person:
    "{player} sits beside {agnes} and asks..." is spoken to Agnes."""
    if not speech:
        return None
    named = {m for m in re.findall(r"\{(\w+)\}", _braced(state, out.get("text", "")))
             if m != actor and (m == S.PLAYER or m in state["chars"])}
    return named.pop() if len(named) == 1 else None


def _event(state, actor, out):
    # A person acts where they are, whatever room the GM wrote (the keeper on the
    # stairs can't block a doorway in the kitchen). Only nobody's events, like a
    # bell, take the GM's room.
    if actor != WORLD:
        room = S.location(state, actor)
    else:
        room = out.get("room")
        if room not in state["world"]["rooms"]:
            room = state["world"]["player"]["location"]
    speech = out.get("speech") or None
    text = out.get("text", "")
    if not speech:
        # Words the GM wrote into the text belong in speech, where perception
        # (volume, hearing rolls) applies to them.
        m = re.search(r'["“]([^"”]{2,})["”]', text)
        if m:
            speech = m.group(1).strip()
            text = (text[:m.start()].rstrip(" ,") + text[m.end():]).strip()
            if not text.endswith((".", ":", "!", "?")):
                text += ":"
    volume = out.get("volume") if out.get("volume") in VOLUMES else ("normal" if speech else "silent")
    if speech and volume == "silent":
        volume = "normal"
    imp = out.get("importance")
    own = out.get("actor_text")
    # The actor's memory names people the way the actor knows them.
    own = P.render(state, actor, _braced(state, own)) if own and actor != WORLD else None
    return {"actor": actor, "room": room, "text": _braced(state, text),
            "speech": speech, "volume": volume, "subtle": bool(out.get("subtle")),
            "target": _to_id(state, out.get("target")) or _implied_target(state, actor, out, speech),
            "actor_text": own,
            "importance": min(10, max(1, imp)) if isinstance(imp, int) else 3}


# --- state changes -----------------------------------------------------------

def _door(state, name):
    doors = state["world"].get("doors", {})
    if name in doors:
        return name, doors[name]
    a, _, b = name.partition("-")
    flipped = f"{b}-{a}"
    return (flipped, doors[flipped]) if flipped in doors else (name, None)


def _spoken_names(state, cid):
    """Ways someone's name can be said aloud: "Pip (Philip)" -> "Pip", "Philip"."""
    idn = state["chars"][cid]["identity"]
    words = idn["name"].replace("(", " ").replace(")", " ").split() + idn["short"].split()
    return {w for w in words if w not in S.titles(state)} | {idn["short"]}


def apply_change(state, actor, cmd, rng, event=None):
    """Validate and apply one state change. Returns (ok, message, arrival or None).
    `event` is the event the change came with, for checks that depend on it."""
    parts = cmd.split()
    if not parts:
        return False, "empty", None
    verb, args = parts[0].lower(), [p.strip(",.") for p in parts[1:]]
    objs, rooms = state["world"]["objects"], state["world"]["rooms"]
    try:
        if verb == "move":
            who, room = _to_id(state, args[0]), args[1].lower()
            if not who or room not in rooms:
                return False, "unknown person or room", None
            if who == S.PLAYER and actor != S.PLAYER:
                return False, "only the traveller decides where the traveller goes", None
            here = S.location(state, who)
            if room == here:
                return False, f"{who} is already in the {room}", None
            route = S.path(state, here, room)
            if route is None:
                return False, f"no way from the {here} to the {room}: the door is locked", None
            # Walk room by room; whoever is in each room sees them come in.
            seen, checks, prev = {}, [], here
            for step in route:
                S.actor(state, who)["location"] = step
                p, c = P.arrive(state, who, prev, rng)
                seen.update(p)
                checks += c
                prev = step
            return True, f"{who}: {' -> '.join([here] + route)}", (seen, checks)

        if verb == "give":
            oid, who = args[0], _to_id(state, args[1])
            if oid not in objs or not who:
                return False, "unknown object or person", None
            if objs[oid].get("hidden"):
                return False, f"{oid} is still hidden", None
            if S.object_room(state, oid) != S.location(state, who):
                return False, f"{oid} is not in the same room as {who}", None
            objs[oid]["location"] = who
            return True, f"{oid} -> {who}", None

        if verb == "drop":
            oid = args[0]
            holder = objs.get(oid, {}).get("location")
            if holder != S.PLAYER and holder not in state["chars"]:
                return False, f"nobody is holding {oid}", None
            objs[oid]["location"] = S.location(state, holder)
            return True, f"{oid} dropped in the {objs[oid]['location']}", None

        if verb in ("unlock", "lock"):
            did, door = _door(state, args[0])
            if not door:
                return False, f"no door {args[0]}", None
            if actor == WORLD or S.location(state, actor) not in S.door_rooms(did):
                return False, f"{actor} is not at the {did} door", None
            if objs.get(door.get("key"), {}).get("location") != actor:
                return False, f"{actor} does not hold {door.get('key')}", None
            door["locked"] = verb == "lock"
            return True, f"{did} {verb}ed", None

        if verb == "reveal":
            oid = args[0]
            if oid not in objs:
                return False, f"no object {oid}", None
            if actor == WORLD or S.object_room(state, oid) != S.location(state, actor):
                return False, f"{actor} is not where {oid} is", None
            loc = objs[oid]["location"]
            if loc.startswith("behind ") and objs.get(loc[7:], {}).get("hidden"):
                return False, f"{loc[7:]} has not been found yet", None
            objs[oid]["hidden"] = False
            return True, f"{oid} revealed", None

        if verb == "know":
            who, whom = _to_id(state, args[0]), _to_id(state, args[1])
            if not who or not whom or S.location(state, who) != S.location(state, whom):
                return False, "unknown people, or not in the same room", None
            # A name is learned by hearing it said in this event. The traveller has
            # no modelled name, so any speech of theirs can be an introduction.
            speech = (event or {}).get("speech") or ""
            if whom == S.PLAYER:
                heard = bool(speech) and event.get("actor") == S.PLAYER
            else:
                heard = any(n.lower() in speech.lower() for n in _spoken_names(state, whom))
            if not heard:
                return False, f"nobody said {whom}'s name", None
            known = S.actor(state, who).setdefault("known", [])
            if whom not in known:
                known.append(whom)
            return True, f"{who} knows {whom}", None
    except IndexError:
        return False, "missing arguments", None
    return False, f"unknown command '{verb}'", None


# --- running a resolve call ---------------------------------------------------

def _drop_echo(out, said):
    """A later traveller event that repeats words from their own line (the GM
    split one line into two events): keep the action, drop the repeat."""
    out = dict(out)
    flat_said = _flat(said)
    if out.get("speech") and _flat(out["speech"]) in flat_said:
        out["speech"] = None
    out["text"] = re.sub(r'["“]([^"”]*)["”]',
                         lambda m: "" if _flat(m.group(1)) in flat_said else m.group(0), out.get("text", ""))
    if out.get("actor_text") and _flat(out["actor_text"]) in flat_said:
        out["actor_text"] = None
    return out


def _flat(text):
    """Letters and digits only, for comparing wordings."""
    return re.sub(r"\W", "", text.lower())


def play_event(state, raw, rng, says=None, volume=None, defer=False):
    """Roll, keep one branch, perceive it, apply its changes. Returns a report.
    `says` is the traveller's own words, which the event carries whatever the
    GM wrote (speaking can't fail; what it achieves can). With `defer`, the
    traveller is also walking somewhere: the words are kept out of this event
    and said on arrival (see resolve)."""
    actor = _to_id(state, raw.get("actor")) or WORLD
    report = {"actor": actor, "check": None, "branch": "success", "other": None}
    check = raw.get("check") if isinstance(raw.get("check"), dict) else None
    if check and check.get("ability") in ABILITIES and actor != WORLD:
        mod = max(-6, min(4, int(check.get("modifier", 0))))
        roll = S.roll_under(state, actor, check["ability"], mod, rng)
        roll["why"] = check.get("why", "")
        report["check"] = roll
        report["branch"] = "success" if roll["success"] else "failure"

    succ, fail = raw.get("success") or {}, raw.get("failure")
    if report["branch"] == "failure":
        if not fail:
            fail = {"text": "{%s} tries, but it does not work." % actor, "room": succ.get("room"),
                    "volume": "silent", "subtle": False, "actor_text": "I tried, but it did not work."}
            report["note"] = "no failure branch given; used a plain failure"
        out, report["other"] = fail, succ.get("text")
    else:
        out, report["other"] = succ, fail.get("text") if fail else None

    event = _event(state, actor, out)
    if says and defer:
        report["deferred"] = {"says": says, "volume": volume, "target": event["target"]}
        core = says.rstrip(".!? ")
        # Whatever words the GM put in the walking event (quoted, or verbatim)
        # belong to the arrival, not the room being left.
        text = re.sub(r',?\s*(?:and\s+)?(?:saying|says|said)?:?\s*["“][^"”]*["”]', "",
                      event["text"])
        at = text.lower().find(core.lower())
        if core and at >= 0:
            text = text[:at]
        event["text"] = text.rstrip(' ,:"“') + "."
        event.update(speech=None, target=None)
        if event["volume"] in ("whisper", "normal"):
            event["volume"] = "silent"
        if event.get("actor_text") and _flat(core) in _flat(event["actor_text"]):
            event["actor_text"] = None  # the words get their own memory line
    elif says:
        if not event["speech"]:
            # The GM's volume described a gesture, not these words.
            report["note"] = "traveller's words restored"
            event["volume"] = "normal"
        elif _flat(event["speech"]) != _flat(says):
            report["note"] = f"GM's wording replaced: {event['speech']!r}"
        event["speech"] = says
        if event["volume"] == "silent":
            event["volume"] = "normal"
        if volume:  # how the player said to say it
            event["volume"] = volume
        # The words now live in "speech"; take any copy out of the text so
        # listeners don't hear them twice, and make sure the traveller's own
        # memory has them exactly once.
        core = says.rstrip(".!? ")
        at = event["text"].lower().find(core.lower())
        if core and at >= 0:
            event["text"] = (event["text"][:at].rstrip(' ,"“') + ":").replace("::", ":")
        own = event.get("actor_text") or ""
        if _flat(core) not in _flat(own):
            event["actor_text"] = (own.rstrip(". ") + f'. I say: "{says}"').lstrip(". ")
    # A move happens where the actor starts; P.arrive covers the new room.
    for cmd in out.get("state_changes") or []:
        p = cmd.split()
        if len(p) == 3 and p[0].lower() == "move" and _to_id(state, p[1]) == actor and actor != WORLD:
            event["room"] = S.location(state, actor)
    report["event"] = event
    report["perceptions"], report["checks"] = P.apply(state, event, rng)
    entry = state["log"][-1]
    entry["gm"] = True
    if report["check"]:
        entry["check"] = report["check"]
        entry["branch"] = report["branch"]

    report["changes"] = []
    for cmd in out.get("state_changes") or []:
        ok, msg, arrival = apply_change(state, actor, cmd, rng, event)
        report["changes"].append({"cmd": cmd, "ok": ok, "msg": msg, "arrival": arrival})
        if not ok and actor == S.PLAYER and cmd.lower().startswith("move player"):
            # Otherwise the narration happily walks the traveller somewhere they never got.
            clock = state["world"]["clock"]
            S.actor(state, S.PLAYER)["memory"].append(
                {"t": clock["turn"], "time": clock["time"], "importance": 4, "by": S.PLAYER,
                 "text": f"I could not get there ({msg}); I am still in the {S.location(state, S.PLAYER)}."})
    entry["state_changes"] = [{k: c[k] for k in ("cmd", "ok", "msg")} for c in report["changes"]]
    return report


def resolve(state, intents, client, rng, tick=()):
    """One `resolve` call. Returns (reports, stats)."""
    reply, stats = client.chat(SYSTEM, resolve_message(state, intents, tick),
                               RESOLVE_SCHEMA, llm.TEMP_RESOLVE)
    events = reply.get("events") or []
    says = next((it["says"] for it in intents if it["actor"] == S.PLAYER and it.get("says")), None)
    volume = next((it.get("volume") for it in intents if it["actor"] == S.PLAYER and it.get("says")), None)
    if says and not any(_to_id(state, e.get("actor")) == S.PLAYER for e in events):
        # The GM skipped the traveller entirely; they still said it.
        events.insert(0, {"actor": S.PLAYER, "check": None, "success": {
            "text": "{player} says:", "room": S.location(state, S.PLAYER), "volume": "normal",
            "subtle": False, "actor_text": ""}})
    goes = next((it["goes_to"] for it in intents if it["actor"] == S.PLAYER and it.get("goes_to")), None)
    said = says
    reports = []
    player_idx = [i for i, e in enumerate(events) if _to_id(state, e.get("actor")) == S.PLAYER]
    last_player = player_idx[-1] if player_idx else -1
    for i, raw in enumerate(events):
        first_player = says and _to_id(state, raw.get("actor")) == S.PLAYER
        if said and not first_player and _to_id(state, raw.get("actor")) == S.PLAYER:
            for key in ("success", "failure"):
                if raw.get(key):
                    raw[key] = _drop_echo(raw[key], said)
        reports.append(play_event(state, raw, rng, *((says, volume, bool(goes)) if first_player else ())))
        if first_player:
            says = None
        if i == last_player:
            # The traveller's move and words land before anyone else acts.
            _finish_player(state, reports, goes, rng)
    if last_player < 0:
        _finish_player(state, reports, goes, rng)
    return reports, stats


def _finish_player(state, reports, goes, rng):
    """After the traveller's own events: make sure they got where they were going
    (the GM may only have narrated the walk), then say any words held back for
    the arrival."""
    moved = any(c["ok"] and c["cmd"].lower().startswith("move player")
                for r in reports for c in r["changes"])
    if goes and not moved and S.location(state, S.PLAYER) != goes:
        mine = next((r for r in reports if r["actor"] == S.PLAYER), None)
        ok, msg, arrival = apply_change(state, S.PLAYER, f"move player {goes}", rng)
        change = {"cmd": f"move player {goes}", "ok": ok, "msg": msg + " (from the typed line)",
                  "arrival": arrival}
        if mine:
            mine["changes"].append(change)
        if not ok:
            clock = state["world"]["clock"]
            S.actor(state, S.PLAYER)["memory"].append(
                {"t": clock["turn"], "time": clock["time"], "importance": 4, "by": S.PLAYER,
                 "text": f"I could not get there ({msg}); I am still in the {S.location(state, S.PLAYER)}."})
    later = next((r.pop("deferred") for r in reports if r.get("deferred")), None)
    if later:
        reports.append(_say_on_arrival(state, later, rng))


def _say_on_arrival(state, said, rng):
    """The traveller walked and talked: the words are said where they ended up."""
    here = S.location(state, S.PLAYER)
    tid = said["target"] if said["target"] and S.location(state, said["target"]) == here else None
    vol = said["volume"] or "normal"
    verb = {"whisper": "whispers", "loud": "calls out"}.get(vol, "says")
    to = f" to {{{tid}}}" if tid else ""
    to_me = f" to {S.name_for(state, S.PLAYER, tid)}" if tid else ""
    first = {"whisper": "whisper", "loud": "call out"}.get(vol, "say")
    event = {"actor": S.PLAYER, "room": here, "text": f"{{player}} {verb}{to}:", "speech": said["says"],
             "volume": vol, "subtle": False, "target": tid, "importance": 4,
             "actor_text": f'I {first}{to_me}: "{said["says"]}"'}
    perceptions, checks = P.apply(state, event, rng)
    state["log"][-1]["gm"] = True
    return {"actor": S.PLAYER, "check": None, "branch": "success", "other": None, "event": event,
            "perceptions": perceptions, "checks": checks, "changes": [],
            "note": "said on arrival"}


# --- narrate -------------------------------------------------------------------

NARRATE_SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}


def narrate_message(state, lines):
    """prompts.md section 3. Built only from what the traveller perceived, so it
    carries no ground truth; the GM system prompt itself holds none either."""
    player = state["world"]["player"]
    room = player["location"]
    visible = [f"{S.name_for(state, S.PLAYER, o)} ({S.appearance(state, o)})"
               if S.knows(state, S.PLAYER, o) else S.appearance(state, o)
               for o in S.in_room(state, room) if o != S.PLAYER]
    known = [S.short(state, o) for o in player.get("known", [])]
    return _pc(state, f"""Write what the traveller experiences this turn, in second person, present
tense. Use ONLY these events, which are the ones the traveller perceived.
Do not add facts, objects or motives that are not in them. Describe people by
how they look and sound unless the traveller has learned their name. Use
words and details that fit the setting below.

Setting: {S.setting(state, 'gm')}
Where the traveller is: the {S.room_name(state, room)}. {state['world']['rooms'][room]['desc']}
Who the traveller can see: {', '.join(visible) or 'nobody'}
Names the traveller knows: {', '.join(known) or 'none'}

Perceived events (the traveller's own memory, "I" is the traveller):
{_bullets(lines, 'nothing happens')}

Write 2 to 6 sentences of plain prose: this is read by the player, so no
{{braces}} or ids. If someone spoke directly to the traveller, end by quoting
it; if nobody did, end without a quote. Do not ask the player what they do
next.""")


def _unbrace(state, text):
    """Any {id} the narrator still wrote becomes how the traveller knows them."""
    def sub(m):
        cid = m.group(1)
        if cid in state["chars"]:
            name = S.name_for(state, S.PLAYER, cid)
        else:
            name = "you" if cid == S.PLAYER else cid.replace("_", " ")
        before = m.string[:m.start()].rstrip()
        return name[0].upper() + name[1:] if not before or before[-1] in '.!?"' else name
    return re.sub(r"\{(\w+)\}", sub, text)


def narrate(state, lines, client):
    """Returns (prose, stats)."""
    reply, stats = client.chat(SYSTEM, narrate_message(state, lines), NARRATE_SCHEMA, llm.TEMP_CHARACTER)
    return _tidy_quotes(_unbrace(state, reply.get("text", "").strip())), stats


def _tidy_quotes(text):
    """The narrator sometimes opens a quote for words nobody said (it was told to
    end on what was said to the traveller). Drop a bare opening quote at the end;
    close any other quote left open."""
    text = re.sub(r'\s*["“]\s*$', "", text)
    if (text.count('"') + text.count("“") + text.count("”")) % 2:
        text += '"'
    return text


# --- splitting the traveller's typed line ----------------------------------------

SPLIT_SCHEMA = {
    "type": "object",
    "properties": {"does": {"type": ["string", "null"]}, "says": {"type": ["string", "null"]}},
    "required": ["does", "says", "goes_to"],
}

SPLIT_PROMPT = """A player typed this line for their character, the traveller, in a text
role-playing game. Split it into what the traveller physically does and the
exact words the traveller says out loud.

- "say ...", "ask him ...", "tell her ..." and plain sentences with no
  physical action ("I agree, it must be true", "no thank you") are speech.
- Put speech in the traveller's own voice, as they would say it to the
  listener: "ask her how long she has worked here" becomes "How long have
  you worked here?". Otherwise keep the player's wording.
- "does" is only the physical part, e.g. "walks over to the door". Leave out
  the speaking itself.
- "volume": how the words are said: "whisper", "normal", or "loud" (shouting,
  calling out, saying it loudly or clearly to someone hard of hearing).
- "goes_to": if the traveller moves to a different room this turn, the room
  they end up in; otherwise null. Rooms, with what is in them:
{rooms}
  The traveller is now in the {here}.
- Use null for a part that is missing.

The line: "{line}\""""


def split_line(state, line, client):
    """does, says, goes_to and volume for the traveller's typed line. Speech and movement
    are then guaranteed to happen, whatever the GM does with them."""
    rooms = state["world"]["rooms"]
    schema = {**SPLIT_SCHEMA, "properties": {**SPLIT_SCHEMA["properties"],
                                             "goes_to": {"enum": list(rooms) + [None]},
                                             "volume": {"enum": ["whisper", "normal", "loud"]}}}
    room_lines = "\n".join(f"  - {r}: {d['desc']}" for r, d in rooms.items())
    prompt = (_pc(state, SPLIT_PROMPT).replace("{rooms}", room_lines)
              .replace("{here}", S.location(state, S.PLAYER))
              .replace("{line}", line.replace('"', "'")))
    reply, _ = client.chat("You split player input for a game engine. Answer only with JSON.",
                           prompt, schema, 0.2)
    goes = reply.get("goes_to")
    if goes == S.location(state, S.PLAYER) or goes not in rooms:
        goes = None
    volume = reply.get("volume") if reply.get("volume") in ("whisper", "loud") else "normal"
    return {"does": reply.get("does") or None, "says": reply.get("says") or None,
            "goes_to": goes, "volume": volume}
