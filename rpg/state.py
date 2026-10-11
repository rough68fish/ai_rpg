"""Loading, saving and querying the scene state.

The whole game state is one dict:
  {"world": <world.json>, "chars": {id: <characters/id.json>}, "log": [events]}
The player lives in world["player"] and is treated as an observer with id "player".
"""
import json
import random
import shutil
from datetime import datetime
from pathlib import Path

PLAYER = "player"


def load_scene(scene_dir):
    scene_dir = Path(scene_dir)
    world = json.loads((scene_dir / "world.json").read_text(encoding="utf-8"))
    chars = {}
    for path in sorted((scene_dir / "characters").glob("*.json")):
        chars[path.stem] = json.loads(path.read_text(encoding="utf-8"))
    return {"world": world, "chars": chars, "log": []}


# --- modules -------------------------------------------------------------
# A module is a folder under modules/ holding module.json (title, description),
# world.json and characters/*.json. Everything scene-specific lives there.

def list_modules(base):
    """{name: module.json contents} for every module folder under base."""
    return {d.name: json.loads((d / "module.json").read_text(encoding="utf-8"))
            for d in sorted(Path(base).iterdir()) if (d / "module.json").exists()}


def load_module(base, name):
    folder = Path(base) / name
    if not (folder / "module.json").exists():
        raise SystemExit(f"no module called {name!r}; available: {', '.join(list_modules(base))}")
    state = load_scene(folder)
    info = json.loads((folder / "module.json").read_text(encoding="utf-8"))
    state["module"] = {"name": name, "title": info.get("title", name)}
    return state


def new_run_dir(base, prefix=""):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run = Path(base) / (f"{prefix}-{stamp}" if prefix else stamp)
    run.mkdir(parents=True, exist_ok=True)
    return run


def save_snapshot(state, run_dir):
    turn = state["world"]["clock"]["turn"]
    path = Path(run_dir) / f"turn_{turn:03d}.json"
    path.write_text(json.dumps(state, indent=2))
    return path


def load_snapshot(path):
    return json.loads(Path(path).read_text())


# --- lookups -------------------------------------------------------------

def actor(state, aid):
    """Record for a character or the player."""
    if aid == PLAYER:
        return state["world"]["player"]
    return state["chars"][aid]


def all_ids(state):
    return [PLAYER] + list(state["chars"])


def location(state, aid):
    return actor(state, aid)["location"]


def short(state, aid):
    rec = actor(state, aid)
    return rec.get("short") or rec["identity"]["short"]


def appearance(state, aid):
    rec = actor(state, aid)
    return rec.get("appearance") or rec["identity"]["appearance"]


def knows(state, observer, other):
    """Does observer know other by name?"""
    if observer == other:
        return True
    rec = actor(state, observer)
    if observer == PLAYER:
        return other in rec.get("known", [])
    return other in rec["identity"].get("relationships", {}) or other in rec.get("known", [])


def name_for(state, observer, other):
    """How observer refers to other: a name if known, otherwise a description."""
    if observer is None:
        return short(state, other)
    if observer == other:
        return "I"
    return short(state, other) if knows(state, observer, other) else appearance(state, other)


def in_room(state, room):
    return [a for a in all_ids(state) if location(state, a) == room]


def adjacent(state, room):
    return state["world"]["rooms"][room]["adjacent"]


def door_between(state, a, b):
    doors = state["world"].get("doors", {})
    return doors.get(f"{a}-{b}") or doors.get(f"{b}-{a}")


def path(state, start, goal):
    """Rooms walked through from start to goal (excluding start), avoiding
    locked doors; None if there is no way."""
    prev, frontier = {start: None}, [start]
    while frontier:
        room = frontier.pop(0)
        if room == goal:
            steps = []
            while room != start:
                steps.append(room)
                room = prev[room]
            return steps[::-1]
        for nxt in adjacent(state, room):
            door = door_between(state, room, nxt)
            if nxt not in prev and not (door and door.get("locked")):
                prev[nxt] = room
                frontier.append(nxt)
    return None


def door_rooms(door_id):
    return door_id.split("-", 1)


def object_room(state, oid):
    """The room an object is in, following holders ("aldric") and hiding
    places ("behind loose_stone")."""
    loc = state["world"]["objects"][oid]["location"]
    if loc in state["world"]["rooms"]:
        return loc
    if loc == PLAYER or loc in state["chars"]:
        return location(state, loc)
    if loc.startswith("behind "):
        return object_room(state, loc[len("behind "):])
    return None


def carrying(state, aid):
    return [oid for oid, o in state["world"]["objects"].items() if o["location"] == aid]


def stat(state, aid, ability):
    if aid == PLAYER:
        return state["world"]["player"].get("stats", {}).get(ability, 10)
    return state["chars"][aid]["stats"][ability]


def roll_under(state, aid, ability, modifier, rng):
    """Old-school check: d20 at or under ability + modifier succeeds."""
    target = stat(state, aid, ability) + modifier
    roll = rng.randint(1, 20)
    return {"actor": aid, "ability": ability, "modifier": modifier,
            "target": target, "roll": roll, "success": roll <= target}


def player_name(state):
    """What the GM calls the player's character, e.g. "the traveller"."""
    return state["world"]["player"].get("name", "the traveller")


def player_words(state):
    """Lower-case ways the GM or a character may refer to the player."""
    p = state["world"]["player"]
    return {"player", "stranger", "the stranger", "traveller", "the traveller",
            p.get("name", "").lower(), p.get("short", "").lower(),
            p.get("appearance", "").lower()} - {""}


# Words in a name that are a form of address, not the name itself ("Father Tomas"
# is still Tomas). A module can add its own in world.json "titles".
TITLES = ("Father", "Mother", "Brother", "Sister", "Mr.", "Mrs.", "Miss", "Sir", "Dame",
          "Lord", "Lady", "Master", "Mistress", "Captain", "Doctor", "Dr.")


def titles(state):
    return set(TITLES) | set(state["world"].get("titles", []))


def room_name(state, room):
    """A room's display name: its "name" in world.json, or the id with spaces."""
    return state["world"]["rooms"][room].get("name") or room.replace("_", " ")


def setting(state, part):
    """A line of the module's setting: "gm" (for the GM), "known" (what every
    character knows about where they live) or "era" (a rule for characters).
    Empty when the module or an old snapshot has none."""
    return state["world"].get("setting", {}).get(part, "")


def _event(clock):
    """(what, at) for the event the scene builds toward, e.g. ("mass", "09:00"),
    or (None, None) when the module has none. Old snapshots have mass_at."""
    if clock.get("mass_at") and not clock.get("event_at"):
        return "mass", clock["mass_at"]
    return clock.get("event"), clock.get("event_at")


def event_line(state):
    """The time and coming event for the GM, e.g. "08:20 (mass at 09:00, at the Terce bell)"."""
    clock = state["world"]["clock"]
    what, at = _event(clock)
    if not at:
        return clock["time"]
    bell = f", at the {clock['bell']} bell" if clock.get("bell") else ""
    return f"{clock['time']} ({what} at {at}{bell})"


def time_line(state):
    """The time as people in the scene tell it. With a "bell" in the clock (no
    clocks in that era) it is told by the bell; otherwise by the clock."""
    clock = state["world"]["clock"]
    what, at = _event(clock)
    if not at:
        return f"It is {clock['time']}."
    if not clock.get("bell"):
        return f"It is {clock['time']}. {what.capitalize()} is at {at}."
    h, m = map(int, clock["time"].split(":"))
    eh, em = map(int, at.split(":"))
    left = (eh * 60 + em) - (h * 60 + m)
    bell = clock["bell"]
    if left > 0:
        return (f"The bell for {bell}, when {what} begins, will ring in "
                f"about {max(5, round(left / 5) * 5)} minutes.")
    if left == 0:
        return f"The bell for {bell} is ringing: {what} is beginning."
    return f"The {bell} bell has rung; {what} should already have begun."


def make_rng(seed=None):
    return random.Random(seed)
