# First seed (films only, up to Endgame). Kept so an untouched copy can be upgraded.
MCU_TIMELINE_V1 = """\
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

# Full MCU (films + Disney+ series) in in-universe order. "tv" marks a series.
# Ids checked against Wikidata's TMDB id properties. Tofa has no reorder API,
# so the order here is only the order items are added in.
MCU_TIMELINE_V2 = """\
1771   # Captain America: The First Avenger
299537 # Captain Marvel
1726   # Iron Man
10138  # Iron Man 2
1724   # The Incredible Hulk
10195  # Thor
24428  # The Avengers
76338  # Thor: The Dark World
68721  # Iron Man 3
100402 # Captain America: The Winter Soldier
118340 # Guardians of the Galaxy
283995 # Guardians of the Galaxy Vol. 2
99861  # Avengers: Age of Ultron
102899 # Ant-Man
271110 # Captain America: Civil War
497698 # Black Widow
284054 # Black Panther
315635 # Spider-Man: Homecoming
284052 # Doctor Strange
284053 # Thor: Ragnarok
363088 # Ant-Man and the Wasp
299536 # Avengers: Infinity War
299534 # Avengers: Endgame
91363 tv  # What If...?
84958 tv  # Loki
85271 tv  # WandaVision
88396 tv  # The Falcon and the Winter Soldier
566525 # Shang-Chi and the Legend of the Ten Rings
524434 # Eternals
429617 # Spider-Man: Far From Home
634649 # Spider-Man: No Way Home
88329 tv  # Hawkeye
92749 tv  # Moon Knight
453395 # Doctor Strange in the Multiverse of Madness
92782 tv  # Ms. Marvel
616037 # Thor: Love and Thunder
232125 tv # I Am Groot
92783 tv  # She-Hulk: Attorney at Law
505642 # Black Panther: Wakanda Forever
894205 # Werewolf by Night
774752 # The Guardians of the Galaxy Holiday Special
640146 # Ant-Man and the Wasp: Quantumania
447365 # Guardians of the Galaxy Vol. 3
114472 tv # Secret Invasion
609681 # The Marvels
122226 tv # Echo
533535 # Deadpool & Wolverine
138501 tv # Agatha All Along
822119 # Captain America: Brave New World
202555 tv # Daredevil: Born Again
114471 tv # Ironheart
986056 # Thunderbolts*
241388 tv # Eyes of Wakanda
138503 tv # Your Friendly Neighborhood Spider-Man
617126 # The Fantastic Four: First Steps
138505 tv # Marvel Zombies
198178 tv # Wonder Man
"""

# Complete order: V2 plus the Marvel One-Shots and the Netflix series (ids from TMDB list 84979).
# Where an entry sits is a judgment call, since sources disagree; edit the text to taste.
MCU_TIMELINE = """\
1771   # Captain America: The First Avenger
211387 # Marvel One-Shot: Agent Carter
299537 # Captain Marvel
1726   # Iron Man
10138  # Iron Man 2
1724   # The Incredible Hulk
76122  # Marvel One-Shot: The Consultant
76535  # Marvel One-Shot: A Funny Thing Happened on the Way to Thor's Hammer
10195  # Thor
24428  # The Avengers
119569 # Marvel One-Shot: Item 47
76338  # Thor: The Dark World
68721  # Iron Man 3
253980 # Marvel One-Shot: All Hail the King
100402 # Captain America: The Winter Soldier
118340 # Guardians of the Galaxy
283995 # Guardians of the Galaxy Vol. 2
61889 tv  # Marvel's Daredevil
38472 tv  # Marvel's Jessica Jones
99861  # Avengers: Age of Ultron
102899 # Ant-Man
271110 # Captain America: Civil War
497698 # Black Widow
62126 tv  # Marvel's Luke Cage
62127 tv  # Marvel's Iron Fist
62285 tv  # Marvel's The Defenders
67178 tv  # Marvel's The Punisher
284054 # Black Panther
315635 # Spider-Man: Homecoming
284052 # Doctor Strange
284053 # Thor: Ragnarok
363088 # Ant-Man and the Wasp
299536 # Avengers: Infinity War
299534 # Avengers: Endgame
91363 tv  # What If...?
84958 tv  # Loki
85271 tv  # WandaVision
88396 tv  # The Falcon and the Winter Soldier
566525 # Shang-Chi and the Legend of the Ten Rings
524434 # Eternals
429617 # Spider-Man: Far From Home
634649 # Spider-Man: No Way Home
88329 tv  # Hawkeye
92749 tv  # Moon Knight
453395 # Doctor Strange in the Multiverse of Madness
92782 tv  # Ms. Marvel
616037 # Thor: Love and Thunder
232125 tv # I Am Groot
92783 tv  # She-Hulk: Attorney at Law
505642 # Black Panther: Wakanda Forever
894205 # Werewolf by Night
774752 # The Guardians of the Galaxy Holiday Special
640146 # Ant-Man and the Wasp: Quantumania
447365 # Guardians of the Galaxy Vol. 3
114472 tv # Secret Invasion
609681 # The Marvels
122226 tv # Echo
533535 # Deadpool & Wolverine
138501 tv # Agatha All Along
822119 # Captain America: Brave New World
202555 tv # Daredevil: Born Again
114471 tv # Ironheart
986056 # Thunderbolts*
241388 tv # Eyes of Wakanda
138503 tv # Your Friendly Neighborhood Spider-Man
617126 # The Fantastic Four: First Steps
138505 tv # Marvel Zombies
198178 tv # Wonder Man
"""

NAME = "MCU Timeline"


FLAG = "seeded_mcu_timeline"
OVERVIEW = "The full Marvel Cinematic Universe (films and series) in timeline order"


def seed_defaults(db) -> None:
    for d in db.list_definitions():  # upgrade untouched copies of earlier seeds
        if d["source_config"].get("text") in (MCU_TIMELINE_V1, MCU_TIMELINE_V2):
            db.update_definition(d["id"], source_config={"text": MCU_TIMELINE})
    if db.get_setting(FLAG):
        return  # seeded once; never bring it back after the user removed it
    db.set_setting(FLAG, "1")
    taken = any(d["name"] == NAME for d in db.list_definitions())
    i = db.create_definition(NAME + " (in-universe order)" if taken else NAME, OVERVIEW,
                             "manual", {"text": MCU_TIMELINE}, 1440)
    db.update_definition(i, enabled=False)
