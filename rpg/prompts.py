"""Builds character prompts from a character's own record (see prompts.md, section 5).

Nothing here reads world truth, hidden objects or stats, and check_leakage()
asserts that none of them slipped in.
"""
from . import selfimage
from . import state as S

MAX_MEMORIES = 15   # older memories, picked by importance
RECENT_TURNS = 6    # memories from this many recent turns are always kept, in full

RULES = """Rules:
- You only know what is written here and what you remember. If something is
  not in your memories, you do not know it. Do not guess at hidden facts as
  if they were true.
- You decide what you TRY to do and say. You do not decide whether it works;
  the world will tell you what happened.
- Speak the way you would really speak. Keep speech short: one to three
  sentences, the way people talk while they are busy.
- Keep track of who said what. Answer the person who actually spoke to you,
  and call people only by names you know. Someone whose name you don't know
  is "the stranger" or how they look; never give them another person's name.
- You may choose to do nothing, keep working, or stay silent.
- "thought" is private. No one else ever sees it. Be honest there.
- Answer only with JSON in the requested format.

Reply fields:
- thought: your private thought, one or two sentences
- speech: exactly what you say out loud, or null to stay silent
- volume: whisper, normal or loud
- target: the person you speak to or act on, named the way you know them, or null
- action: what you physically try to do, or null
- mood: one or two words
- new_beliefs, drop_beliefs, new_goals, done_goals: lists of short lines, usually
  empty. To drop a belief or finish a goal, copy its line exactly."""


def _bullets(items, empty="nothing"):
    return "\n".join(f"- {i}" for i in items) if items else f"- {empty}"


def system_message(state, cid):
    rec = state["chars"][cid]
    idn = rec["identity"]
    rels = [f"{S.short(state, other)}: {feeling}"
            for other, feeling in idn.get("relationships", {}).items()]
    era = S.setting(state, "era")
    rules = RULES.replace("Rules:\n", f"Rules:\n- {era}\n", 1) if era else RULES
    return f"""You are {idn['name']}, {idn['age']}, {idn['role']}. You are a person
in a story, not an assistant. Stay fully in character at all times.

Where you live: {S.setting(state, 'known') or 'a small village'}

Who you are: {idn['personality']}
How you speak: {idn['voice']}
About yourself:
{_bullets(selfimage.about_yourself(rec))}

What you know that others don't:
{_bullets(idn.get('secrets', []), 'nothing special')}

How you feel about people:
{_bullets(rels, 'no one in particular')}

{rules}"""


def seen_count(rec):
    """How many memories the character has already been prompted with. Before the
    first call that is the setup memories (the ones without a turn number)."""
    if "memories_seen" in rec:
        return rec["memories_seen"]
    return sum(1 for m in rec.get("memory", []) if not isinstance(m["t"], int))


def _select_memories(memories, turn, limit=MAX_MEMORIES, recent_turns=RECENT_TURNS):
    """Everything from the last few turns, so a conversation is never cut to one
    side of it, plus the most important older memories. Returned in original
    (chronological) order."""
    recent = {i for i, m in enumerate(memories)
              if isinstance(m["t"], int) and m["t"] > turn - recent_turns}
    older = [i for i in range(len(memories)) if i not in recent]
    if len(older) > limit:
        older = sorted(older, key=lambda i: (memories[i]["importance"], i), reverse=True)[:limit]
    return [memories[i] for i in sorted(recent.union(older))]


def _when(entry):
    return f"turn {entry['t']} ({entry.get('time', '?')})" if isinstance(entry["t"], int) else entry["t"]


def user_message(state, cid):
    rec = state["chars"][cid]
    world = state["world"]
    room = rec["location"]
    memories = rec.get("memory", [])
    seen = seen_count(rec)
    old = _select_memories(memories[:seen], world["clock"]["turn"])
    new = memories[seen:]
    visible = [S.name_for(state, cid, o) + ("" if S.knows(state, cid, o) else " (name unknown)")
               for o in S.in_room(state, room) if o != cid]
    return f"""{S.time_line(state)}
You are in the {S.room_name(state, room)}: {world['rooms'][room]['desc']}
You can see: {', '.join(visible) if visible else 'nobody'}.

What you believe:
{_bullets(rec.get('beliefs', []))}

What you want right now:
{_bullets(rec.get('goals', []))}

What you remember (oldest first):
{_bullets([f"{_when(m)}: {m['text']}" for m in old], 'nothing yet')}

What just happened:
{_bullets([m['text'] for m in new], 'nothing new')}

What do you do?"""


CHARACTER_SCHEMA = {
    "type": "object",
    "properties": {
        "thought": {"type": "string"},
        "speech": {"type": ["string", "null"]},
        "volume": {"enum": ["whisper", "normal", "loud"]},
        "target": {"type": ["string", "null"]},
        "action": {"type": ["string", "null"]},
        "mood": {"type": "string"},
        "new_beliefs": {"type": "array", "items": {"type": "string"}},
        "drop_beliefs": {"type": "array", "items": {"type": "string"}},
        "new_goals": {"type": "array", "items": {"type": "string"}},
        "done_goals": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["thought", "speech", "volume", "target", "action", "mood"],
}


def check_leakage(state, cid, text):
    """Return a list of problems if anything GM-only appears in a character prompt."""
    problems = []
    for line in state["world"].get("truth", []):
        if line in text:
            problems.append(f"world truth: {line[:60]}")
    for oid, obj in state["world"].get("objects", {}).items():
        if obj.get("hidden") and obj["desc"] in text:
            problems.append(f"hidden object: {oid}")
    for ability in ("STR", "INT", "WIS", "DEX", "CON", "CHA"):
        if f"{ability} " in text or f"{ability}:" in text:
            problems.append(f"stat name: {ability}")
    for other, rec in state["chars"].items():
        if other == cid:
            continue
        for secret in rec["identity"].get("secrets", []):
            if secret in text:
                problems.append(f"{other}'s secret")
    return problems
