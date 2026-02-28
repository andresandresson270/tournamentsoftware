"""
Tournament match scraper for badminton coaches.
Reads tournaments.json; for each tournament that has not ended, scrapes players and matches
from tournamentsoftware.com and writes data/<id>.json.
"""

import hashlib
import json
import os
import time
from datetime import date, datetime, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.tournamentsoftware.com"
COOKIEWALL_SAVE = f"{BASE_URL}/cookiewall/Save"
REQUEST_DELAY = 0.5
TOURNAMENTS_JSON = "tournaments.json"
DATA_DIR = "data"


def accept_cookie_consent(session: requests.Session, players_url: str) -> None:
    """Accept the site's cookie consent so the real page content is returned."""
    r = session.get(players_url)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    form = soup.select_one('form[action="/cookiewall/Save"]')
    if not form:
        return
    post_data = []
    for inp in form.find_all("input", type="hidden"):
        name = inp.get("name")
        if name:
            post_data.append((name, inp.get("value") or ""))
    # "Select all and save": include all cookie purposes so we get full content
    for inp in form.find_all("input", type="checkbox", attrs={"name": "CookiePurposes"}):
        if inp.get("value"):
            post_data.append(("CookiePurposes", inp.get("value")))
    r2 = session.post(COOKIEWALL_SAVE, data=post_data, allow_redirects=True)
    r2.raise_for_status()


def get_soup(url: str, session: requests.Session) -> BeautifulSoup:
    """Fetch URL and return BeautifulSoup. Prepend BASE_URL for relative hrefs when needed."""
    full_url = url if url.startswith("http") else urljoin(BASE_URL, url)
    r = session.get(full_url)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def scrape_players(
    session: requests.Session,
    players_url: str,
    get_players_content_url: str,
) -> list[dict]:
    """Scrape player list via GetPlayersContent (AJAX) so we get the actual grid HTML."""
    # The /players page loads the list via POST GetPlayersContent; GET alone returns empty placeholder
    r = session.post(
        get_players_content_url,
        data={},
        headers={
            "Referer": players_url,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "text/html, */*; q=0.01",
        },
    )
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    players = []

    # ol.list.list--grid > li.list__item > div.media__content > h5.media__title span.nav-link__value
    grid = soup.select("ol.list.list--grid li.list__item")
    for li in grid:
        media_content = li.select_one("div.media__content")
        if not media_content:
            continue

        h5 = media_content.select_one("h5.media__title")
        if not h5:
            continue

        name_span = h5.select_one("span.nav-link__value")
        name = name_span.get_text(strip=True) if name_span else None

        # Link can be a.nav-link.media__link or the only <a> in h5
        link = h5.select_one("a.nav-link.media__link") or h5.select_one("a[href*='player']")
        href = link.get("href") if link else None
        profile_url = urljoin(BASE_URL, href) if href else None

        subinfo = media_content.select_one("div.media__content-subinfo small.media__subheading span.nav-link__value")
        team_name = subinfo.get_text(strip=True) if subinfo else None

        if name and profile_url:
            players.append({"name": name, "profile_url": profile_url, "team": team_name or ""})

    return players


def scrape_matches_for_player(profile_url: str, session: requests.Session) -> list[dict]:
    """Scrape all matches from a player's profile page."""
    soup = get_soup(profile_url, session)
    matches = []

    # ul.match-group > li.match-group__item > div.match
    for item in soup.select("ul.match-group li.match-group__item div.match"):
        match = parse_match(item)
        if match:
            matches.append(match)

    return matches


def parse_match(div_match) -> dict | None:
    """Extract match data from a div.match element."""
    # Round/Event: div.match__header > ul.match__header-title span.nav-link__value
    header = div_match.select_one("div.match__header ul.match__header-title")
    round_name = None
    event_name = None
    if header:
        spans = header.select("span.nav-link__value")
        if len(spans) >= 1:
            round_name = spans[0].get_text(strip=True)
        if len(spans) >= 2:
            event_name = spans[1].get_text(strip=True)

    # Time: div.match__footer icon-clock
    time_str = None
    footer = div_match.select_one("div.match__footer ul.match__footer-list")
    if footer:
        for li in footer.select("li.match__footer-list-item"):
            if li.select_one("svg") or "icon-clock" in (li.get("class") or []):
                val = li.select_one("span.nav-link__value")
                if val:
                    time_str = val.get_text(strip=True)
                    break
        if not time_str and footer.select("span.nav-link__value"):
            time_str = footer.select("span.nav-link__value")[0].get_text(strip=True)

    # Players: div.match__body two div.match__row, each with div.match__row-title-value
    body = div_match.select_one("div.match__body")
    side_a_players = []
    side_a_ids = []
    side_b_players = []
    side_b_ids = []
    side_a_won = False
    side_b_won = False

    if body:
        rows = body.select("div.match__row")
        for i, row in enumerate(rows[:2]):
            won = "has-won" in (row.get("class") or [])
            if i == 0:
                side_a_won = won
            else:
                side_b_won = won
            for entry in row.select("div.match__row-title-value"):
                a = entry.select_one("a[data-player-id]")
                span = entry.select_one("span.nav-link__value")
                pid = a.get("data-player-id") if a else None
                pname = span.get_text(strip=True) if span else None
                if pid and pname:
                    if i == 0:
                        side_a_players.append(pname)
                        side_a_ids.append(pid)
                    else:
                        side_b_players.append(pname)
                        side_b_ids.append(pid)

    result_div = div_match.select_one("div.match__result")
    result = result_div.get_text(strip=True) if result_div else None

    return {
        "round": round_name,
        "event": event_name,
        "time": time_str,
        "side_a_players": side_a_players,
        "side_a_ids": side_a_ids,
        "side_b_players": side_b_players,
        "side_b_ids": side_b_ids,
        "side_a_won": side_a_won,
        "side_b_won": side_b_won,
        "result": result,
    }


def match_id_from_players_and_time(player_ids: list[str], time_str: str) -> str:
    """Unique match ID: sorted player IDs + time, hashed."""
    key = "|".join(sorted(player_ids)) + "|" + (time_str or "")
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _norm_name(name: str) -> str:
    """Normalize for comparison: strip and remove trailing comma."""
    return (name or "").strip().rstrip(",")


def build_teams_and_matches(players: list[dict], session: requests.Session) -> dict:
    """Fetch matches per player, add each match to every team that has a player in it."""
    # Per-team: avoid adding the same match twice to one team (e.g. doubles with two same-team players)
    seen_match_ids_per_team: dict[str, set[str]] = {}
    teams: dict[str, dict] = {}
    # Raw match data (side_a/b) so we can add the same match to other teams in a second pass
    raw_matches_by_id: dict[str, dict] = {}

    for i, p in enumerate(players):
        team_name = p["team"]
        print(f"[{i + 1}/{len(players)}] {p['name']} ({team_name}) ...")
        time.sleep(REQUEST_DELAY)

        matches_raw = scrape_matches_for_player(p["profile_url"], session)

        if team_name not in teams:
            teams[team_name] = {"players": [], "matches": []}
            seen_match_ids_per_team[team_name] = set()

        # Collect unique player names per team (for teams.team_name.players)
        if p["name"] not in teams[team_name]["players"]:
            teams[team_name]["players"].append(p["name"])

        for m in matches_raw:
            all_ids = m["side_a_ids"] + m["side_b_ids"]
            mid = match_id_from_players_and_time(all_ids, m["time"])
            # Only skip if we already added this match to *this* team (e.g. second TBR player in doubles)
            if mid in seen_match_ids_per_team[team_name]:
                continue
            seen_match_ids_per_team[team_name].add(mid)

            if mid not in raw_matches_by_id:
                raw_matches_by_id[mid] = {
                    "side_a_players": m["side_a_players"],
                    "side_b_players": m["side_b_players"],
                    "side_a_won": m["side_a_won"],
                    "side_b_won": m["side_b_won"],
                    "time": m["time"],
                    "round": m["round"],
                    "event": m["event"],
                    "result": m["result"],
                }

            match_type = "doubles" if (len(m["side_a_players"]) + len(m["side_b_players"])) > 2 else "singles"

            # Determine "our" side: the one that has a player from this team (current player's team)
            our_players = m["side_a_players"]
            opponent_players = m["side_b_players"]
            opponent_team = None  # we don't have club/team for opponents from this HTML
            winner = None
            if m["side_a_won"]:
                winner = "side_a"
            elif m["side_b_won"]:
                winner = "side_b"

            # If current player is in side_b, flip our/opponent (use normalized names: list may have "Name,")
            side_b_norm = {_norm_name(n) for n in m["side_b_players"]}
            if _norm_name(p["name"]) in side_b_norm:
                our_players = m["side_b_players"]
                opponent_players = m["side_a_players"]
                if winner == "side_a":
                    winner = "side_b"
                elif winner == "side_b":
                    winner = "side_a"

            teams[team_name]["matches"].append({
                "id": mid,
                "time": m["time"],
                "round": m["round"],
                "event": m["event"],
                "type": match_type,
                "our_players": our_players,
                "opponent_players": opponent_players,
                "opponent_team": opponent_team,
                "result": m["result"],
                "winner": winner,
            })

    # Second pass: ensure every match is in every team that has a player (in case a profile missed it)
    for mid, raw in raw_matches_by_id.items():
        side_a = raw["side_a_players"]
        side_b = raw["side_b_players"]
        side_a_won = raw["side_a_won"]
        side_b_won = raw["side_b_won"]
        match_type = "doubles" if len(side_a) + len(side_b) > 2 else "singles"

        for team_name, team_data in teams.items():
            if mid in seen_match_ids_per_team[team_name]:
                continue
            team_players_norm = {_norm_name(n) for n in team_data["players"]}
            if not team_players_norm:
                continue
            in_side_a = bool(team_players_norm & {_norm_name(n) for n in side_a})
            in_side_b = bool(team_players_norm & {_norm_name(n) for n in side_b})
            if not in_side_a and not in_side_b:
                continue

            our_players = side_a if in_side_a else side_b
            opponent_players = side_b if in_side_a else side_a
            winner = None
            if side_a_won:
                winner = "side_a" if in_side_a else "side_b"
            elif side_b_won:
                winner = "side_b" if in_side_a else "side_a"

            seen_match_ids_per_team[team_name].add(mid)
            team_data["matches"].append({
                "id": mid,
                "time": raw["time"],
                "round": raw["round"],
                "event": raw["event"],
                "type": match_type,
                "our_players": our_players,
                "opponent_players": opponent_players,
                "opponent_team": None,
                "result": raw["result"],
                "winner": winner,
            })

    # Sort matches by time per team
    for team_name in teams:
        matches = teams[team_name]["matches"]
        # Sort by time string; handle None
        def sort_key(m):
            t = m.get("time") or ""
            return t
        teams[team_name]["matches"] = sorted(matches, key=sort_key)

    return teams


def load_tournaments() -> list[dict]:
    """Load tournament list; each has id, name, url, startDate, endDate (YYYY-MM-DD)."""
    with open(TOURNAMENTS_JSON, encoding="utf-8") as f:
        return json.load(f)


def tournament_still_active(tournament: dict) -> bool:
    """True if tournament end date is today or in the future (we should scrape it)."""
    end_str = tournament.get("endDate") or ""
    try:
        end = date.fromisoformat(end_str)
        return end >= date.today()
    except (ValueError, TypeError):
        return True  # if date missing/invalid, scrape to be safe


def scrape_one_tournament(
    tournament: dict,
    session: requests.Session,
) -> None:
    """Scrape a single tournament and write data/<id>.json."""
    tid = tournament.get("id") or ""
    name = tournament.get("name") or tid
    players_url = (tournament.get("url") or "").strip()
    if not tid or not players_url:
        print(f"Skipping tournament (missing id or url): {name}")
        return
    # Build GetPlayersContent URL from players page URL (.../players -> .../Players/GetPlayersContent)
    get_players_content_url = players_url.rstrip("/").replace("/players", "/Players/GetPlayersContent")
    if get_players_content_url == players_url:
        get_players_content_url = players_url.rstrip("/") + "/Players/GetPlayersContent"

    print(f"--- {name} ---")
    print("Accepting cookie consent...")
    accept_cookie_consent(session, players_url)
    print("Fetching players list...")
    players = scrape_players(session, players_url, get_players_content_url)
    print(f"Found {len(players)} players.")
    if not players:
        print(f"No players for {name}, skipping.")
        return
    print("Fetching matches per player...")
    teams = build_teams_and_matches(players, session)

    out = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "teams": teams,
    }

    os.makedirs(DATA_DIR, exist_ok=True)
    out_path = os.path.join(DATA_DIR, f"{tid}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"Wrote {out_path} with {len(teams)} teams.")


def main():
    tournaments = load_tournaments()
    today = date.today()
    active = [t for t in tournaments if tournament_still_active(t)]
    if not active:
        print("No active tournaments (all have ended). Nothing to scrape.")
        return

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; rv:109.0) Gecko/20100101 Firefox/115.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    })

    for tournament in active:
        scrape_one_tournament(tournament, session)
        time.sleep(1)  # brief pause between tournaments


if __name__ == "__main__":
    main()
