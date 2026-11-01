# BADICE – Claude Code Guide

Badminton tournament viewer for coaches. Scrapes tournamentsoftware.com and displays match schedules in a mobile-first web UI.

**Hosting:** Vercel, according to the GitHub repo's homepage setting. That setting points to https://tournamentsoftware-two.vercel.app, but that URL returned `DEPLOYMENT_NOT_FOUND` on 2026-11-01, so confirm the real production URL before relying on it. GitHub Pages is **disabled** (the old `andresandresson270.github.io/tournamentsoftware/` URL returns 404).

---

## Architecture

```
tournaments.json        → list of tournaments to scrape
scraper_v2.py           → Python scraper in use (run by GitHub Actions)
scraper.py              → legacy v1 scraper (per-player profiles), not used by the workflow
data/<id>_v2.json       → one JSON file per tournament (output of scraper_v2.py)
data/<id>.json          → legacy v1 output, only for old tournaments
index.html              → single-file SPA, reads data/<id>_v2.json
.github/workflows/scrape.yml  → triggered by cron-job.org via workflow_dispatch
```

No build step. No npm. No framework. Deploy is just `git push`.

---

## Scraper (scraper_v2.py)

**Flow:**
1. Accept cookie consent on tournamentsoftware.com
2. Read "Last changed" on the tournament overview page. If it equals `last_changed` in the existing JSON, **skip and write nothing**
3. POST to `/Players/GetPlayersContent` → map player id → name + team
4. For each day tab on `/Matches`: skip days listed in `complete_days`, otherwise fetch the day page and parse match cards. Matches that already have a result are skipped
5. Detect walkovers (winner marked but no scores)
6. Merge with existing data, write `data/<id>_v2.json`

About 3–6 requests per run instead of ~100 in v1.

**Consequence for the UI:** `last_updated` only changes when the source site changed. A quiet period makes the data look older than the last check.

**Match ID:** SHA256 of sorted player IDs + time string (first 16 hex chars). Stable across runs.

**Names:** player names in matches can include a seed suffix, e.g. `"Eggert Þór Eggertsson [1/2]"`. The `teams[*].players` list has names without seeds. To decide which club a player belongs to, use `our_player_teams` / `opponent_player_teams`, not name matching.

---

## Data format (data/<id>_v2.json)

```json
{
  "last_updated": "ISO 8601 (time of last write, not last check)",
  "last_changed": "Sunday, November 1, 2026 10:09 AM GMT (from the source site)",
  "complete_days": ["20261031"],
  "teams": {
    "TeamName": {
      "players": ["Name"],
      "matches": [{
        "id": "16-char hex",
        "time": "Sat 3/14/2026 10:00 AM",
        "round": "Round 1 | Round of 16 | Quarter final | Semi final | Final | Qualification round of 32 ...",
        "event": "1.Deild - Einliðaleikur karla - Group A",
        "type": "singles|doubles",
        "our_players": [],
        "our_player_teams": [],
        "opponent_players": [],
        "opponent_player_teams": [],
        "opponent_team": "TeamName|null",
        "result": "",
        "result_games": [[21,15],[21,15]],
        "winner": "side_a|side_b|null",
        "walkover": true
      }]
    }
  }
}
```

- Each team's match entries are from that team's perspective: `our_*` = this team's side, `side_a` = our side, and `result_games` are `[ours, theirs]`.
- `walkover` is only present when true. Walkovers have a `winner` and empty `result_games`.
- The same match appears once under each club involved; dedupe by `id`.
- Round and event names come in English from the source ("Round 1", "Group A"). The UI translates them to Icelandic.

---

## Frontend (index.html)

Single HTML file. Vanilla JS + Tailwind CSS (CDN) + DM Sans (Google Fonts).

**Design rules — do not change these:**
- Dark theme only. Background `#0d0d0f` (`surface-900`), cards `#16161a` (`surface-800`)
- Accent color: amber (`#f59e0b` / `accent-500`). Do not introduce other accent colors
- Font: DM Sans. Do not swap fonts
- Max width `max-w-2xl` centered — works on mobile and desktop
- Touch targets minimum 44px. Prefer `rounded-2xl` for cards, `rounded-full` for pills
- Transitions: `0.2s ease` on hover, `0.1s` on active/press. Keep them subtle
- Time slot urgency: yellow → orange → red border/header tint (`.time-slot-1` through `.time-slot-6`). In club view the tint means how many of the club's matches are at the same time (the pill says "N samtímis"). Only unfinished slots are tinted; finished slots use the neutral `.time-slot-done`
- Muted text is `mute` = `#a1a1aa` (about 7:1 contrast). Don't go back to the darker grey
- Print stylesheet outputs A4, black-on-white, hides UI chrome. It prints **all days**, not just the selected tab, and hides scores and badges

**Layout (top to bottom):**
- One sticky header (`#sticky-nav`, ~110px) that stays fixed while scrolling. Keep it this compact: don't add rows, and don't make it hide or resize on scroll (changing a sticky header's height makes the list jump):
  - Title row: tournament title. Below it the **day picker** `● Sun 1. nóv ▾ · 🕑 33m` (amber dot = today; the age of the data has the full "Uppfært fyrir …" as a tooltip). Then the search 🔍 / print / refresh buttons
  - Tapping the title opens `#day-menu`, which lists each day with its match count for the current filter
  - Tapping 🔍 replaces the title with the search field
  - Club pills ("Allir leikir" + clubs, UMFA first). They're drawn at 36px, but `.pill-hit` keeps a 44px tap area inside the row's 4px vertical padding
- Search suggestions (`#suggest`) and the day menu drop down under the title row
- Player view ignores the day picker and lists **all days**, each with a day heading
- Club view only: a timeline of the selected day. Bar height = the club's matches in that slot; tap a bar to scroll to it
- Time slots for the selected day. Every day is rendered; the other days are hidden with `.hidden` so print can show them

**Match rows (scoreboard style):** one line per side. Doubles partners each get their own line. Game scores sit in columns on the right; the winner is bold white and the loser muted. Seeds like `[1/2]` are shown as `.seed` chips. Under the names: event · round, translated to Icelandic (`eventIs`, `roundIs`). Walkovers show a "Gefinn leikur" pill instead of scores.

**Orientation (`orient()`):** decides which side is shown first, and flips the scores with it.
- Club or player view: that side first, with W/L from its perspective
- "Allir leikir", or both sides from the same club: winner first with W

**Search:**
- Typing matches player names. Accents and þ/ð/æ are ignored, so "stef" finds "Stefán" and "thor" finds "Þór"
- With a club selected, focusing an empty search box lists that club's players with their record and next match
- Choosing a player, or tapping a name in any row, switches to player view. The search field stays open with the name and an amber outline
- ✕ always clears the player and closes the search. Tapping outside with nothing typed also closes it, and so does picking a club

Only one tournament is shown at a time: `pickDefaultTournament` picks the newest current one from `tournaments.json`. There is no tournament switcher.

**Auto-scroll to "now" (`scrollToNow` / `findNowSlot`):**
- Only when the selected day is today. It targets the first slot that still has an unfinished match (what's on court or up next), not the first slot by clock time, because tournaments run late
- Unfinished matches more than 90 min old are ignored (`STUCK_MATCH_MS`), so a result that was never entered can't pin the page to the morning
- The target lands `NOW_PEEK` (64px) below the header, so the end of the previous slot stays visible
- It waits for `document.fonts.ready` + two animation frames before measuring. On first load it jumps instantly and re-checks once after 300 ms in case the layout shifted, unless the user has scrolled since. Taps (club, day, ✕) scroll smoothly
- Runs on first load, the refresh button, and club/day changes. Never on the 30 s silent refresh. `scrollSeq` cancels stale jumps

**State:** single `state` object: `selectedTeam`, `selectedPlayer`, `selectedDay`. The hash holds either the club (`#UMFA`) or the player (`#p:Name`), so both can be shared as links. The day isn't stored in the URL; on load it defaults to today, or the next day with matches. The page refreshes the data every 30 s while the tab is visible.

**UI language:** Icelandic (`IS_DAYS`, `IS_MONTHS` arrays). Keep all user-visible strings in Icelandic.

**Performance guidelines:**
- No new CDN dependencies unless unavoidable
- No framework additions (React, Vue, Alpine, etc.)
- Keep the file self-contained and under ~1000 lines if possible
- Skeleton loading already implemented — use it for any new async section

---

## Adding a tournament

Edit `tournaments.json`:

```json
{
  "id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "name": "Tournament Name 2026",
  "url": "https://www.tournamentsoftware.com/tournament/GUID/players",
  "startDate": "2026-03-01",
  "endDate": "2026-03-02"
}
```

- `id` must be lowercase with hyphens
- Scraper only runs for tournaments where `endDate >= today`

---

## Storage

**Use JSON files.** No database needed. Data is small and tournament-scoped. Static hosting + git-committed JSON is the right fit. Only reconsider if persistent historical data or multi-writer scenarios arise.

---

## GitHub Actions (scrape.yml)

- Runs `python scraper_v2.py`
- Triggered externally by **cron-job.org** via `workflow_dispatch` (not GitHub's built-in cron — it was unreliable, delays of 60–120 min)
- cron-job.org job 1: every 10 min, hours 08–21 UTC (peak)
- cron-job.org job 2: top of every hour, hours 00–07 and 22–23 UTC (off-peak)
- API endpoint: `POST https://api.github.com/repos/andresandresson270/tournamentsoftware/actions/workflows/scrape.yml/dispatches`
- Requires a GitHub classic token with `workflow` scope stored in cron-job.org
- Commits only if `data/` changed; uses `--rebase` pull to handle concurrent runs
- Commit message pattern: `chore: update tournament data [scrape]`

---

## Local development

```bash
serve.bat          # Windows: python -m http.server 8080
# then open http://localhost:8080
```

Do not open `index.html` directly via `file://` — CORS blocks JSON loading.

---

## What not to do

- Do not add a JS framework or bundler
- Do not add a database or external API dependency to the frontend
- Do not change the dark theme or accent colors
- Do not increase `REQUEST_DELAY` without good reason (already slow)
- Do not commit `*-DESKTOP-*.html` / `*-DESKTOP-*.json` debug artifacts
- Do not push frontend changes in the middle of a live tournament without testing locally first. Every push deploys
