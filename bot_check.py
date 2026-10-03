import re

# Test the regex pattern from bot.py
pattern1 = r"^(adventure\.|select_location|back_to_locations|adventure_back)"
pattern2 = r"^combat\."

test_strings = [
    "adventure.location:forest",
    "adventure.location:drunk_forest",
    "adventure.back",
    "combat.strike",
    "combat.block",
    "combat.flee",
    "combat.accept_fate",
]

for s in test_strings:
    m1 = re.match(pattern1, s)
    m2 = re.match(pattern2, s)
    print(f"'{s}':")
    print(f"  pattern1 match: {bool(m1)}")
    print(f"  pattern2 match: {bool(m2)}")
