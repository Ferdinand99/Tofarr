MCU_TIMELINE = """\
1771   # Captain America: The First Avenger
299537 # Captain Marvel
1726   # Iron Man
10138  # Iron Man 2
1724   # The Incredible Hulk
10195  # Thor
24428  # The Avengers
68721  # Iron Man 3
76338  # Thor: The Dark World
100402 # Captain America: The Winter Soldier
118340 # Guardians of the Galaxy
99861  # Avengers: Age of Ultron
102899 # Ant-Man
271110 # Captain America: Civil War
284052 # Doctor Strange
283995 # Guardians of the Galaxy Vol. 2
315635 # Spider-Man: Homecoming
284053 # Thor: Ragnarok
284054 # Black Panther
299536 # Avengers: Infinity War
363088 # Ant-Man and the Wasp
299534 # Avengers: Endgame
"""

def seed_defaults(db) -> None:
    if db.list_definitions():
        return
    i = db.create_definition("MCU Timeline", "Marvel Cinematic Universe in chronological order",
                             "manual", {"text": MCU_TIMELINE}, 1440)
    db.update_definition(i, enabled=False)
