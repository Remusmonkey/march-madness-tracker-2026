#!/usr/bin/env python3
"""Fetch live NCAA tournament scores from ESPN and write scores.json.

Designed to run as a GitHub Action on a schedule during the tournament.
Reads bracket.json to know what matchups to look for, fetches completed
game results from ESPN's public API, and writes scores in the same format
the bracket tracker HTML expects.
"""

import json
import os
import re
import sys
from datetime import date, timedelta
from urllib.request import urlopen, Request

BRACKET_JSON = "bracket.json"
SCORES_JSON = "scores.json"

ESPN_SCOREBOARD = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/"
    "mens-college-basketball/scoreboard?dates={date}&groups=100&limit=100"
)

TOURNAMENT_START = date(2026, 3, 17)
TOURNAMENT_END = date(2026, 4, 7)

NAME_ALIASES = {
    "UConn": ["connecticut", "uconn"],
    "St. John's": ["st. john's", "st john's"],
    "Miami FL": ["miami (fl)", "miami fl", "miami hurricanes", "mia"],
    "NC State": ["nc state", "north carolina state"],
    "Cal Baptist": ["california baptist", "cal baptist"],
    "UCF": ["ucf", "central florida"],
    "SMU": ["smu", "southern methodist"],
    "VCU": ["vcu", "virginia commonwealth"],
    "LIU": ["liu", "long island university", "long island"],
    "UMBC": ["umbc", "maryland-baltimore county"],
    "Northern Iowa": ["northern iowa", "uni"],
    "North Dakota State": ["north dakota state", "north dakota st"],
    "South Florida": ["south florida", "usf"],
    "Wright State": ["wright state", "wright st"],
    "Tennessee State": ["tennessee state", "tennessee st"],
    "Utah State": ["utah state", "utah st"],
    "High Point": ["high point"],
    "Kennesaw State": ["kennesaw state", "kennesaw st"],
    "Saint Mary's": ["saint mary's", "st. mary's", "saint mary's (ca)"],
    "Saint Louis": ["saint louis", "st. louis"],
    "Texas A&M": ["texas a&m", "texas a&m aggies"],
    "Iowa State": ["iowa state"],
    "Miami (OH)": ["miami (oh)", "miami oh", "miami redhawks", "m-oh"],
    "Prairie View A&M": ["prairie view a&m", "prairie view"],
    "Howard": ["howard"],
    "Texas": ["texas longhorns", "tex"],
}

# First Four play-in losers -> the teams that replaced them in R64
FIRST_FOUR_SUBS = {
    "Lehigh": "Prairie View A&M",
    "UMBC": "Howard",
    "SMU": "Miami (OH)",
    "NC State": "Texas",
}


def normalize(name):
    return re.sub(r"[.\s]+", " ", name.strip().lower())


def build_game_map(bracket):
    """Replicate the JS gameCounter logic to produce game IDs in the same
    order the HTML page generates them."""
    games = []
    counter = 0
    region_names = list(bracket["regions"].keys())
    round_keys = ["round_64", "round_32", "sweet_16", "elite_eight"]

    for rname in region_names[:2]:
        for rkey in round_keys:
            for g in bracket["regions"][rname].get(rkey, []):
                games.append({
                    "id": f"{rname}_{rkey}_{counter}",
                    "team_a": g["team_a_name"],
                    "team_b": g["team_b_name"],
                })
                counter += 1

    for g in bracket["final_four"]:
        games.append({
            "id": f"ff_semifinal_{counter}",
            "team_a": g["team_a_name"],
            "team_b": g["team_b_name"],
        })
        counter += 1

    c = bracket["championship"]
    games.append({
        "id": f"ff_championship_{counter}",
        "team_a": c["team_a_name"],
        "team_b": c["team_b_name"],
    })
    counter += 1

    for rname in region_names[2:]:
        for rkey in round_keys:
            for g in bracket["regions"][rname].get(rkey, []):
                games.append({
                    "id": f"{rname}_{rkey}_{counter}",
                    "team_a": g["team_a_name"],
                    "team_b": g["team_b_name"],
                })
                counter += 1

    return games


def get_aliases(name):
    raw = NAME_ALIASES.get(name, [name.lower()])
    return list({normalize(a) for a in raw} | {normalize(name)})


def fetch_espn_results():
    results = []
    d = TOURNAMENT_START
    while d <= TOURNAMENT_END:
        url = ESPN_SCOREBOARD.format(date=d.strftime("%Y%m%d"))
        try:
            req = Request(url, headers={"User-Agent": "BracketTracker/1.0"})
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            for event in data.get("events", []):
                comp = event.get("competitions", [{}])[0]
                status = comp.get("status", {}).get("type", {})
                if not status.get("completed", False):
                    continue
                competitors = comp.get("competitors", [])
                if len(competitors) != 2:
                    continue
                teams = []
                for c in competitors:
                    teams.append({
                        "name": c.get("team", {}).get("displayName", ""),
                        "abbrev": c.get("team", {}).get("abbreviation", ""),
                        "score": c.get("score", "0"),
                    })
                results.append(teams)
        except Exception as e:
            print(f"  Warning: Could not fetch {d}: {e}", file=sys.stderr)
        d += timedelta(days=1)
    return results


EXACT_ONLY = {"miami fl", "miami (fl)", "miami (oh)", "miami oh",
               "texas longhorns", "texas"}

def name_matches(aliases, candidate):
    candidate_norm = normalize(candidate)
    for alias in aliases:
        if alias == candidate_norm:
            return True
        if alias in EXACT_ONLY or candidate_norm in EXACT_ONLY:
            continue
        if alias in candidate_norm or candidate_norm in alias:
            return True
    return False


def _check_pair(a_aliases, b_aliases, result):
    """Check if a and b match a given ESPN result. Returns score dict or None."""
    name0, name1 = result[0]["name"], result[1]["name"]
    abbr0, abbr1 = result[0].get("abbrev", ""), result[1].get("abbrev", "")

    a_in_0 = name_matches(a_aliases, name0) or name_matches(a_aliases, abbr0)
    a_in_1 = name_matches(a_aliases, name1) or name_matches(a_aliases, abbr1)
    b_in_0 = name_matches(b_aliases, name0) or name_matches(b_aliases, abbr0)
    b_in_1 = name_matches(b_aliases, name1) or name_matches(b_aliases, abbr1)

    if a_in_0 and b_in_1:
        return {"a": str(result[0]["score"]), "b": str(result[1]["score"])}
    if a_in_1 and b_in_0:
        return {"a": str(result[1]["score"]), "b": str(result[0]["score"])}
    return None


def match_game(bracket_game, espn_results):
    a_aliases = get_aliases(bracket_game["team_a"])
    b_aliases = get_aliases(bracket_game["team_b"])

    for result in espn_results:
        m = _check_pair(a_aliases, b_aliases, result)
        if m:
            return m

    # Retry with First Four substitutions (play-in losers replaced by winners)
    sub_a = FIRST_FOUR_SUBS.get(bracket_game["team_a"])
    sub_b = FIRST_FOUR_SUBS.get(bracket_game["team_b"])
    subs_to_try = []
    if sub_b:
        subs_to_try.append((a_aliases, get_aliases(sub_b)))
    if sub_a:
        subs_to_try.append((get_aliases(sub_a), b_aliases))
    if sub_a and sub_b:
        subs_to_try.append((get_aliases(sub_a), get_aliases(sub_b)))

    for alt_a, alt_b in subs_to_try:
        for result in espn_results:
            m = _check_pair(alt_a, alt_b, result)
            if m:
                return m

    return None


ROUND_DATES = {
    "round_64": date(2026, 3, 20),
    "round_32": date(2026, 3, 22),
    "sweet_16": date(2026, 3, 28),
    "elite_eight": date(2026, 3, 30),
    "final_four": date(2026, 4, 4),
    "championship": date(2026, 4, 6),
}


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    bracket_path = os.path.join(script_dir, BRACKET_JSON)
    with open(bracket_path) as f:
        bracket = json.load(f)

    game_map = build_game_map(bracket)
    print(f"Bracket has {len(game_map)} games to track")

    espn_results = fetch_espn_results()
    print(f"Fetched {len(espn_results)} completed tournament games from ESPN")

    scores = {}
    for game in game_map:
        result = match_game(game, espn_results)
        if result:
            scores[game["id"]] = result
            print(f"  Matched: {game['team_a']} vs {game['team_b']} -> "
                  f"{result['a']}-{result['b']}")

    today = date.today()
    did_not_occur = []
    for game in game_map:
        if game["id"] in scores:
            continue
        gid = game["id"]
        round_key = None
        for rk in ROUND_DATES:
            if rk in gid:
                round_key = rk
                break
        if round_key and today > ROUND_DATES[round_key]:
            did_not_occur.append(gid)
            print(f"  Did not occur: {game['team_a']} vs {game['team_b']}")

    output = {"scores": scores, "did_not_occur": did_not_occur}
    scores_path = os.path.join(script_dir, SCORES_JSON)
    with open(scores_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nWrote {len(scores)} scores + {len(did_not_occur)} cancelled to {SCORES_JSON}")


if __name__ == "__main__":
    main()
