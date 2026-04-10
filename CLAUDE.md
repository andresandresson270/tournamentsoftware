# BADICE – Claude Code Guide

Badminton tournament viewer for coaches. Scrapes tournamentsoftware.com and displays match schedules in a mobile-first web UI.

**Live site:** https://andresandresson270.github.io/tournamentsoftware/

---

## Architecture

```
tournaments.json        → list of tournaments to scrape
scraper.py              → Python scraper (runs via GitHub Actions)
data/<id>.json          → one JSON file per tournament (output of scraper)
index.html              → single-file SPA, reads data/<id>.json
.github/workflows/scrape.yml  → runs scraper every 10 min (peak) / hourly (off-peak)
```

No build step. No npm. No framework. Deploy is just `git push`.

---

## Scraper (scraper.py)

**Current flow:**
1. Accept cookie consent on tournamentsoftware.com
2. POST to `/Players/GetPlayersContent` → get all players + teams
3. For each player, open their profile page → extract matches
4. Deduplicate, parse scores, write `data/<id>.json`

**Known bottleneck:** ~100 player profile requests × 0.5 s delay = 2–4 min per tournament.

**Planned improvement (not yet implemented):**
- Scrape `/Matches` day pages instead of per-player profiles → ~5–8 requests total
- Check "last changed" on tournament overview page before scraping; skip if unchanged
- Skip match IDs that already have a result (`winner != null`)

**Match ID:** SHA256 of sorted player IDs + time string (first 16 hex chars). Stable across runs.

**Score parsing:** compact digit string e.g. `"21152115"` → `[[21,15],[21,15]]`. Uses 2-digit chunks when value ≤ 30, else 1-digit.

---

## Data format (data/<id>.json)

```json
{
  "last_updated": "ISO 8601",
  "teams": {
    "TeamName": {
      "players": ["Name"],
      "matches": [{
        "id": "16-char hex",
        "time": "Sat 3/14/2026 10:00 AM",
        "round": "Round 1",
        "event": "U13 KVK - ...",
        "type": "singles|doubles",
        "our_players": [],
        "our_player_teams": [],
        "opponent_players": [],
        "opponent_player_teams": [],
        "opponent_team": "TeamName|null",
        "result": "21152115",
        "result_games": [[21,15],[21,15]],
        "winner": "side_a|side_b|null"
      }]
    }
  }
}
```

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
- Time slot urgency: yellow → orange → red border/header tint (`.time-slot-1` through `.time-slot-6`)
- Print stylesheet outputs A4, black-on-white, hides UI chrome

**State:** single `state` object. `selectedTeam` is persisted in `location.hash`.

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

**Use JSON files.** No database needed. Data is small and tournament-scoped. GitHub Pages + git-committed JSON is the right fit. Only reconsider if persistent historical data or multi-writer scenarios arise.

---

## GitHub Actions (scrape.yml)

- Every 10 min during 08:00–17:50 UTC (peak hours)
- Top of every hour outside peak
- Manual trigger via `workflow_dispatch`
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
