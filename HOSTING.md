# Hosting the tournament viewer for free (GitHub Pages + scheduled scrape)

You can host the site for free and have the scraper run every 30 minutes using **GitHub Pages** and **GitHub Actions**.

## What you get

- **Site**: Your `index.html` and `data.json` served at `https://<your-username>.github.io/<repo-name>/`
- **Scraper**: Runs every 30 minutes in the cloud, updates `data.json`, and pushes the change so the site always shows fresh data.

## One-time setup

### 1. Create a GitHub account (if you don’t have one)

Sign up at [github.com](https://github.com).

### 2. Create a new repository (or use an existing one)

- Go to [github.com/new](https://github.com/new).
- Name it (e.g. `tournamentsoftware`).
- Choose **Public**.
- Do **not** add a README (you already have files).
- Create the repo.

Or use your existing repo: [github.com/andresandresson270/tournamentsoftware](https://github.com/andresandresson270/tournamentsoftware).

### 3. Push your project to GitHub

From your project folder (where `scraper.py`, `index.html`, etc. are):

```bash
git init
git add .gitignore index.html data.json scraper.py requirements.txt serve.bat HOSTING.md .github
git commit -m "Initial commit: tournament viewer and scraper"
git branch -M main
git remote add origin https://github.com/andresandresson270/tournamentsoftware.git
git push -u origin main
```

Repo: [github.com/andresandresson270/tournamentsoftware](https://github.com/andresandresson270/tournamentsoftware).

### 4. Turn on GitHub Pages

- In the repo, go to **Settings** → **Pages** (left sidebar).
- Under **Source**, choose **Deploy from a branch**.
- Branch: **main**, folder: **/ (root)**.
- Save.

After a minute or two your site will be at:

**`https://andresandresson270.github.io/tournamentsoftware/`**

### 5. Fix the data path in the site (important)

The site loads `data.json` with a relative URL. On GitHub Pages the URL is something like  
`https://user.github.io/repo-name/`, so the browser will request  
`https://user.github.io/repo-name/data.json`. As long as `data.json` is in the repo root (same as `index.html`), that works.

If you ever put the site in a subfolder, change the path in `index.html`:

```javascript
const dataUrl = 'data.json';  // or 'subfolder/data.json' if needed
```

## How the scraper runs every 30 minutes

- The file **`.github/workflows/scrape.yml`** defines a GitHub Action.
- It runs on a schedule (`*/30 * * * *` = every 30 minutes) and also when you click **Run workflow** in the **Actions** tab.
- Each run:
  1. Checks out the repo.
  2. Installs Python and `requirements.txt`.
  3. Runs `python scraper.py` (which overwrites `data.json`).
  4. Commits and pushes `data.json` if it changed.

So the site always serves the latest `data.json` that the scraper produced.

## Other free options (if you don’t use GitHub)

| Option | Hosting | Run script every 30 min |
|--------|--------|---------------------------|
| **GitHub Pages + Actions** | Free, same repo | Yes, built-in (recommended) |
| **Netlify** | Free static hosting | Use Netlify Functions + external cron (e.g. cron-job.org) to call an endpoint that runs the scraper and writes to a file or API; more setup. |
| **PythonAnywhere** | Free account, limited | Scheduled task (cron) every 30 min; you upload the scraper and run it; can serve static files. |
| **Render** | Free static sites | No cron on free tier; you’d need a separate free “cron job” or worker to run the script and update a store (e.g. GitHub repo or S3). |

For “free + script every 30 minutes + simple,” GitHub Pages + GitHub Actions is the most straightforward.

## Troubleshooting

- **Site 404**: Wait 1–2 minutes after enabling Pages; then open [https://andresandresson270.github.io/tournamentsoftware/](https://andresandresson270.github.io/tournamentsoftware/) (with trailing slash if needed).
- **Old data**: Check the **Actions** tab for failed runs (e.g. scraper error or network). Fix the run, then trigger **Run workflow** again.
- **Scraper fails in Actions**: The tournament site might block or throttle non-browser requests. If it starts failing, you may need to increase delay in `scraper.py` or run the workflow less often (e.g. every 1–2 hours).
