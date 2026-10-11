"""Promotion and demotion between agent and GM-managed (framework.md, turn loop
step 4; prompts.md section 4).

Promote: the traveller's action targets or names a managed character who
perceived it. Demote: an agent with no contact with the traveller for
DEMOTE_AFTER turns. Both switches compress the memories gathered since the last
summary. Memories with importance 7+ are kept word for word by code; the model
only merges the rest, and it sees nothing but the character's own record.
"""
from . import llm
from . import prompts
from .gm import SYSTEM
from . import state as S

DEMOTE_AFTER = 5
KEEP_IMPORTANCE = 7
MAX_SUMMARY_LINES = 3

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "summaries": {"type": "array", "items": {
            "type": "object",
            "properties": {"text": {"type": "string"},
                           "importance": {"type": "integer", "minimum": 1, "maximum": 10}},
            "required": ["text", "importance"]}},
        "new_beliefs": {"type": "array", "items": {"type": "string"}},
        "new_goals": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summaries", "new_beliefs", "new_goals"],
}


# --- contact ------------------------------------------------------------------

def _names(event, cid):
    return event.get("target") == cid or "{%s}" % cid in event.get("text", "")


def engaged(state, log_from):
    """Managed characters the traveller engaged since log index log_from: a
    traveller event that targets or names them, and that they perceived."""
    out = []
    for entry in state["log"][log_from:]:
        e = entry["event"]
        if e["actor"] != S.PLAYER:
            continue
        for cid in entry["perceived_by"]:
            rec = state["chars"].get(cid)
            if rec and rec["mode"] == "managed" and _names(e, cid) and cid not in out:
                out.append(cid)
    return out


def touch_contacts(state, log_from):
    """Record contact both ways: they perceived the traveller, or the traveller
    perceived them."""
    turn = state["world"]["clock"]["turn"]
    for entry in state["log"][log_from:]:
        actor, seen = entry["event"]["actor"], entry["perceived_by"]
        if actor == S.PLAYER:
            for cid in seen:
                if cid in state["chars"]:
                    state["chars"][cid]["last_contact_turn"] = turn
        elif actor in state["chars"] and S.PLAYER in seen:
            state["chars"][actor]["last_contact_turn"] = turn


def idle_agents(state):
    turn = state["world"]["clock"]["turn"]
    return [cid for cid, rec in state["chars"].items() if rec["mode"] == "agent"
            and turn - rec.get("last_contact_turn", 0) >= DEMOTE_AFTER]


# --- summaries ----------------------------------------------------------------

def _summarised_upto(rec):
    if "summarised_upto" in rec:
        return rec["summarised_upto"]
    return sum(1 for m in rec.get("memory", []) if not isinstance(m["t"], int))


def summary_message(state, cid, memories):
    rec = state["chars"][cid]
    idn = rec["identity"]
    name = idn["short"]
    return f"""Summarise these memories of {name} from {name}'s own point of view, in first
person. Keep only what {name} actually experienced. Merge them into at most
{MAX_SUMMARY_LINES} short lines, each with an importance from 1 to 10. Then list any new
beliefs or goals {name} has clearly formed from them (usually none).

Character sheet:
{idn['name']}, {idn['age']}, {idn['role']}. {idn['personality']}
Current beliefs:
{prompts._bullets(rec.get('beliefs', []))}
Current goals:
{prompts._bullets(rec.get('goals', []))}

Memories to summarise:
{prompts._bullets([f"turn {m['t']}: {m['text']}" for m in memories])}"""


def summarise(state, cid, end, client):
    """Compress memory[summarised_upto:end] in place. Returns a report dict."""
    rec = state["chars"][cid]
    mem = rec.setdefault("memory", [])
    start = _summarised_upto(rec)
    chunk = mem[start:end]
    keep = [m for m in chunk if m["importance"] >= KEEP_IMPORTANCE]
    merge = [m for m in chunk if m["importance"] < KEEP_IMPORTANCE]
    report = {"before": len(chunk), "after": len(chunk), "beliefs": [], "goals": [], "error": None}

    if len(merge) > MAX_SUMMARY_LINES:
        msg = summary_message(state, cid, merge)
        leaks = prompts.check_leakage(state, cid, msg)
        if leaks:
            report["error"] = "not sent, LEAKAGE: " + "; ".join(leaks)
            return report
        try:
            # GM system prompt, but the message carries only this character's record.
            reply, _ = client.chat(SYSTEM, msg, SUMMARY_SCHEMA, llm.TEMP_RESOLVE)
        except llm.LLMError as e:
            report["error"] = str(e)
            return report
        span = f"turns {merge[0]['t']}-{merge[-1]['t']}"
        summaries = [{"t": span, "time": merge[-1].get("time"), "text": s["text"].strip(),
                      "importance": max(1, min(10, int(s.get("importance", 4)))), "by": "summary"}
                     for s in reply.get("summaries", [])[:MAX_SUMMARY_LINES] if s.get("text")]
        for key, new in (("beliefs", reply.get("new_beliefs")), ("goals", reply.get("new_goals"))):
            items = rec.setdefault(key, [])
            for line in new or []:
                if line and line not in items:
                    items.append(line)
                    report[key].append(line)
        compressed = summaries + keep
        rec["memory"] = mem[:start] + compressed + mem[end:]
        report["after"] = len(compressed)
        report["summaries"] = [s["text"] for s in summaries]
    rec["summarised_upto"] = start + report["after"]
    return report


# --- switching ----------------------------------------------------------------

def promote(state, cid, client):
    """Managed -> agent. Memories from before this turn are summarised; this
    turn's stay unread, so the character reacts to what just happened."""
    rec = state["chars"][cid]
    turn = state["world"]["clock"]["turn"]
    mem = rec.setdefault("memory", [])
    end = next((i for i, m in enumerate(mem) if m["t"] == turn), len(mem))
    end = max(end, _summarised_upto(rec))
    report = summarise(state, cid, end, client)
    rec["mode"] = "agent"
    rec["memories_seen"] = rec["summarised_upto"]
    rec["last_contact_turn"] = turn
    return report


def demote(state, cid, client):
    """Agent -> managed, with everything so far summarised."""
    rec = state["chars"][cid]
    report = summarise(state, cid, len(rec.setdefault("memory", [])), client)
    rec["mode"] = "managed"
    rec["memories_seen"] = len(rec["memory"])
    return report
