# NBA Fantasy Command Center

A free-to-use fantasy basketball suite for Head-to-Head Points leagues. Built with Flask + nba_api — no API key required, deployable for free on Render.

## Features
- **Hub** — Today's games scoreboard + Quick Win Projector
- **Schedule** — Weekly schedule summarizer with game density heatmap
- **Box Score** — Advanced box score viewer with shooting splits, usage, and fantasy points
- **Start/Sit** — Optimizer comparing two players with matchup context
- **Trade Analyzer** — Side-by-side roster comparison by fantasy points
- **Win Projector** — Win probability using a normal-distribution model
- **ROS Heatmap** — Rest-of-season game density by team
- **Waiver Wire Streamer Finder** — Teams with the most games in a window

## Custom Scoring
PTS=1.0, REB=1.2, AST=1.5, STL=3.0, BLK=3.0, TOV=-1.0

## Local Development
```bash
pip install -r requirements.txt
python app.py
# Open http://localhost:5000
```

## Deploy to Render (free)
1. Push this repo to GitHub
2. [Create a new Web Service on Render](https://dashboard.render.com/) pointing at the repo
3. Render reads the `Procfile` and `requirements.txt` automatically
4. Set the **Environment** to `python` and deploy

The app runs on `0.0.0.0` and honors the `PORT` env var.

## Data Source
All data comes from the free [nba_api](https://github.com/swar/nba_api) library, which proxies the official NBA Stats API.