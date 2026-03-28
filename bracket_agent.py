#!/usr/bin/env python3
"""
March Madness Bracket Filler — KenPom Strategy (Pure Python)

Feed it a CSV of teams with KenPom ratings, and it fills out
a complete 64-team bracket using the AdjEM win-probability formula.

Usage:
    python3 bracket_agent.py                  # uses teams.csv
    python3 bracket_agent.py my_teams.csv     # use your own file
"""

import csv
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# ─────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────

@dataclass
class Team:
    region: str
    seed: int
    name: str
    adj_em: float
    adj_o: float = 0.0
    adj_d: float = 0.0
    adj_t: float = 0.0

    def __str__(self):
        return f"[{self.seed}] {self.name}"


@dataclass
class Game:
    round_name: str
    team_a: Team
    team_b: Team
    winner: Team = None
    loser: Team = None
    win_prob: float = 0.0


# ─────────────────────────────────────────────
# KenPom win-probability engine
# ─────────────────────────────────────────────

def kenpom_win_probability(team_a: Team, team_b: Team) -> float:
    """
    Calculate P(team_a wins) on a neutral court using KenPom AdjEM.

    Formula: P(A) = 1 / (1 + 10^(-(AdjEM_A - AdjEM_B) / 11))

    The divisor of 11 is calibrated so that a 10-point AdjEM gap
    corresponds to roughly a 74% win probability.
    """
    delta = team_a.adj_em - team_b.adj_em
    return 1.0 / (1.0 + 10 ** (-delta / 11.0))


HISTORICAL_UPSET_BOOST = {
    (5, 12): 0.06,
    (6, 11): 0.06,
    (7, 10): 0.05,
    (3, 14): 0.03,
    (4, 13): 0.04,
}

UPSET_ELIGIBLE_SEEDS = {(5, 12), (6, 11), (7, 10)}
TARGET_UPSETS = 3


def pick_winner(team_a: Team, team_b: Team, round_name: str) -> Game:
    """
    Decide who wins a matchup using KenPom + historical upset adjustments.

    In the Round of 64, when the AdjEM gap is small, we bump the lower
    seed's probability slightly to reflect real-world upset rates.
    """
    p_a = kenpom_win_probability(team_a, team_b)

    if round_name == "Round of 64":
        lower_seed = min(team_a.seed, team_b.seed)
        higher_seed = max(team_a.seed, team_b.seed)
        boost = HISTORICAL_UPSET_BOOST.get((lower_seed, higher_seed), 0.0)

        if boost > 0:
            underdog = team_b if team_a.seed < team_b.seed else team_a
            if underdog is team_b:
                p_a -= boost
            else:
                p_a += boost

    if p_a >= 0.50:
        winner, loser = team_a, team_b
        prob = p_a
    else:
        winner, loser = team_b, team_a
        prob = 1.0 - p_a

    return Game(
        round_name=round_name,
        team_a=team_a,
        team_b=team_b,
        winner=winner,
        loser=loser,
        win_prob=round(prob, 3),
    )


# ─────────────────────────────────────────────
# Bracket structure
# Standard NCAA: 1v16, 8v9, 5v12, 4v13, 6v11, 3v14, 7v10, 2v15
# ─────────────────────────────────────────────

FIRST_ROUND_MATCHUPS = [
    (1, 16), (8, 9), (5, 12), (4, 13),
    (6, 11), (3, 14), (7, 10), (2, 15),
]

ROUND_NAMES = [
    "Round of 64",
    "Round of 32",
    "Sweet 16",
    "Elite Eight",
]


def _run_r64(teams_by_seed: dict[int, Team]) -> tuple[list[Game], list[Team]]:
    """Run Round of 64 for a region, return games and advancing teams."""
    games = []
    winners = []
    for seed_a, seed_b in FIRST_ROUND_MATCHUPS:
        game = pick_winner(teams_by_seed[seed_a], teams_by_seed[seed_b], ROUND_NAMES[0])
        games.append(game)
        winners.append(game.winner)
    return games, winners


def _run_later_rounds(advancing: list[Team]) -> tuple[Team, list[Game]]:
    """Simulate R32 through Elite Eight given 8 advancing teams."""
    all_games = []
    current = advancing
    for round_idx in range(1, 4):
        next_round = []
        for i in range(0, len(current), 2):
            game = pick_winner(current[i], current[i + 1], ROUND_NAMES[round_idx])
            all_games.append(game)
            next_round.append(game.winner)
        current = next_round
    return current[0], all_games


def simulate_region(teams_by_seed: dict[int, Team], region_name: str) -> tuple[Team, list[Game]]:
    """Simulate all four rounds of a region, return the winner and all games."""
    r64_games, advancing = _run_r64(teams_by_seed)
    winner, later_games = _run_later_rounds(advancing)
    return winner, r64_games + later_games


def _find_and_flip_upsets(
    all_r64: list[tuple[str, list[Game], list[Team]]],
    n: int = TARGET_UPSETS,
) -> list[tuple[str, list[Game], list[Team]]]:
    """Identify the N best upset candidates across all regions and flip them.

    Candidates are R64 games in UPSET_ELIGIBLE_SEEDS with the smallest AdjEM gap.
    Returns the updated (region_name, r64_games, advancing) tuples.
    """
    candidates = []
    for region_name, games, advancing in all_r64:
        for idx, game in enumerate(games):
            low = min(game.team_a.seed, game.team_b.seed)
            high = max(game.team_a.seed, game.team_b.seed)
            if (low, high) in UPSET_ELIGIBLE_SEEDS and game.winner.seed == low:
                gap = abs(game.team_a.adj_em - game.team_b.adj_em)
                candidates.append((gap, region_name, idx, game))

    candidates.sort(key=lambda x: x[0])
    flipped_info: dict[tuple[str, int], float] = {}

    for gap, region_name, idx, game in candidates[:n]:
        flipped_info[(region_name, idx)] = gap

    result = []
    for region_name, games, advancing in all_r64:
        new_games = list(games)
        new_advancing = list(advancing)
        for idx, game in enumerate(games):
            key = (region_name, idx)
            if key in flipped_info:
                flipped = Game(
                    round_name=game.round_name,
                    team_a=game.team_a,
                    team_b=game.team_b,
                    winner=game.loser,
                    loser=game.winner,
                    win_prob=round(1.0 - game.win_prob, 3),
                )
                new_games[idx] = flipped
                new_advancing[idx] = flipped.winner
                print(f"  UPSET PICK: [{flipped.winner.seed}] {flipped.winner.name} "
                      f"over [{flipped.loser.seed}] {flipped.loser.name} "
                      f"in {region_name} (AdjEM gap: {flipped_info[key]:.1f})")
        result.append((region_name, new_games, new_advancing))
    return result


def simulate_bracket(regions: dict[str, dict[int, Team]]) -> dict:
    """Run the entire tournament with smart upset selection.

    Two-pass approach:
    1. Run all R64 games using pure KenPom probabilities
    2. Flip the tightest upset-eligible matchups (5v12, 6v11, 7v10)
    3. Simulate R32+ with updated results
    """
    region_names = list(regions.keys())

    all_r64 = []
    for name in region_names:
        r64_games, advancing = _run_r64(regions[name])
        all_r64.append((name, r64_games, advancing))

    print(f"\n  Selecting {TARGET_UPSETS} smart upset picks...\n")
    all_r64 = _find_and_flip_upsets(all_r64, TARGET_UPSETS)

    region_winners = []
    region_results = {}
    for name, r64_games, advancing in all_r64:
        winner, later_games = _run_later_rounds(advancing)
        region_winners.append(winner)
        region_results[name] = r64_games + later_games

    ff_game_1 = pick_winner(region_winners[0], region_winners[1], "Final Four")
    ff_game_2 = pick_winner(region_winners[2], region_winners[3], "Final Four")
    championship = pick_winner(ff_game_1.winner, ff_game_2.winner, "Championship")

    return {
        "region_names": region_names,
        "region_results": region_results,
        "final_four": [ff_game_1, ff_game_2],
        "championship": championship,
        "champion": championship.winner,
    }


# ─────────────────────────────────────────────
# Data loading
# ─────────────────────────────────────────────

def load_teams(csv_path: str) -> dict[str, dict[int, Team]]:
    """Load teams from CSV into {region: {seed: Team}} structure."""
    regions: dict[str, dict[int, Team]] = {}

    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            team = Team(
                region=row["region"].strip(),
                seed=int(row["seed"].strip()),
                name=row["team"].strip(),
                adj_em=float(row["adj_em"].strip().replace("−", "-")),
                adj_o=float(row.get("adj_o", "0").strip() or "0"),
                adj_d=float(row.get("adj_d", "0").strip() or "0"),
                adj_t=float(row.get("adj_t", "0").strip() or "0"),
            )
            regions.setdefault(team.region, {})[team.seed] = team

    for name, seeds in regions.items():
        if len(seeds) != 16:
            print(f"WARNING: Region '{name}' has {len(seeds)} teams (expected 16)")

    return regions


# ─────────────────────────────────────────────
# Output — readable bracket
# ─────────────────────────────────────────────

def format_game(game: Game) -> str:
    marker_a = ">>>" if game.winner is game.team_a else "   "
    marker_b = ">>>" if game.winner is game.team_b else "   "
    return (
        f"  {marker_a} [{game.team_a.seed:>2}] {game.team_a.name:<22} (AdjEM: {game.team_a.adj_em:>+6.1f})\n"
        f"  {marker_b} [{game.team_b.seed:>2}] {game.team_b.name:<22} (AdjEM: {game.team_b.adj_em:>+6.1f})\n"
        f"       Winner: {game.winner.name} ({game.win_prob:.0%})"
    )


def print_bracket(results: dict):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = []

    lines.append("=" * 64)
    lines.append("  MARCH MADNESS 2026 — KENPOM BRACKET")
    lines.append(f"  Generated: {timestamp}")
    lines.append(f"  Strategy:  KenPom Adjusted Efficiency Margin (AdjEM)")
    lines.append("=" * 64)

    for region_name in results["region_names"]:
        games = results["region_results"][region_name]
        lines.append("")
        lines.append(f"{'━' * 64}")
        lines.append(f"  REGION: {region_name.upper()}")
        lines.append(f"{'━' * 64}")

        game_idx = 0
        for round_name in ROUND_NAMES:
            round_games = [g for g in games if g.round_name == round_name]
            if not round_games:
                continue
            lines.append(f"\n  ── {round_name} ──")
            for g in round_games:
                lines.append("")
                lines.append(format_game(g))
            game_idx += len(round_games)

    lines.append("")
    lines.append(f"{'━' * 64}")
    lines.append("  FINAL FOUR")
    lines.append(f"{'━' * 64}")
    for g in results["final_four"]:
        lines.append("")
        lines.append(format_game(g))

    lines.append("")
    lines.append(f"{'━' * 64}")
    lines.append("  CHAMPIONSHIP")
    lines.append(f"{'━' * 64}")
    lines.append("")
    lines.append(format_game(results["championship"]))

    champ = results["champion"]
    lines.append("")
    lines.append(f"{'━' * 64}")
    lines.append(f"  NATIONAL CHAMPION: [{champ.seed}] {champ.name}")
    lines.append(f"  AdjEM: {champ.adj_em:+.1f}  |  AdjO: {champ.adj_o:.1f}  |  AdjD: {champ.adj_d:.1f}")
    lines.append(f"{'━' * 64}")

    return "\n".join(lines)


# ─────────────────────────────────────────────
# Output — compact round-by-round summary
# ─────────────────────────────────────────────

def print_summary(results: dict) -> str:
    lines = []
    lines.append("")
    lines.append("=" * 64)
    lines.append("  BRACKET SUMMARY")
    lines.append("=" * 64)

    for region_name in results["region_names"]:
        games = results["region_results"][region_name]
        e8 = [g for g in games if g.round_name == "Elite Eight"][0]
        lines.append(f"  {region_name:<12} Champion:  [{e8.winner.seed}] {e8.winner.name}")

    ff = results["final_four"]
    lines.append("")
    lines.append("  Final Four:")
    lines.append(f"    {ff[0].winner.name}  over  {ff[0].loser.name}  ({ff[0].win_prob:.0%})")
    lines.append(f"    {ff[1].winner.name}  over  {ff[1].loser.name}  ({ff[1].win_prob:.0%})")

    c = results["championship"]
    lines.append("")
    lines.append(f"  Championship:  {c.winner.name}  over  {c.loser.name}  ({c.win_prob:.0%})")
    lines.append(f"  CHAMPION:      {c.winner.name}")
    lines.append("=" * 64)

    return "\n".join(lines)


# ─────────────────────────────────────────────
# Output — JSON for scoring
# ─────────────────────────────────────────────

def build_json_bracket(results: dict) -> dict:
    def game_to_dict(g: Game) -> dict:
        return {
            "team_a_name": g.team_a.name,
            "team_a_seed": g.team_a.seed,
            "team_b_name": g.team_b.name,
            "team_b_seed": g.team_b.seed,
            "winner": g.winner.name,
            "winner_seed": g.winner.seed,
            "loser": g.loser.name,
            "loser_seed": g.loser.seed,
            "win_prob": g.win_prob,
        }

    bracket = {
        "timestamp": datetime.now().isoformat(),
        "strategy": "KenPom AdjEM",
        "regions": {},
        "final_four": [game_to_dict(g) for g in results["final_four"]],
        "championship": game_to_dict(results["championship"]),
        "champion": results["champion"].name,
    }

    for region_name in results["region_names"]:
        games = results["region_results"][region_name]
        region_data = {}
        for round_name in ROUND_NAMES:
            key = round_name.lower().replace(" ", "_").replace("of_", "")
            region_data[key] = [game_to_dict(g) for g in games if g.round_name == round_name]
        bracket["regions"][region_name] = region_data

    return bracket


# ─────────────────────────────────────────────
# Output — Markdown bracket
# ─────────────────────────────────────────────

def build_markdown_bracket(results: dict) -> str:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "# March Madness 2026 — KenPom Bracket",
        "",
        f"> Generated: {ts}",
        "> Strategy: KenPom Adjusted Efficiency Margin (AdjEM) with smart upset selection",
        "",
    ]

    champ = results["champion"]
    c = results["championship"]
    lines += [
        "## National Champion",
        "",
        f"### [{champ.seed}] {champ.name}",
        "",
        f"- **AdjEM:** {champ.adj_em:+.1f}",
        f"- **Championship:** {c.winner.name} over {c.loser.name} ({c.win_prob:.0%})",
        "",
        "---",
        "",
        "## Final Four",
        "",
        "| Semifinal | Winner | Loser | Win Prob |",
        "|-----------|--------|-------|----------|",
    ]
    for i, g in enumerate(results["final_four"], 1):
        lines.append(f"| {i} | [{g.winner.seed}] {g.winner.name} | "
                     f"[{g.loser.seed}] {g.loser.name} | {g.win_prob:.0%} |")

    lines += ["", "---", ""]

    for region_name in results["region_names"]:
        games = results["region_results"][region_name]
        lines += [f"## {region_name} Region", ""]

        for round_name in ROUND_NAMES:
            round_games = [g for g in games if g.round_name == round_name]
            if not round_games:
                continue
            lines += [
                f"### {round_name}",
                "",
                "| Matchup | Winner | Win Prob | Upset? |",
                "|---------|--------|----------|--------|",
            ]
            for g in round_games:
                is_upset = g.winner.seed > g.loser.seed
                upset_tag = "UPSET" if is_upset else ""
                lines.append(
                    f"| [{g.team_a.seed}] {g.team_a.name} vs "
                    f"[{g.team_b.seed}] {g.team_b.name} | "
                    f"**[{g.winner.seed}] {g.winner.name}** | "
                    f"{g.win_prob:.0%} | {upset_tag} |"
                )
            lines.append("")

        lines += ["---", ""]

    lines += [
        "## Upset Picks",
        "",
        "The bracket engine identifies the tightest AdjEM matchups in historically",
        "upset-prone seed pairings (5v12, 6v11, 7v10) and selects the top 3 as upsets.",
        "",
        "| Matchup | Region | Seed Diff | AdjEM Gap |",
        "|---------|--------|-----------|-----------|",
    ]

    for region_name in results["region_names"]:
        games = results["region_results"][region_name]
        r64 = [g for g in games if g.round_name == "Round of 64"]
        for g in r64:
            if g.winner.seed > g.loser.seed:
                low, high = g.loser.seed, g.winner.seed
                if (low, high) in UPSET_ELIGIBLE_SEEDS:
                    gap = abs(g.team_a.adj_em - g.team_b.adj_em)
                    lines.append(
                        f"| [{g.winner.seed}] {g.winner.name} over "
                        f"[{g.loser.seed}] {g.loser.name} | "
                        f"{region_name} | {high - low} | {gap:.1f} |"
                    )

    lines += [
        "",
        "---",
        "",
        "## Scoring System (Upset-Weighted)",
        "",
        "| Round | Base Points | Upset Bonus (per seed diff) |",
        "|-------|-------------|----------------------------|",
        "| Round of 64 | 1 | 1.5 |",
        "| Round of 32 | 2 | 2.5 |",
        "| Sweet 16 | 4 | 4 |",
        "| Elite Eight | 8 | 6 |",
        "| Final Four | 16 | 10 |",
        "| Championship | 32 | 15 |",
        "",
        "**Example:** Correctly picking a 10-seed over a 7-seed in R64 earns",
        "1 + (3 × 1.5) = **5.5 points** instead of the base 1 point.",
        "",
        "---",
        "",
        "*Generated by Bracket Filler Agent*",
    ]

    return "\n".join(lines)


# ─────────────────────────────────────────────
# Output — HTML bracket page
# ─────────────────────────────────────────────

def build_html_bracket(json_bracket: dict) -> str:
    template_path = Path(__file__).parent / "bracket_template.html"
    template = template_path.read_text()
    json_str = json.dumps(json_bracket)
    return template.replace("__BRACKET_JSON__", json_str)


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main():
    csv_path = sys.argv[1] if len(sys.argv) > 1 else "teams.csv"

    if not Path(csv_path).exists():
        print(f"ERROR: Cannot find '{csv_path}'.")
        print("Create a CSV with columns: region, seed, team, adj_em")
        print("See teams.csv for the expected format.")
        sys.exit(1)

    print(f"Loading teams from {csv_path}...")
    regions = load_teams(csv_path)
    print(f"Loaded {sum(len(s) for s in regions.values())} teams across {len(regions)} regions.\n")

    results = simulate_bracket(regions)

    bracket_text = print_bracket(results)
    summary_text = print_summary(results)

    print(bracket_text)
    print(summary_text)

    out_txt = f"bracket_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    with open(out_txt, "w") as f:
        f.write(bracket_text)
        f.write(summary_text)
    print(f"\nSaved bracket to {out_txt}")

    json_bracket = build_json_bracket(results)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')

    out_json = f"bracket_{ts}.json"
    with open(out_json, "w") as f:
        json.dump(json_bracket, f, indent=2)
    print(f"Saved JSON to {out_json}")

    html_content = build_html_bracket(json_bracket)
    out_html = f"bracket_{ts}.html"
    with open(out_html, "w") as f:
        f.write(html_content)
    print(f"Saved HTML bracket to {out_html}")

    md_content = build_markdown_bracket(results)
    out_md = f"bracket_{ts}.md"
    with open(out_md, "w") as f:
        f.write(md_content)
    print(f"Saved Markdown bracket to {out_md}")

    print(f"\n  Open in browser:  file://{Path(out_html).resolve()}")


if __name__ == "__main__":
    main()
