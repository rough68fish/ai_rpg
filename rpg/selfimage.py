"""What a character knows about its own abilities, in plain words.

Characters never see their stats, but people know whether they are strong,
clumsy or slow-witted. Each ability outside the average band (9-12) becomes one
"About yourself" line. A character record can word a line itself with
`self_image: {"STR": "your knees ache and you move slowly"}`; that line is used
for that ability whatever the score, so authors can add colour or mention an
average ability that matters to the character.

Bands follow the basic D&D modifiers (3 / 4-5 / 6-8 / 9-12 / 13-15 / 16-17 / 18).
Wording is kept free of any era or setting so the engine stays module-agnostic.
"""

ABILITIES = ("STR", "INT", "WIS", "DEX", "CON", "CHA")

# (lowest score in band, line per ability). None = average, nothing said.
BANDS = [
    (18, {"STR": "you are the strongest person you know",
          "INT": "you are brilliant, and you know it",
          "WIS": "almost nothing escapes your notice, and you read people easily",
          "DEX": "you are uncannily quick and sure-handed",
          "CON": "you never seem to tire or fall sick",
          "CHA": "you can talk almost anyone round; people want to please you"}),
    (16, {"STR": "you are very strong; you can lift what others can't",
          "INT": "you are clever; you see patterns others miss",
          "WIS": "you are very perceptive and hard to fool",
          "DEX": "you are quick and graceful",
          "CON": "you are tireless and rarely ill",
          "CHA": "people like you and tend to follow your lead"}),
    (13, {"STR": "you are strong",
          "INT": "you are sharp and learn quickly",
          "WIS": "you are observant and hard to fool",
          "DEX": "you are nimble",
          "CON": "you are hardy",
          "CHA": "people warm to you"}),
    (9, None),
    (6, {"STR": "you are not very strong",
         "INT": "you are not quick with ideas; you keep your plans simple",
         "WIS": "you are a bit naive and often miss what is going on",
         "DEX": "you are a bit clumsy",
         "CON": "you tire easily",
         "CHA": "people tend to overlook you"}),
    (4, {"STR": "you are weak; heavy work is beyond you",
         "INT": "you are slow with words and plans; you speak and reason simply",
         "WIS": "you are easily fooled and often caught unawares",
         "DEX": "you are clumsy; you drop and knock things over",
         "CON": "you are frail and run out of breath quickly",
         "CHA": "you are awkward and make a poor first impression"}),
    (3, {"STR": "you are very weak; even ordinary loads defeat you",
         "INT": "words and ideas confuse you; you use only short, simple words",
         "WIS": "you trust anyone and almost never notice trouble coming",
         "DEX": "you trip, fumble and break things all the time",
         "CON": "you are sickly and exhausted by the smallest effort",
         "CHA": "people find you off-putting and stop listening to you"}),
]


def line_for(ability, score):
    """The default self-description for one ability, or None if average."""
    for low, lines in BANDS:
        if score >= low:
            return lines[ability] if lines else None
    return BANDS[-1][1][ability]


def about_yourself(rec):
    """Stat-based lines (author wording first, then the default table), followed
    by the record's other `traits`."""
    custom = rec.get("self_image", {})
    stats = rec.get("stats", {})
    lines = []
    for a in ABILITIES:
        line = custom.get(a) or (line_for(a, stats[a]) if a in stats else None)
        if line:
            lines.append(line)
    return lines + list(rec.get("traits", []))
