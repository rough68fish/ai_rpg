"""Who perceives an event, and what they remember of it.

An event is a dict:
  actor    id of who did it ("player" for the traveller)
  room     where it happened
  text     objective description; refer to people as {id}, e.g. "{pip} drops a candle"
  speech   exact words, or None
  volume   "silent" | "whisper" | "normal" | "loud"
  subtle   True if easy to miss
  target   id spoken to or acted on, or None
  importance  1-10, how memorable
  actor_text  optional first-person line for the actor's own memory

Rules (see framework.md):
  same room      see actions, hear normal and loud speech
  adjacent room  hear loud speech and noises, without seeing who
  whisper        target hears; others in the room need a WIS -4 check for the words
  subtle         others in the room (not the target) need a WIS check to notice at all
  hearing mods   e.g. Martha's -4 add a WIS check to every hearing
"""
import re

from . import state as S

# WIS modifiers for perception checks. Easy hearing is automatic for normal
# ears and only rolled for someone with a hearing penalty.
HEAR_SAME_ROOM = 6
HEAR_LOUD_SAME_ROOM = 10  # raised voices are hard to miss, even for poor ears
HEAR_WHISPER_TARGET = 4
HEAR_WHISPER_OVERHEAR = -4
HEAR_ADJACENT_LOUD = 2
NOTICE_SUBTLE = 0


_IRREGULAR = {"is": "am", "was": "was", "has": "have", "does": "do", "goes": "go"}


def _first_person(verb):
    """'pauses' -> 'pause', 'watches' -> 'watch', 'tries' -> 'try'. Good enough for
    the GM's plain present-tense event text."""
    v = verb.lower()
    if v in _IRREGULAR:
        return _IRREGULAR[v]
    if v.endswith("ies") and len(v) > 4:
        return verb[:-3] + "y"
    if v.endswith(("ches", "shes", "sses", "xes", "zes", "oes")):
        return verb[:-2]
    if v.endswith("s") and not v.endswith("ss"):
        return verb[:-1]
    return verb


def render(state, observer, text):
    """Replace {id} placeholders with how this observer knows each person."""
    def sub(m):
        aid, possessive = m.group(1), m.group(2)
        if aid == observer:
            if possessive:
                return "my"
            # Subject at the start of a sentence: marked so the verb can be fixed.
            before = text[:m.start()].rstrip()
            return "\0I" if not before or before[-1] in ".!?;" else "me"
        if aid == S.PLAYER or aid in state["chars"]:
            return S.name_for(state, observer, aid) + (possessive or "")
        return m.group(0)
    out = re.sub(r"\{(\w+)\}('s)?", sub, text)
    out = re.sub(r"\0I (\w+)", lambda m: "I " + _first_person(m.group(1)), out).replace("\0", "")
    return out[0].upper() + out[1:] if out else out


def _hearing_mod(state, observer):
    rec = S.actor(state, observer)
    return rec.get("perception_mods", {}).get("hearing", 0)


def _hears(state, observer, mod, auto, rng, checks):
    """Does observer hear? auto means no roll for normal ears; a hearing
    penalty (Martha) always forces a WIS roll."""
    hmod = _hearing_mod(state, observer)
    if auto and hmod == 0:
        return True
    c = S.roll_under(state, observer, "WIS", mod + hmod, rng)
    c["why"] = "hearing"
    checks.append(c)
    return c["success"]


def perceive(state, event, rng):
    """Work out what each observer perceives.

    Returns (perceptions, checks): perceptions maps observer id -> first-person line,
    checks is the list of WIS rolls made (for the log, never shown to characters).
    """
    perceptions, checks = {}, []
    room, actor = event["room"], event["actor"]
    speech, volume = event.get("speech"), event.get("volume", "normal")
    target, subtle = event.get("target"), event.get("subtle", False)

    for obs in S.all_ids(state):
        if obs == actor:
            continue
        here = S.location(state, obs) == room
        near = room in S.adjacent(state, S.location(state, obs))
        line = None

        if here:
            if subtle and obs != target:  # whoever it is aimed at always notices
                c = S.roll_under(state, obs, "WIS", NOTICE_SUBTLE, rng)
                c["why"] = "notice subtle"
                checks.append(c)
                if not c["success"]:
                    continue
            seen = render(state, obs, event["text"])
            if speech:
                who = S.name_for(state, obs, actor)
                if volume == "whisper" and obs != target:
                    if _hears(state, obs, HEAR_WHISPER_OVERHEAR, False, rng, checks):
                        line = f'{seen} I overhear the whisper: "{speech}"'
                    else:
                        line = f"{seen} I can't make out the words."
                else:
                    base = {"whisper": HEAR_WHISPER_TARGET, "loud": HEAR_LOUD_SAME_ROOM}.get(volume, HEAR_SAME_ROOM)
                    if _hears(state, obs, base, True, rng, checks):
                        line = f'{seen} "{speech}"'
                    else:
                        line = f"{seen} {who[0].upper() + who[1:]} says something I can't quite catch."
            else:
                line = seen
        elif near and volume == "loud":
            if _hears(state, obs, HEAR_ADJACENT_LOUD, True, rng, checks):
                if speech:
                    who = S.short(state, actor) if S.knows(state, obs, actor) else "an unfamiliar voice"
                    line = f'From the {room}, I hear {who}: "{speech}"'
                else:
                    line = f"I hear a loud noise from the {room}."

        if line:
            perceptions[obs] = line
    return perceptions, checks


def apply(state, event, rng):
    """Perceive an event, write it into every perceiver's memory, and log it."""
    clock = state["world"]["clock"]
    perceptions, checks = perceive(state, event, rng)
    imp = event.get("importance", 3)
    # The actor remembers doing it, in first person if the event provides that.
    if event["actor"] in S.all_ids(state):
        own = event.get("actor_text") or "I did this: " + render(state, event["actor"], event["text"])
        perceptions_with_self = {event["actor"]: own, **perceptions}
    else:
        perceptions_with_self = perceptions
    for obs, line in perceptions_with_self.items():
        S.actor(state, obs).setdefault("memory", []).append(
            {"t": clock["turn"], "time": clock["time"], "text": line, "importance": imp,
             "by": event["actor"]})
    state["log"].append({"turn": clock["turn"], "time": clock["time"], "event": event,
                         "perceived_by": perceptions, "checks": checks})
    return perceptions, checks


def arrive(state, aid, from_room, rng):
    """Move has already happened: let the new room notice the arrival. The
    mover gets no memory line; their departure event covers it."""
    clock = state["world"]["clock"]
    event = {"actor": aid, "room": S.location(state, aid),
             "text": f"{{{aid}}} comes in from the {from_room}.", "volume": "silent", "subtle": False}
    perceptions, checks = perceive(state, event, rng)
    for obs, line in perceptions.items():
        S.actor(state, obs).setdefault("memory", []).append(
            {"t": clock["turn"], "time": clock["time"], "text": line, "importance": 3, "by": aid})
    state["log"].append({"turn": clock["turn"], "time": clock["time"], "event": event,
                         "perceived_by": perceptions, "checks": checks})
    return perceptions, checks
