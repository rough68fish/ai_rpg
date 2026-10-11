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
                    line = f'From the {S.room_name(state, room)}, I hear {who}: "{speech}"'
                else:
                    line = f"I hear a loud noise from the {S.room_name(state, room)}."

        if line:
            perceptions[obs] = line
    return perceptions, checks


_SPEECH_TAG = re.compile(r"\{\w+\} [\w ]+?( to \{\w+\})?:?\s*")


def own_memory(state, event):
    """What the actor remembers of their own event. The outcome text is the
    truth of what happened (the GM's actor_text tends to say only what they
    tried), so it is put in first person, with their words after it."""
    text, speech = event.get("text", "").strip(), event.get("speech")
    if not text or _SPEECH_TAG.fullmatch(text) and len(text.split()) <= 5:
        # Just "{x} says to {y}:" - the words are the memory, or there is none.
        if not speech:
            return None
        return event.get("actor_text") or f'I said: "{speech}"'
    own = render(state, event["actor"], text).rstrip(":").rstrip()
    if speech and speech not in own:
        own += f' I said: "{speech}"'
    return own


# --- learning names --------------------------------------------------------
# A name is learned by hearing it, each person for themselves: the speaker
# introducing themselves ("my name is Karik"), someone being called by name
# while spoken to ("Steady yourself, Pip"), or someone in the room being
# introduced ("this is Father Tomas"). Only those in the room who made out the
# words learn it, and they learn the name as it was said.

_NAME = r"(?=([\w'-]+(?:\s+[\w'-]+)?))"  # a lookahead, so "I'm afraid I am Bran" finds Bran
# Phrases that can only be followed by a name, so "my name is karik" typed in
# lower case still counts; and "I am"/"I'm", which only count before a capital.
_INTRO_SURE = re.compile(r"\b(?:my name is|my name's|call me|they call me|i am called|i'm called)\s+"
                         + _NAME, re.IGNORECASE)
_INTRO_MAYBE = re.compile(r"\b(?:i am|i'm)\s+" + _NAME, re.IGNORECASE)
# Words after "I am" that are not names ("I am Sorry", "I'm Not sure").
_NOT_NAMES = {"i", "a", "an", "the", "not", "so", "sorry", "afraid", "here", "just", "sure",
              "fine", "well", "glad", "only", "but", "and", "no", "yes", "from", "your", "his",
              "her", "on", "in", "at", "to", "of", "going", "come", "looking", "told", "called"}


def _introduced_name(state, speech):
    """The name a speaker gives for themselves, e.g. "Karik" or "Father Tomas"."""
    for pattern, sure in ((_INTRO_SURE, True), (_INTRO_MAYBE, False)):
        for m in pattern.finditer(speech):
            words = m.group(1).split()
            if words[0].lower() in _NOT_NAMES or not (sure or words[0][0].isupper()):
                continue
            if len(words) == 2 and (words[1].lower() in _NOT_NAMES
                                    or not words[1][0].isupper() and words[0] not in S.titles(state)):
                words = words[:1]  # "Karik from the north"
            return " ".join(w[:1].upper() + w[1:] for w in words)
    return None


def _names_of(state, aid):
    """Ways a person's name can be said aloud."""
    if aid == S.PLAYER:
        p = state["world"]["player"]
        return {p["name"]} if p.get("name") and p.get("name") != p.get("short") else set()
    idn = state["chars"][aid]["identity"]
    words = idn["name"].replace("(", " ").replace(")", " ").split() + idn["short"].split()
    return {w for w in words if w not in S.titles(state)} | {idn["short"], idn["name"]}


def _said_name(state, speech, aid):
    """The name of `aid` as it was said in speech, with any title before it
    ("Father Aldric"), or None."""
    titles = "|".join(re.escape(t) for t in sorted(S.titles(state), key=len, reverse=True))
    for name in sorted(_names_of(state, aid), key=len, reverse=True):
        m = re.search(rf"(?:\b(?:{titles})\s+)?(?<![\w-]){re.escape(name)}(?![\w-])", speech)
        if m:
            # Keep a title that is part of how they are known ("Father Aldric"),
            # not a passing courtesy ("Master Karik").
            return m.group(0) if m.group(0) in _names_of(state, aid) else name
    return None


# Words that hand someone else's name to the listener: "this is Father Tomas",
# "meet Pip", "her name is Agnes". Only counts when a name of someone in the
# room follows, so "he is tired" teaches nothing.
_PRESENTS = re.compile(r"\b(?:this is|that is|this here is|here is|here's|meet|his name is|"
                       r"her name is|his name's|her name's|he is called|she is called|he's called|"
                       r"she's called|he is|she is|he's|she's|named|"
                       r"(?:may i|let me|allow me to) (?:present|introduce))\s+", re.IGNORECASE)


def _name_at(state, text, aid):
    """The name of `aid` if text starts with it (after an optional title), as
    _said_name gives it; else None."""
    titles = "|".join(re.escape(t) for t in sorted(S.titles(state), key=len, reverse=True))
    for name in sorted(_names_of(state, aid), key=len, reverse=True):
        m = re.match(rf"(?:(?:{titles})\s+)?{re.escape(name)}(?![\w-])", text, re.IGNORECASE)
        if m:
            said = m.group(0)
            proper = next((n for n in _names_of(state, aid) if n.lower() == said.lower()), None)
            return proper or name
    return None


def _presented(state, speech, actor, present):
    """[(id, name)] for people in the room the speaker introduces to others."""
    found = []
    for m in _PRESENTS.finditer(speech):
        rest = speech[m.end():]
        for other in present:
            if other == actor or any(o == other for o, _ in found):
                continue
            name = _name_at(state, rest, other)
            if name:
                found.append((other, name))
    return found


def names_heard(state, event):
    """[(id, name)] for every person whose name this event makes known to anyone
    in the room who hears the words."""
    speech, actor = event.get("speech"), event["actor"]
    if not speech or actor not in S.all_ids(state):
        return []
    found = []
    given = _introduced_name(state, speech)
    if given:
        if actor == S.PLAYER:
            p = state["world"]["player"]
            if p.get("name") == p.get("short"):  # the first name they give is theirs
                p["name"] = given
        found.append((actor, given))
    target = event.get("target")
    others = [o for o in S.in_room(state, event["room"]) if o != actor]
    if target not in others and len(others) == 1:
        target = others[0]  # spoken with nobody named: to the only one there
    if target in others:
        said = _said_name(state, speech, target)
        if said:
            found.append((target, said))
    found += [p for p in _presented(state, speech, actor, others) if p[0] not in dict(found)]
    return found


def _heard_words(event, line):
    return bool(event.get("speech")) and f'"{event["speech"]}"' in (line or "")


def apply(state, event, rng):
    """Perceive an event, write it into every perceiver's memory, and log it."""
    clock = state["world"]["clock"]
    perceptions, checks = perceive(state, event, rng)
    imp = event.get("importance", 3)
    own = own_memory(state, event) if event["actor"] in S.all_ids(state) else None
    perceptions_with_self = {event["actor"]: own, **perceptions} if own else perceptions
    for obs, line in perceptions_with_self.items():
        S.actor(state, obs).setdefault("memory", []).append(
            {"t": clock["turn"], "time": clock["time"], "text": line, "importance": imp,
             "by": event["actor"]})
    learned = []
    names = names_heard(state, event)
    # Hearers in the room who made out the words; the speaker knows what they said.
    hearers = [o for o, line in perceptions.items()
               if S.location(state, o) == event["room"] and _heard_words(event, line)]
    for whom, name in names:
        for who in hearers + [event["actor"]]:
            if S.learn_name(state, who, whom, name):
                learned.append(f"{who} knows {whom} as {name}")
    state["log"].append({"turn": clock["turn"], "time": clock["time"], "event": event,
                         "perceived_by": perceptions, "checks": checks,
                         **({"learned": learned} if learned else {})})
    return perceptions, checks


def arrive(state, aid, from_room, rng):
    """Move has already happened: let the new room notice the arrival. The
    mover gets no memory line; their departure event covers it."""
    clock = state["world"]["clock"]
    event = {"actor": aid, "room": S.location(state, aid),
             "text": f"{{{aid}}} comes in from the {S.room_name(state, from_room)}.", "volume": "silent", "subtle": False}
    perceptions, checks = perceive(state, event, rng)
    for obs, line in perceptions.items():
        S.actor(state, obs).setdefault("memory", []).append(
            {"t": clock["turn"], "time": clock["time"], "text": line, "importance": 3, "by": aid})
    state["log"].append({"turn": clock["turn"], "time": clock["time"], "event": event,
                         "perceived_by": perceptions, "checks": checks})
    return perceptions, checks
