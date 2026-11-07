"""
Tournament match scraper v2.
Scrapes /Matches page by day instead of individual player profiles.
- Checks "last changed" on overview page first; skips if unchanged.
- Tracks complete days so finished days are never re-fetched.
- Within a day, skips matches that already have a result.
- Detects walkovers (forfeit: winner present but no score).
Outputs data/<id>_v2.json. Frontend is unchanged.
"""

import hashlib
import json
import os
import re
import time
from datetime import date, datetime, timezone
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.tournamentsoftware.com"
COOKIEWALL_SAVE = f"{BASE_URL}/cookiewall/Save"
REQUEST_DELAY = 0.5
TOURNAMENTS_JSON = "tournaments.json"
DATA_DIR = "data"


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def accept_cookie_consent(session: requests.Session, url: str) -> None:
    r = session.get(url)
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
    for inp in form.find_all("input", type="checkbox", attrs={"name": "CookiePurposes"}):
        if inp.get("value"):
            post_data.append(("CookiePurposes", inp.get("value")))
    session.post(COOKIEWALL_SAVE, data=post_data, allow_redirects=True).raise_for_status()


def get_soup(url: str, session: requests.Session) -> BeautifulSoup:
    full_url = url if url.startswith("http") else urljoin(BASE_URL, url)
    r = session.get(full_url)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


# ---------------------------------------------------------------------------
# Overview page — last changed
# ---------------------------------------------------------------------------

def scrape_last_changed(overview_url: str, session: requests.Session) -> str | None:
    """Return the 'Last changed' text from the tournament overview page."""
    try:
        soup = get_soup(overview_url, session)
        for item in soup.select("div.list__item"):
            label = item.select_one("dt.list__label")
            if label and "last changed" in label.get_text(strip=True).lower():
                value = item.select_one("dd.list__value")
                if value:
                    return value.get_text(separator=" ", strip=True)
    except Exception as e:
        print(f"  Could not fetch last-changed: {e}")
    return None


# ---------------------------------------------------------------------------
# Players page — build player_id -> {name, team} map
# ---------------------------------------------------------------------------

def scrape_players(
    session: requests.Session,
    players_url: str,
    get_players_content_url: str,
) -> dict[str, dict]:
    """POST to GetPlayersContent; return {player_id: {name, team}}."""
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
    result: dict[str, dict] = {}

    for li in soup.select("ol.list.list--grid li.list__item"):
        mc = li.select_one("div.media__content")
        if not mc:
            continue
        h5 = mc.select_one("h5.media__title")
        if not h5:
            continue
        name_span = h5.select_one("span.nav-link__value")
        name = _norm_name(name_span.get_text(strip=True)) if name_span else None
        link = h5.select_one("a.nav-link.media__link") or h5.select_one("a[href*='player']")
        href = link.get("href") if link else None
        player_id = None
        if href:
            qs = parse_qs(urlparse(href).query)
            ids = qs.get("player", [])
            if ids:
                player_id = ids[0]
        subinfo = mc.select_one(
            "div.media__content-subinfo small.media__subheading span.nav-link__value"
        )
        team = subinfo.get_text(strip=True) if subinfo else ""
        if name and player_id:
            result[player_id] = {"name": name, "team": team.strip()}

    return result


# ---------------------------------------------------------------------------
# Matches page — day tabs
# ---------------------------------------------------------------------------

def get_day_tabs(soup: BeautifulSoup) -> list[dict]:
    """Extract day tabs from the /Matches page. Each entry: {date, url}."""
    days = []
    for tab in soup.select("a.js-date-selection-tab"):
        date_val = tab.get("data-value", "")        # e.g. "20260411"
        data_href = tab.get("data-href", "")        # AJAX endpoint
        if date_val and data_href:
            days.append({"date": date_val, "url": urljoin(BASE_URL, data_href)})
    return days


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _norm_name(name: str) -> str:
    return (name or "").strip().rstrip(",").strip()


def _build_time_str(date_val: str, time_of_day: str) -> str:
    """'20260411' + '9:00 AM' -> 'Sat 4/11/2026 9:00 AM'"""
    try:
        d = datetime.strptime(date_val, "%Y%m%d")
        return f"{d.strftime('%a')} {d.month}/{d.day}/{d.year} {time_of_day}"
    except ValueError:
        return time_of_day


def match_id_from_players_and_time(player_ids: list[str], time_str: str) -> str:
    key = "|".join(sorted(player_ids)) + "|" + (time_str or "")
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _parse_scores(div_match) -> list[list[int]]:
    """
    Parse ul.points into [[side_a_score, side_b_score], ...] per game.
    First cell = side_a (top row), second cell = side_b (bottom row).
    """
    games = []
    for ul in div_match.select("div.match__result ul.points"):
        cells = ul.select("li.points__cell")
        if len(cells) == 2:
            try:
                games.append([
                    int(cells[0].get_text(strip=True)),
                    int(cells[1].get_text(strip=True)),
                ])
            except ValueError:
                pass
    return games


def _is_walkover(div_match) -> bool:
    """Walkover: a winner is marked (has-won) but there are no point cells."""
    has_won = div_match.select_one("div.match__row.has-won")
    has_scores = bool(div_match.select("div.match__result ul.points"))
    return bool(has_won) and not has_scores


def _draw_id_of(div_match) -> str | None:
    """Draw number from the event link in a match card header ('...draw.aspx?id=...&draw=14')."""
    link = div_match.select_one("ul.match__header-title a[href*='draw=']")
    m = re.search(r"draw=(\d+)", link.get("href", "")) if link else None
    return m.group(1) if m else None


# Undecided playoff slot shown instead of a player, e.g. "A1 #1" = 1st place in group A1
PLACEHOLDER_RE = re.compile(r"^(\S+)\s+#(\d+)$")


def parse_placeholder_card(div_match, time_str: str) -> dict | None:
    """
    Playoff match whose players aren't decided yet: one or both sides show "A1 #1".
    Returns {draw, time, round, sides: [{group, pos} | {names: [...]}, ...]} or None.
    This is how we learn which group places go to which playoff (and who they meet).
    """
    draw_id = _draw_id_of(div_match)
    rows = div_match.select("div.match__row-wrapper > div.match__row")
    if not draw_id or len(rows) < 2:
        return None
    title_items = div_match.select("ul.match__header-title li.match__header-title-item")
    round_name = title_items[1].get_text(strip=True) if len(title_items) > 1 else None
    sides, found = [], False
    for row in rows[:2]:
        names = [
            _norm_name(a.select_one("span.nav-link__value").get_text(strip=True))
            for a in row.select("a[data-player-id]") if a.select_one("span.nav-link__value")
        ]
        value = row.select_one("div.match__row-title-value")
        text = value.get_text(" ", strip=True) if value else ""
        ph = None if names else PLACEHOLDER_RE.match(text)
        if ph:
            found = True
            sides.append({"group": ph.group(1), "pos": int(ph.group(2))})
        else:
            sides.append({"names": names} if names else {"text": text})
    if not found:
        return None
    return {"draw": draw_id, "time": time_str, "round": round_name, "sides": sides}


def parse_match_card(
    div_match,
    time_str: str,
    player_id_to_info: dict[str, dict],
) -> dict | None:
    """Parse one div.match card from the matches page into a raw neutral dict."""
    # Event and round (first two li in match__header-title)
    title_items = div_match.select("ul.match__header-title li.match__header-title-item")
    event_name = title_items[0].get_text(strip=True) if len(title_items) > 0 else None
    round_name = title_items[1].get_text(strip=True) if len(title_items) > 1 else None
    draw_id = _draw_id_of(div_match)

    # Two rows: side_a (top) and side_b (bottom)
    rows = div_match.select("div.match__row-wrapper > div.match__row")
    if len(rows) < 2:
        return None

    side_a_won = "has-won" in (rows[0].get("class") or [])
    side_b_won = "has-won" in (rows[1].get("class") or [])

    side_a_ids, side_a_names = [], []
    side_b_ids, side_b_names = [], []

    for entry in rows[0].select("div.match__row-title-value"):
        a = entry.select_one("a[data-player-id]")
        if a:
            pid = a["data-player-id"]
            ns = a.select_one("span.nav-link__value")
            pname = _norm_name(ns.get_text(strip=True)) if ns else ""
            if pid and pname:
                side_a_ids.append(pid)
                side_a_names.append(pname)

    for entry in rows[1].select("div.match__row-title-value"):
        a = entry.select_one("a[data-player-id]")
        if a:
            pid = a["data-player-id"]
            ns = a.select_one("span.nav-link__value")
            pname = _norm_name(ns.get_text(strip=True)) if ns else ""
            if pid and pname:
                side_b_ids.append(pid)
                side_b_names.append(pname)

    # Skip byes and incomplete sides
    if not side_a_ids or not side_b_ids:
        return None
    if any(n.lower() == "bye" for n in side_a_names + side_b_names):
        return None

    mid = match_id_from_players_and_time(side_a_ids + side_b_ids, time_str)
    match_type = "doubles" if len(side_a_ids) + len(side_b_ids) > 2 else "singles"
    walkover = _is_walkover(div_match)
    result_games_raw = [] if walkover else _parse_scores(div_match)

    winner = None
    if side_a_won:
        winner = "side_a"
    elif side_b_won:
        winner = "side_b"

    side_a_teams = [player_id_to_info.get(pid, {}).get("team", "") for pid in side_a_ids]
    side_b_teams = [player_id_to_info.get(pid, {}).get("team", "") for pid in side_b_ids]

    return {
        "id": mid,
        "time": time_str,
        "round": round_name,
        "event": event_name,
        "draw": draw_id,
        "type": match_type,
        "side_a_ids": side_a_ids,
        "side_a_names": side_a_names,
        "side_a_teams": side_a_teams,
        "side_b_ids": side_b_ids,
        "side_b_names": side_b_names,
        "side_b_teams": side_b_teams,
        "result_games_raw": result_games_raw,
        "winner": winner,          # "side_a" | "side_b" | None (neutral, not flipped yet)
        "walkover": walkover,
    }


# ---------------------------------------------------------------------------
# Scrape one day
# ---------------------------------------------------------------------------

def scrape_day(
    session: requests.Session,
    day_url: str,
    date_val: str,
    player_id_to_info: dict[str, dict],
    existing_complete_ids: set[str],
) -> tuple[list[dict], bool, list[dict]]:
    """
    Fetch one day's matches. Returns (new_or_updated_matches, day_is_complete, placeholders).
    Skips match IDs already in existing_complete_ids (they have a result).
    day_is_complete is True when every match on the page has a result.
    placeholders: undecided playoff matches ("A1 #1" vs "A2 #1"), see parse_placeholder_card.
    """
    soup = get_soup(day_url, session)
    new_matches: list[dict] = []
    placeholders: list[dict] = []
    total = 0
    complete_count = 0

    for wrapper in soup.select("div.match-group__wrapper"):
        header = wrapper.select_one("h5.match-group__header")
        time_of_day = header.get_text(strip=True) if header else ""
        time_str = _build_time_str(date_val, time_of_day)

        for div_match in wrapper.select("div.match"):
            ph = parse_placeholder_card(div_match, time_str)
            if ph:
                placeholders.append(ph)
            # Quick player ID extraction to check cache before full parse
            pids = [
                a["data-player-id"]
                for a in div_match.select("a[data-player-id]")
                if a.get("data-player-id")
            ]
            if not pids:
                continue
            total += 1
            quick_id = match_id_from_players_and_time(pids, time_str)

            if quick_id in existing_complete_ids:
                complete_count += 1
                continue  # already have a final result — skip

            match = parse_match_card(div_match, time_str, player_id_to_info)
            if match:
                new_matches.append(match)
                if match["winner"] is not None:
                    complete_count += 1
            # If parse_match_card returns None (bye/invalid) don't count it

    day_complete = (total > 0 and complete_count == total)
    return new_matches, day_complete, placeholders


# ---------------------------------------------------------------------------
# Build teams output from raw matches
# ---------------------------------------------------------------------------

def build_team_entry(raw: dict, in_side_a: bool) -> dict:
    """
    Build a single match entry from one team's perspective.
    in_side_a: True if this team's players are on side_a.
    """
    our_players   = raw["side_a_names"]  if in_side_a else raw["side_b_names"]
    our_teams     = raw["side_a_teams"]  if in_side_a else raw["side_b_teams"]
    opp_players   = raw["side_b_names"]  if in_side_a else raw["side_a_names"]
    opp_teams     = raw["side_b_teams"]  if in_side_a else raw["side_a_teams"]

    # Flip result_games so index 0 is always "our" score
    if in_side_a:
        result_games = [[a, b] for a, b in raw["result_games_raw"]]
    else:
        result_games = [[b, a] for a, b in raw["result_games_raw"]]

    # Flip winner so "side_a" always means "our team won"
    winner = raw["winner"]
    if not in_side_a and winner:
        winner = "side_b" if winner == "side_a" else "side_a"

    opp_team_set = {t for t in opp_teams if t}
    opponent_team = list(opp_team_set)[0] if len(opp_team_set) == 1 else None

    entry: dict = {
        "id": raw["id"],
        "time": raw["time"],
        "round": raw["round"],
        "event": raw["event"],
        "type": raw["type"],
        "our_players": our_players,
        "our_player_teams": our_teams,
        "opponent_players": opp_players,
        "opponent_player_teams": opp_teams,
        "opponent_team": opponent_team,
        "result": "",
        "result_games": result_games,
        "winner": winner,
    }
    if raw.get("draw"):
        entry["draw"] = raw["draw"]
    if raw["walkover"]:
        entry["walkover"] = True
    return entry


def merge_into_teams(
    new_raw: list[dict],
    existing_teams: dict,
    team_to_player_ids: dict[str, set[str]],
    player_id_to_info: dict[str, dict],
) -> dict:
    """
    Merge newly scraped matches into the existing teams structure.
    existing_teams already contains complete matches (winner != null) from last run.
    """
    # Start from existing teams so we keep completed matches
    teams: dict = {}
    for tname, tdata in existing_teams.items():
        teams[tname] = {
            "players": list(tdata.get("players", [])),
            "matches": [m for m in tdata.get("matches", []) if m.get("winner") is not None],
        }

    # Index existing complete match IDs per team to avoid duplicates
    seen_per_team: dict[str, set[str]] = {
        tname: {m["id"] for m in tdata["matches"]}
        for tname, tdata in teams.items()
    }

    # Add new/updated matches
    for raw in new_raw:
        side_a_ids = set(raw["side_a_ids"])
        side_b_ids = set(raw["side_b_ids"])

        for team, pids in team_to_player_ids.items():
            in_side_a = bool(pids & side_a_ids)
            in_side_b = bool(pids & side_b_ids)
            if not in_side_a and not in_side_b:
                continue  # this team has no player in the match

            if team not in teams:
                teams[team] = {"players": [], "matches": []}
                seen_per_team[team] = set()

            if raw["id"] in seen_per_team[team]:
                continue  # duplicate (e.g. doubles with two players from same team)

            seen_per_team[team].add(raw["id"])
            teams[team]["matches"].append(build_team_entry(raw, in_side_a))

    # Populate players list for every team from the players AJAX data
    for pid, info in player_id_to_info.items():
        t = info["team"]
        if not t:
            continue
        if t not in teams:
            teams[t] = {"players": [], "matches": []}
        if info["name"] not in teams[t]["players"]:
            teams[t]["players"].append(info["name"])

    # Sort matches by time within each team
    for tdata in teams.values():
        tdata["matches"].sort(key=lambda m: m.get("time") or "")

    return teams


# ---------------------------------------------------------------------------
# Draws: groups (round robin) and playoffs
# ---------------------------------------------------------------------------

def _ws(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _base_name(name: str) -> str:
    """Name without seed suffix: 'Jón [1/2]' -> 'Jón'."""
    return re.sub(r"\s*\[[^\]]*\]\s*$", "", name or "").strip()


def _event_and_short(name: str) -> tuple[str, str]:
    """
    'U13 KK Einliða - A1'                       -> ('U13 KK Einliða', 'A1')
    'U13 KK Einliða - U13 KK Einliða - A úrslit' -> ('U13 KK Einliða', 'A úrslit')
    'U17/U19 KK Tvíliða - U17/U19 KK Tvíliða A úrslit' -> ('U17/U19 KK Tvíliða', 'A úrslit')
    'U17/U19 KK Einliða A'                      -> ('U17/U19 KK Einliða A', '')
    """
    parts = [p.strip() for p in _ws(name).split(" - ")]
    event = parts[0]
    short = parts[-1] if len(parts) > 1 else ""
    if short.startswith(event):
        short = short[len(event):].strip()
    return event, short


def scrape_draws_index(session: requests.Session, tid: str) -> dict[str, dict]:
    """All draws of the tournament: {draw_id: {name, event, short, kind, type, size}}."""
    soup = get_soup(f"{BASE_URL}/sport/draws.aspx?id={tid}", session)
    draws: dict[str, dict] = {}
    for tr in soup.select("table tr"):
        a = tr.select_one("a[href*='draw=']")
        m = re.search(r"draw=(\d+)", a.get("href", "")) if a else None
        if not m:
            continue
        cells = [td.get_text(" ", strip=True) for td in tr.select("td")] + ["", "", "", ""]
        name = _ws(a.get_text(" ", strip=True))
        event, short = _event_and_short(name)
        draw_type = cells[2]
        draws[m.group(1)] = {
            "name": name,
            "event": event,
            "short": short,
            "kind": "group" if draw_type.lower() == "round robin" else "playoff",
            "type": draw_type,
            "size": cells[1],
        }
    return draws


def _int(s: str) -> int:
    try:
        return int(s)
    except (TypeError, ValueError):
        return 0


def scrape_standings(session: requests.Session, tid: str, draw_id: str) -> list[dict]:
    """Official group standings (the site applies the tie rules), best first."""
    url = f"{BASE_URL}/tournament/{tid}/Draw/{draw_id}/GetStandings"
    r = session.get(url, headers={
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"{BASE_URL}/tournament/{tid}/draw/{draw_id}",
    })
    r.raise_for_status()
    soup = BeautifulSoup(r.content, "html.parser")
    rows: list[dict] = []
    for tr in soup.select("table tbody tr"):
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 10:
            continue
        pos = tds[0].select_one(".standing-status")
        clubs = [s.get("title") or s.get_text(strip=True) for s in tds[0].select(".entrant-info-club")]
        names = [_norm_name(s.get_text(strip=True)) for s in tds[1].select("a span.nav-link__value")]
        played, won, drawn, lost, matches, games, points, pts = [td.get_text(strip=True) for td in tds[2:10]]
        rows.append({
            "pos": _int(pos.get_text(strip=True)) if pos else len(rows) + 1,
            "players": [n for n in names if n],
            "teams": clubs,
            "played": _int(played), "won": _int(won), "drawn": _int(drawn), "lost": _int(lost),
            "matches": matches, "games": games, "points": points, "pts": _int(pts),
        })
    return rows


def update_draws(
    session: requests.Session,
    tid: str,
    existing_draws: dict,
    teams: dict,
    placeholders: list[dict],
) -> dict:
    """
    Build the 'draws' section and tag every match entry in `teams` with its draw id.
    - Standings are re-fetched only for groups whose number of finished matches changed.
    - advances[pos] says where a group place goes next: {draw, vs, time, round}.
      Learned from undecided playoff matches ("A1 #1" vs "A2 #1") while they exist; kept once learned.
      For playoffs that were already filled in, rebuilt from first-round players + group standings.
    """
    index = scrape_draws_index(session, tid)
    if not index:
        print("  Draws: none found, keeping previous")
        return existing_draws

    draws: dict[str, dict] = {}
    for did, meta in index.items():
        old = existing_draws.get(did, {})
        draws[did] = {**meta, **{k: old[k] for k in ("standings", "finished", "advances") if k in old}}

    # Tag matches with their draw (older entries were stored before we read the link)
    name_to_id = {d["name"]: did for did, d in draws.items()}
    unique: dict[str, dict] = {}
    for tdata in teams.values():
        for m in tdata.get("matches", []):
            if not m.get("draw"):
                did = name_to_id.get(_ws(m.get("event")))
                if did:
                    m["draw"] = did
            unique.setdefault(m["id"], m)

    finished: dict[str, int] = {}
    for m in unique.values():
        if m.get("draw") and m.get("winner"):
            finished[m["draw"]] = finished.get(m["draw"], 0) + 1

    # Standings: only for groups with new results (or never fetched)
    fetched = 0
    for did, d in draws.items():
        if d["kind"] != "group":
            continue
        n = finished.get(did, 0)
        if "standings" in d and d.get("finished") == n:
            continue
        time.sleep(REQUEST_DELAY)
        try:
            d["standings"] = scrape_standings(session, tid, did)
            d["finished"] = n
            fetched += 1
        except Exception as e:
            print(f"  Standings for draw {did} failed: {e}")
    print(f"  Draws: {len(draws)} ({sum(d['kind'] == 'group' for d in draws.values())} groups), "
          f"standings fetched for {fetched}")

    group_id = {(d["event"], d["short"]): did for did, d in draws.items() if d["kind"] == "group"}

    # Where each group place goes, from undecided playoff matches
    for ph in placeholders:
        playoff = draws.get(ph["draw"])
        if not playoff:
            continue
        for i, side in enumerate(ph["sides"]):
            gid = group_id.get((playoff["event"], side.get("group")))
            if not gid:
                continue
            draws[gid].setdefault("advances", {})[str(side["pos"])] = {
                "draw": ph["draw"], "vs": ph["sides"][1 - i], "time": ph["time"], "round": ph["round"],
            }

    # Playoffs already filled in before we saw placeholders: rebuild from first-round players
    fed = {a["draw"] for d in draws.values() for a in d.get("advances", {}).values()}
    for pid, playoff in draws.items():
        if playoff["kind"] != "playoff" or pid in fed:
            continue
        pm = sorted((m for m in unique.values() if m.get("draw") == pid), key=lambda m: _sort_time(m.get("time")))
        if not pm:
            continue
        first_round = [m for m in pm if m.get("round") == pm[0].get("round")]
        groups = [(gid, draws[gid]) for (ev, _), gid in group_id.items() if ev == playoff["event"]]
        for m in first_round:
            sides = [m.get("our_players") or [], m.get("opponent_players") or []]
            for i, names in enumerate(sides):
                key = {_base_name(n) for n in names}
                for gid, g in groups:
                    row = next((r for r in g.get("standings", []) if set(r["players"]) == key), None)
                    if row:
                        g.setdefault("advances", {}).setdefault(str(row["pos"]), {
                            "draw": pid, "vs": {"names": [_base_name(n) for n in sides[1 - i]]},
                            "time": m.get("time"), "round": m.get("round"),
                        })
                        break
    return draws


def _sort_time(time_str: str | None) -> datetime:
    try:
        return datetime.strptime(time_str or "", "%a %m/%d/%Y %I:%M %p")
    except ValueError:
        return datetime.max


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def load_existing_v2(tid: str) -> dict:
    path = os.path.join(DATA_DIR, f"{tid}_v2.json")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def load_tournaments() -> list[dict]:
    with open(TOURNAMENTS_JSON, encoding="utf-8") as f:
        return json.load(f)


def tournament_still_active(tournament: dict) -> bool:
    end_str = tournament.get("endDate") or ""
    try:
        return date.fromisoformat(end_str) >= date.today()
    except (ValueError, TypeError):
        return True


# ---------------------------------------------------------------------------
# Main scrape logic per tournament
# ---------------------------------------------------------------------------

def scrape_one_tournament(tournament: dict, session: requests.Session) -> None:
    tid = tournament.get("id") or ""
    name = tournament.get("name") or tid
    players_url = (tournament.get("url") or "").strip()
    if not tid or not players_url:
        print(f"Skipping (missing id or url): {name}")
        return

    get_players_content_url = (
        players_url.rstrip("/").replace("/players", "/Players/GetPlayersContent")
    )
    overview_url = players_url.rstrip("/")
    if overview_url.lower().endswith("/players"):
        overview_url = overview_url[: -len("/players")]
    matches_url = overview_url + "/Matches"

    print(f"\n--- {name} ---")
    accept_cookie_consent(session, players_url)

    # 1. Check if anything changed
    last_changed = scrape_last_changed(overview_url, session)
    print(f"  Site last changed : {last_changed!r}")
    existing = load_existing_v2(tid)
    if last_changed and last_changed == existing.get("last_changed") and "draws" in existing:
        print("  No changes since last scrape — skipping.")
        return

    # 2. Build set of match IDs we already have a result for
    existing_complete_ids: set[str] = set()
    for tdata in existing.get("teams", {}).values():
        for m in tdata.get("matches", []):
            if m.get("winner") is not None:
                existing_complete_ids.add(m["id"])
    print(f"  Known complete matches : {len(existing_complete_ids)}")

    complete_days: list[str] = existing.get("complete_days", [])

    # 3. Scrape player list
    print("  Fetching players...")
    player_id_to_info = scrape_players(session, players_url, get_players_content_url)
    print(f"  Found {len(player_id_to_info)} players.")

    # team -> set of player IDs in that team
    team_to_player_ids: dict[str, set[str]] = {}
    for pid, info in player_id_to_info.items():
        t = info["team"]
        if t:
            team_to_player_ids.setdefault(t, set()).add(pid)

    # 4. Get day tabs
    print("  Fetching day tabs...")
    matches_soup = get_soup(matches_url, session)
    day_tabs = get_day_tabs(matches_soup)
    print(f"  Days: {[d['date'] for d in day_tabs]}")

    # 5. Scrape each non-complete day
    all_new_raw: list[dict] = []
    all_placeholders: list[dict] = []
    new_complete_days = list(complete_days)

    for day in day_tabs:
        date_val = day["date"]
        if date_val in complete_days:
            print(f"  Day {date_val}: already complete — skipping")
            continue
        print(f"  Day {date_val}: scraping...")
        time.sleep(REQUEST_DELAY)
        new_matches, day_complete, placeholders = scrape_day(
            session, day["url"], date_val, player_id_to_info, existing_complete_ids
        )
        print(f"    {len(new_matches)} new/updated matches  complete={day_complete}"
              f"  undecided playoff matches={len(placeholders)}")
        all_new_raw.extend(new_matches)
        all_placeholders.extend(placeholders)
        if day_complete and date_val not in new_complete_days:
            new_complete_days.append(date_val)

    # 6. Merge and write
    new_teams = merge_into_teams(
        all_new_raw,
        existing.get("teams", {}),
        team_to_player_ids,
        player_id_to_info,
    )

    # 7. Groups, standings and playoff paths. Never let this break the match data.
    draws = existing.get("draws", {})
    try:
        draws = update_draws(session, tid, draws, new_teams, all_placeholders)
    except Exception as e:
        print(f"  Draws update failed, keeping previous: {e}")

    out = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "last_changed": last_changed,
        "complete_days": sorted(new_complete_days),
        "teams": new_teams,
        "draws": draws,
    }

    os.makedirs(DATA_DIR, exist_ok=True)
    out_path = os.path.join(DATA_DIR, f"{tid}_v2.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    total_matches = sum(len(t["matches"]) for t in new_teams.values())
    print(f"  Wrote {out_path} — {len(new_teams)} teams, {total_matches} total match entries")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    tournaments = load_tournaments()
    active = [t for t in tournaments if tournament_still_active(t)]
    if not active:
        print("No active tournaments.")
        return

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; rv:109.0) Gecko/20100101 Firefox/115.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    })

    for tournament in active:
        scrape_one_tournament(tournament, session)
        time.sleep(1)


if __name__ == "__main__":
    main()
