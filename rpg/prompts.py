"""Builds character prompts from a character's own record (see prompts.md, section 5).

Nothing here reads world truth, hidden objects or stats, and check_leakage()
asserts that none of them slipped in.
"""
from . import state as S

MAX_MEMORIES = 15

RULES = """Rules:
- You only know what is written here and what you remember. If something is
  not in your memories, you do not know it. Do not guess at hidden facts as
  if they were true.
- You decide what you TRY to do and say. You do not decide whether it works;
  the world will tell you what happened.
- Speak the way you would really speak. Keep speech short: one to three
  sentences, the way people talk while they are busy.
- You may choose to do nothing, keep working, or stay silent.
- "thought" is private. No one else ever sees it. Be honest there.
- Answer only with JSON in the requested format."""


def _bullets(items, empty="nothing"):
    return "\n".join(f"- {i}" for i in items) if items else f"- {empty}"


def system_message(state, cid):
    rec = state["chars"][cid]
    idn = rec["identity"]
    rels = [f"{S.short(state, other)}: {feeling}"
            for other, feeling in idn.get("relationships", {}).items()]
    return f"""You are {idn['name']}, {idn['age']}, {idn['role']}. You are a person
in a story, not an assistant. Stay fully in character at all times.

Who you are: {idn['personality']}
How you speak: {idn['voice']}
About yourself:
{_bullets(rec.get('traits', []))}

What you know that others don't:
{_bullets(idn.get('secrets', []), 'nothing special')}

How you feel about people:
{_bullets(rels, 'no one in particular')}

{RULES}"""


def _is_new(entry, since):
    return isinstance(entry["t"], int) and entry["t"] > since


def _select_memories(memories, limit=MAX_MEMORIES):
    """Most important and most recent, returned in original (chronological) order."""
    if len(memories) <= limit:
        return memories
    scored = sorted(range(len(memories)),
                    key=lambda i: (memories[i]["importance"], i), reverse=True)[:limit]
    return [memories[i] for i in sorted(scored)]


def _when(entry):
    return f"turn {entry['t']} ({entry.get('time', '?')})" if isinstance(entry["t"], int) else entry["t"]


def user_message(state, cid):
    rec = state["chars"][cid]
    world = state["world"]
    room = rec["location"]
    since = rec.get("last_acted_turn", -1)
    memories = rec.get("memory", [])
    old = _select_memories([m for m in memories if not _is_new(m, since)])
    new = [m for m in memories if _is_new(m, since)]
    visible = [S.name_for(state, cid, o) for o in S.in_room(state, room) if o != cid]
    return f"""It is {world['clock']['time']}. Mass is at {world['clock']['mass_at']}.
You are in the {room}: {world['rooms'][room]['desc']}
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
