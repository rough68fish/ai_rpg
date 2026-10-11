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
    world = json.loads((scene_dir / "world.json").read_text())
    chars = {}
    for path in sorted((scene_dir / "characters").glob("*.json")):
        chars[path.stem] = json.loads(path.read_text())
    return {"world": world, "chars": chars, "log": []}


def new_run_dir(base):
    run = Path(base) / datetime.now().strftime("%Y%m%d-%H%M%S")
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


def make_rng(seed=None):
    return random.Random(seed)
