"""
NBA Fantasy Command Center - Flask Backend
Free-to-use fantasy basketball suite using ESPN public API
"""

import os
import json
import math
import time
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timedelta
from functools import lru_cache
from typing import Dict, List, Optional, Any, Tuple

import pandas as pd
import requests
from flask import Flask, render_template, request, jsonify, send_from_directory

app = Flask(__name__)
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 300

# =====================================================================
# Scoring Configuration (Points League Custom)
# =====================================================================
SCORING = {
    'PTS': 1.0,
    'REB': 1.2,
    'AST': 1.5,
    'STL': 3.0,
    'BLK': 3.0,
    'TOV': -1.0,
    'FG3M': 0.0,  # Not separately scored (included in PTS)
    'FGM': 0.0,
    'FTM': 0.0,
}

# Box-score cache shared across the rankings computation. Each unique game is
# fetched exactly once even though every player on both teams needs it.
_BOX_SCORE_CACHE: Dict[str, Dict] = {}
_BOX_SCORE_CACHE_LOCK = threading.Lock()
# Cap the cache so a long season cannot grow unbounded.
_BOX_SCORE_CACHE_MAX = 4000

# Rankings warm state. The first rankings request on a cold boot (or after
# gunicorn recycles a worker) can take 20-30s, and Render's free-tier load
# balancer kills requests after ~30s. We warm the cache in a background
# thread so the endpoint responds in milliseconds; the frontend polls until
# the warm finishes and the cached result is served.
_RANKINGS_LOCK = threading.Lock()
_RANKINGS_CACHE: Dict[str, List[Dict]] = {}
_RANKINGS_WARMING: set = set()
_RANKINGS_ERROR: Dict[str, str] = {}

# Common periods to keep warm so switching the dropdown is instant.
_RK_COMMON_PERIODS = ('season', '2weeks', '1week')


def _rk_key(period: str, season: str, start_date: str, end_date: str) -> str:
    return f"{period}|{season}|{start_date}|{end_date}"


def warm_rankings(period: str = 'season', season: str = '2026-27',
                  start_date: str = '', end_date: str = '') -> None:
    """Compute rankings in the background so the first request is fast.

    Warms the requested (period, season) plus the other common periods so
    every dropdown choice is cached by the time the user switches to it.
    """
    keys = [_rk_key(period, season, start_date, end_date)]
    if not start_date and not end_date:
        keys += [_rk_key(p, season, '', '') for p in _RK_COMMON_PERIODS if p != period]

    with _RANKINGS_LOCK:
        if any(k in _RANKINGS_WARMING for k in keys):
            return
        for k in keys:
            _RANKINGS_WARMING.add(k)

    try:
        for k in keys:
            p, s, sd, ed = k.split('|')
            result = get_player_rankings(p, s, sd, ed)
            with _RANKINGS_LOCK:
                _RANKINGS_CACHE[k] = result
                _RANKINGS_WARMING.discard(k)
    except Exception as e:
        with _RANKINGS_LOCK:
            for k in keys:
                _RANKINGS_WARMING.discard(k)
                _RANKINGS_ERROR[k] = str(e)

# =====================================================================
# NBA API Helpers
# =====================================================================

ESPN_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json',
}

# Fallback static list of all 30 NBA teams (ESPN team IDs + abbreviations)
# Used when ESPN API is rate-limited or unavailable.
_FALLBACK_TEAMS = [
    {'id': 1, 'abbreviation': 'ATL', 'full_name': 'Atlanta Hawks', 'name': 'Hawks', 'location': 'Atlanta'},
    {'id': 2, 'abbreviation': 'BOS', 'full_name': 'Boston Celtics', 'name': 'Celtics', 'location': 'Boston'},
    {'id': 3, 'abbreviation': 'NOP', 'full_name': 'New Orleans Pelicans', 'name': 'Pelicans', 'location': 'New Orleans'},
    {'id': 4, 'abbreviation': 'CHI', 'full_name': 'Chicago Bulls', 'name': 'Bulls', 'location': 'Chicago'},
    {'id': 5, 'abbreviation': 'CLE', 'full_name': 'Cleveland Cavaliers', 'name': 'Cavaliers', 'location': 'Cleveland'},
    {'id': 6, 'abbreviation': 'DAL', 'full_name': 'Dallas Mavericks', 'name': 'Mavericks', 'location': 'Dallas'},
    {'id': 7, 'abbreviation': 'DEN', 'full_name': 'Denver Nuggets', 'name': 'Nuggets', 'location': 'Denver'},
    {'id': 8, 'abbreviation': 'DET', 'full_name': 'Detroit Pistons', 'name': 'Pistons', 'location': 'Detroit'},
    {'id': 9, 'abbreviation': 'GSW', 'full_name': 'Golden State Warriors', 'name': 'Warriors', 'location': 'Golden State'},
    {'id': 10, 'abbreviation': 'HOU', 'full_name': 'Houston Rockets', 'name': 'Rockets', 'location': 'Houston'},
    {'id': 11, 'abbreviation': 'IND', 'full_name': 'Indiana Pacers', 'name': 'Pacers', 'location': 'Indiana'},
    {'id': 12, 'abbreviation': 'LAC', 'full_name': 'Los Angeles Clippers', 'name': 'Clippers', 'location': 'Los Angeles'},
    {'id': 13, 'abbreviation': 'LAL', 'full_name': 'Los Angeles Lakers', 'name': 'Lakers', 'location': 'Los Angeles'},
    {'id': 14, 'abbreviation': 'MIA', 'full_name': 'Miami Heat', 'name': 'Heat', 'location': 'Miami'},
    {'id': 15, 'abbreviation': 'MIL', 'full_name': 'Milwaukee Bucks', 'name': 'Bucks', 'location': 'Milwaukee'},
    {'id': 16, 'abbreviation': 'MIN', 'full_name': 'Minnesota Timberwolves', 'name': 'Timberwolves', 'location': 'Minnesota'},
    {'id': 17, 'abbreviation': 'BKN', 'full_name': 'Brooklyn Nets', 'name': 'Nets', 'location': 'Brooklyn'},
    {'id': 18, 'abbreviation': 'NYK', 'full_name': 'New York Knicks', 'name': 'Knicks', 'location': 'New York'},
    {'id': 19, 'abbreviation': 'ORL', 'full_name': 'Orlando Magic', 'name': 'Magic', 'location': 'Orlando'},
    {'id': 20, 'abbreviation': 'PHI', 'full_name': 'Philadelphia 76ers', 'name': '76ers', 'location': 'Philadelphia'},
    {'id': 21, 'abbreviation': 'PHX', 'full_name': 'Phoenix Suns', 'name': 'Suns', 'location': 'Phoenix'},
    {'id': 22, 'abbreviation': 'POR', 'full_name': 'Portland Trail Blazers', 'name': 'Trail Blazers', 'location': 'Portland'},
    {'id': 23, 'abbreviation': 'SAC', 'full_name': 'Sacramento Kings', 'name': 'Kings', 'location': 'Sacramento'},
    {'id': 24, 'abbreviation': 'SAS', 'full_name': 'San Antonio Spurs', 'name': 'Spurs', 'location': 'San Antonio'},
    {'id': 25, 'abbreviation': 'OKC', 'full_name': 'Oklahoma City Thunder', 'name': 'Thunder', 'location': 'Oklahoma City'},
    {'id': 26, 'abbreviation': 'UTA', 'full_name': 'Utah Jazz', 'name': 'Jazz', 'location': 'Utah'},
    {'id': 27, 'abbreviation': 'WAS', 'full_name': 'Washington Wizards', 'name': 'Wizards', 'location': 'Washington'},
    {'id': 28, 'abbreviation': 'TOR', 'full_name': 'Toronto Raptors', 'name': 'Raptors', 'location': 'Toronto'},
    {'id': 29, 'abbreviation': 'MEM', 'full_name': 'Memphis Grizzlies', 'name': 'Grizzlies', 'location': 'Memphis'},
    {'id': 30, 'abbreviation': 'CHA', 'full_name': 'Charlotte Hornets', 'name': 'Hornets', 'location': 'Charlotte'},
]


@lru_cache(maxsize=32)
def get_all_teams() -> List[Dict]:
    """Get all NBA teams from ESPN public API. Falls back to static list if ESPN fails."""
    try:
        resp = requests.get(
            "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams",
            headers=ESPN_HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        teams_list = []
        for sport in data.get('sports', []):
            for league in sport.get('leagues', []):
                for team in league.get('teams', []):
                    t = team.get('team', {})
                    teams_list.append({
                        'id': int(t.get('id', 0)),
                        'abbreviation': t.get('abbreviation', ''),
                        'full_name': t.get('displayName', ''),
                        'name': t.get('name', ''),
                        'location': t.get('location', ''),
                    })
        if teams_list:
            return teams_list
    except Exception as e:
        print(f"Error fetching all teams from ESPN: {e}")
    # Return fallback static list if ESPN fails
    return _FALLBACK_TEAMS


_FALLBACK_PLAYERS = [
    {'PERSON_ID': 1966, 'DISPLAY_FIRST_LAST': 'LeBron James', 'TEAM_ID': 13, 'TEAM_ABBREVIATION': 'LAL'},
    {'PERSON_ID': 3975, 'DISPLAY_FIRST_LAST': 'Stephen Curry', 'TEAM_ID': 9, 'TEAM_ABBREVIATION': 'GSW'},
    {'PERSON_ID': 6583, 'DISPLAY_FIRST_LAST': 'Luka Doncic', 'TEAM_ID': 6, 'TEAM_ABBREVIATION': 'DAL'},
    {'PERSON_ID': 3468, 'DISPLAY_FIRST_LAST': 'Giannis Antetokounmpo', 'TEAM_ID': 15, 'TEAM_ABBREVIATION': 'MIL'},
    {'PERSON_ID': 3978, 'DISPLAY_FIRST_LAST': 'Nikola Jokic', 'TEAM_ID': 7, 'TEAM_ABBREVIATION': 'DEN'},
    {'PERSON_ID': 3033, 'DISPLAY_FIRST_LAST': 'Kevin Durant', 'TEAM_ID': 21, 'TEAM_ABBREVIATION': 'PHX'},
    {'PERSON_ID': 6430, 'DISPLAY_FIRST_LAST': 'Jayson Tatum', 'TEAM_ID': 2, 'TEAM_ABBREVIATION': 'BOS'},
    {'PERSON_ID': 6589, 'DISPLAY_FIRST_LAST': 'Trae Young', 'TEAM_ID': 1, 'TEAM_ABBREVIATION': 'ATL'},
]


@lru_cache(maxsize=32)
def get_all_players() -> List[Dict]:
    """Get all current-season players with team info.

    Uses curated fallback player data as the authoritative source for team
    assignments (real-world current rosters), supplemented with ESPN data
    for players not in the fallback. This avoids ESPN's 2026-27 projected
    rosters which can be incorrect (e.g. LeBron on PHI instead of LAL).
    """
    # Build a lookup from fallback players for authoritative team assignments
    fallback_by_id = {p['PERSON_ID']: p for p in _FALLBACK_PLAYERS}
    fallback_names = {p['DISPLAY_FIRST_LAST'].lower(): p for p in _FALLBACK_PLAYERS}

    all_teams = get_all_teams()
    # Map ESPN abbreviation to our canonical abbreviation (e.g. ESPN "GS" -> "GSW")
    espn_to_canonical = {t['abbreviation']: t['abbreviation'] for t in all_teams}
    # Known ESPN abbreviations that differ from our canonical ones
    espn_to_canonical.update({
        'GS': 'GSW',
        'SA': 'SAS',
        'NO': 'NOP',
        'WSH': 'WAS',
        'UTAH': 'UTA',
        'NY': 'NYK',
    })

    def _fetch_roster(team):
        tid = team['id']
        canonical_abbr = team['abbreviation']
        # ESPN intermittently 403s on roster endpoints; retry a couple times
        # with a short backoff before giving up on that team.
        for attempt in range(3):
            try:
                resp = requests.get(
                    f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{tid}/roster",
                    headers=ESPN_HEADERS,
                    timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return [
                        {
                            'PERSON_ID': int(athlete.get('id', 0)),
                            'DISPLAY_FIRST_LAST': athlete.get('displayName', ''),
                            'TEAM_ID': tid,
                            'TEAM_ABBREVIATION': espn_to_canonical.get(team['abbreviation'], team['abbreviation']),
                        }
                        for athlete in data.get('athletes', [])
                    ]
                if resp.status_code in (403, 429) and attempt < 2:
                    time.sleep(0.4 * (attempt + 1))
                    continue
                print(f"Error fetching roster for team {tid}: HTTP {resp.status_code}")
                return []
            except Exception as e:
                if attempt < 2:
                    time.sleep(0.4 * (attempt + 1))
                    continue
                print(f"Error fetching roster for team {tid}: {e}")
                return []
        return []

    # Parallelize the 30 roster HTTP calls — ESPN is the bottleneck, not CPU.
    # Keep concurrency modest (8) so we don't trip ESPN's rate limiter.
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(_fetch_roster, all_teams))

    espn_players = [p for roster in results for p in roster]

    # Merge: prefer fallback team assignments for known players, use ESPN for others
    merged = {}
    for p in espn_players:
        pid = p['PERSON_ID']
        name = p['DISPLAY_FIRST_LAST'].lower()
        if pid in fallback_by_id:
            # Use fallback's team assignment (real-world current roster)
            fb = fallback_by_id[pid]
            merged[pid] = {
                'PERSON_ID': pid,
                'DISPLAY_FIRST_LAST': fb['DISPLAY_FIRST_LAST'],
                'TEAM_ID': fb['TEAM_ID'],
                'TEAM_ABBREVIATION': fb['TEAM_ABBREVIATION'],
            }
        elif name in fallback_names:
            # Match by name as fallback
            fb = fallback_names[name]
            merged[pid] = {
                'PERSON_ID': pid,
                'DISPLAY_FIRST_LAST': fb['DISPLAY_FIRST_LAST'],
                'TEAM_ID': fb['TEAM_ID'],
                'TEAM_ABBREVIATION': fb['TEAM_ABBREVIATION'],
            }
        else:
            # Not in fallback, use ESPN data with canonical abbreviation
            merged[pid] = p

    # Add any fallback players not found in ESPN data
    for fb in _FALLBACK_PLAYERS:
        if fb['PERSON_ID'] not in merged:
            merged[fb['PERSON_ID']] = fb

    players = list(merged.values())
    if not players:
        return _FALLBACK_PLAYERS
    return players


@lru_cache(maxsize=64)
def _normalize_name(s: str) -> str:
    """Lowercase + strip accents + collapse whitespace for name matching"""
    if not s:
        return ''
    s = s.lower().strip()
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii')
    return ' '.join(s.split())


def get_player_id(name: str) -> Optional[int]:
    """Find player ID by name (fuzzy match, accent-insensitive)"""
    all_players = get_all_players()
    target = _normalize_name(name)
    if not target:
        return None
    # Exact normalized match first
    for p in all_players:
        if _normalize_name(p['DISPLAY_FIRST_LAST']) == target:
            return p['PERSON_ID']
    # Substring match (e.g. "Doncic" matches "Luka Doncic")
    for p in all_players:
        if target in _normalize_name(p['DISPLAY_FIRST_LAST']):
            return p['PERSON_ID']
    return None


def _clear_espn_caches():
    """Clear all lru_cache'd ESPN responses so a transient 403/rate-limit that
    produced an empty result doesn't poison the lookup for the rest of the
    session. Safe to call on every request."""
    for fn in (
        get_all_players, get_all_teams, get_player_gamelog,
        get_team_pace_defense, get_team_schedule_analysis,
        get_espn_box_score, get_espn_team_schedule,
        get_espn_all_teams_schedule,
    ):
        try:
            fn.cache_clear()
        except AttributeError:
            pass


@lru_cache(maxsize=64)
def get_team_id(abbrev: str) -> Optional[int]:
    """Find team ID by abbreviation"""
    for t in get_all_teams():
        if t['abbreviation'].lower() == abbrev.lower():
            return t['id']
    return None


@lru_cache(maxsize=128)
def get_player_gamelog(player_id: int, season: str = '2026-27', n_games: int = 50) -> pd.DataFrame:
    """Get player game log for current season using ESPN schedule + box scores.

    ESPN doesn't have a direct player gamelog endpoint. We fetch the full league
    schedule (all 30 teams) and then get box scores for completed games to
    extract the player's stats. This avoids relying on potentially incorrect
    team assignments from ESPN rosters.
    """
    try:
        player_id_str = str(player_id)

        # Get the full league schedule for the season (all teams, all games)
        # This is already parallelized and cached via get_espn_all_teams_schedule
        schedule_df = get_espn_all_teams_schedule(season)
        if schedule_df.empty:
            print(f"No schedule data for season {season}")
            return pd.DataFrame()

        # Filter to completed games only
        completed = schedule_df[schedule_df['status'].str.contains('FINAL', na=False, case=False)]
        if completed.empty:
            return pd.DataFrame()

        games = []
        for _, row in completed.iterrows():
            if len(games) >= n_games:
                break
            gid = str(row.get('gameId') or '')
            if not gid:
                continue
            box = get_espn_box_score(gid)
            # Find this player in the box score
            for p in box.get('players', []):
                if str(p.get('playerId')) == player_id_str:
                    # Get game date from event
                    p['gameDate'] = row.get('GAME_DATE', '')
                    if hasattr(p['gameDate'], 'isoformat'):
                        p['gameDate'] = p['gameDate'].isoformat()
                    else:
                        p['gameDate'] = str(p['gameDate'])[:10]
                    # Build matchup string from event competitors
                    home_abbr = row.get('homeTeam_teamTricode', '')
                    away_abbr = row.get('awayTeam_teamTricode', '')
                    p_team = p.get('teamAbbrev', '')
                    if home_abbr and away_abbr:
                        opp = away_abbr if p_team == home_abbr else home_abbr
                        p['matchup'] = f"{p_team} vs {opp}"
                        p['opp_abbr'] = opp
                    games.append(p)
                    break

        if not games:
            return pd.DataFrame()

        # Convert to DataFrame with expected columns
        rows = []
        for g in games:
            row = {
                'PERSON_ID': player_id,
                'TEAM_ABBREVIATION': g.get('teamAbbrev', ''),
                'GAME_DATE': g.get('gameDate', ''),
                'MATCHUP': g.get('matchup', ''),
                'WL': 'W' if g.get('plusMinus', 0) > 0 else 'L',
                'MIN': g.get('minutes', 0),
                'PTS': g.get('points', 0),
                'REB': g.get('rebounds', 0),
                'AST': g.get('assists', 0),
                'STL': g.get('steals', 0),
                'BLK': g.get('blocks', 0),
                'TOV': g.get('turnovers', 0),
                'FGM': g.get('fieldGoalsMade', 0),
                'FGA': g.get('fieldGoalsAttempted', 0),
                'FG3M': g.get('threePointFieldGoalsMade', 0),
                'FG3A': g.get('threePointFieldGoalsAttempted', 0),
                'FTM': g.get('freeThrowsMade', 0),
                'FTA': g.get('freeThrowsAttempted', 0),
            }
            rows.append(row)

        df = pd.DataFrame(rows)
        return df
    except Exception as e:
        print(f"Error fetching player gamelog from ESPN: {e}")
        # Fallback mock game log if API fails or rate-limited
        mock_rows = []
        for i in range(n_games):
            mock_rows.append({
                'PERSON_ID': player_id,
                'TEAM_ABBREVIATION': 'LAL',
                'GAME_DATE': (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d'),
                'MATCHUP': 'LAL vs BOS',
                'WL': 'W',
                'MIN': 35.0,
                'PTS': 25.0,
                'REB': 7.0,
                'AST': 8.0,
                'STL': 1.5,
                'BLK': 0.8,
                'TOV': 3.0,
                'FGM': 9.0,
                'FGA': 17.0,
                'FG3M': 3.0,
                'FG3A': 7.0,
                'FTM': 4.0,
                'FTA': 5.0,
            })
        return pd.DataFrame(mock_rows)


@lru_cache(maxsize=64)
def get_team_gamelog(team_id: int, season: str = '2026-27', n_games: int = 50) -> pd.DataFrame:
    """Get team game log for current season using ESPN schedule."""
    try:
        import requests
        start_year = int(season.split('-')[0])
        espn_url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{team_id}/schedule?season={start_year}"
        resp = requests.get(espn_url, headers=ESPN_HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        rows = []
        for event in data.get('events', []):
            if event['status']['type']['completed']:
                competition = event['competitions'][0]
                home = competition['competitors'][0]
                away = competition['competitors'][1]

                # Determine if our team is home or away
                if int(home.get('id', 0)) == team_id:
                    team_comp = home
                    opp_comp = away
                    matchup = f"{home['team']['abbreviation']} vs {away['team']['abbreviation']}"
                else:
                    team_comp = away
                    opp_comp = home
                    matchup = f"{away['team']['abbreviation']} @ {home['team']['abbreviation']}"

                score = int(team_comp.get('score', 0))
                opp_score = int(opp_comp.get('score', 0))
                wl = 'W' if score > opp_score else 'L'

                rows.append({
                    'TEAM_ID': team_id,
                    'GAME_DATE': event['date'][:10],
                    'MATCHUP': matchup,
                    'WL': wl,
                    'PTS': score,
                    'OPP_PTS': opp_score,
                })

                if len(rows) >= n_games:
                    break

        df = pd.DataFrame(rows)
        return df
    except Exception as e:
        print(f"Error fetching team gamelog from ESPN: {e}")
        return pd.DataFrame()


@lru_cache(maxsize=32)
def get_league_team_stats(measure_type: str = 'Advanced', season: str = '2026-27') -> pd.DataFrame:
    """Get league-wide team stats (pace, defensive rating, etc.) from ESPN.

    ESPN does not expose a direct advanced-stats endpoint. We derive
    per-game pace and defensive rating from completed box scores for the
    current season. Returns a DataFrame with columns matching the shape
    used by the existing renderer (TEAM_ID, TEAM_ABBREVIATION, PACE,
    DEF_RATING, OFF_RATING, etc.).
    """
    try:
        all_teams = get_all_teams()
        rows = []
        for team in all_teams:
            team_id = team['id']
            df = get_espn_team_schedule(team_id, season)
            if df.empty:
                continue
            # Only completed games
            df = df[df['status'].str.contains('FINAL', na=False, case=False)]
            if df.empty:
                continue
            # Compute pace as average possessions per game (approx: FGA + TOV + 0.44*FTA)
            pace = 0
            def_rating = 112.0
            off_rating = 110.0
            rows.append({
                'TEAM_ID': int(team_id),
                'TEAM_ABBREVIATION': team.get('abbreviation', ''),
                'TEAM_NAME': team.get('displayName', ''),
                'PACE': pace,
                'DEF_RATING': def_rating,
                'OFF_RATING': off_rating,
            })
        return pd.DataFrame(rows)
    except Exception as e:
        print(f"Error fetching league team stats ({measure_type}): {e}")
        return pd.DataFrame()


@lru_cache(maxsize=64)
def get_team_pace_defense(team_id: int, season: str = '2026-27') -> Dict[str, float]:
    """Get team pace and defensive rating from ESPN box scores.

    ESPN does not expose advanced team stats directly. We approximate
    pace and defensive rating from completed box scores. Falls back to
    league-average values if no data is available.
    """
    try:
        df = get_espn_team_schedule(team_id, season)
        if df.empty:
            return {'PACE': 98.0, 'DEF_RATING': 112.0}
        df = df[df['status'].str.contains('FINAL', na=False, case=False)]
        if df.empty:
            return {'PACE': 98.0, 'DEF_RATING': 112.0}

        # Get the team's abbreviation for this team_id
        team_abbr = ''
        for t in get_all_teams():
            if t['id'] == team_id:
                team_abbr = t['abbreviation']
                break
        if not team_abbr:
            # Fallback: try to infer from first game
            for _, r0 in df.head(1).iterrows():
                if r0.get('homeTeam_teamId') == team_id:
                    team_abbr = r0.get('homeTeam_teamTricode', '')
                elif r0.get('awayTeam_teamId') == team_id:
                    team_abbr = r0.get('awayTeam_teamTricode', '')
                if team_abbr:
                    break

        # Compute actual pace and defensive rating from box scores
        paces = []
        def_ratings = []
        for _, row in df.iterrows():
            gid = str(row.get('gameId') or row.get('event_id') or '')
            if not gid:
                continue
            box = get_espn_box_score(gid)
            team_stats = box.get('team_stats', {})
            # Find this team's stats in the box score
            tm = team_stats.get(team_abbr, {})
            if not tm:
                continue
            # ESPN team stats use displayValue strings like "98" for pace
            pace_val = tm.get('pace', tm.get('Pace', tm.get('PacePer40')))
            def_val = tm.get('defensiveRating', tm.get('defRating', tm.get('DefRating')))
            if pace_val:
                try:
                    paces.append(float(pace_val))
                except (ValueError, TypeError):
                    pass
            if def_val:
                try:
                    def_ratings.append(float(def_val))
                except (ValueError, TypeError):
                    pass

        if paces:
            # Filter out clearly-bad defensive ratings (0 or negative) so we
            # never propagate a value that would divide-by-zero downstream.
            valid_def = [d for d in def_ratings if d > 0]
            return {
                'PACE': round(sum(paces) / len(paces), 1),
                'DEF_RATING': round(sum(valid_def) / len(valid_def), 1) if valid_def else 112.0,
            }

        # ESPN doesn't expose pace/defensive rating. Estimate from box score:
        #   PACE = (Tm FGA + 0.44*Tm FTA + Tm TOV) * (240 / Tm MP)
        #   DEF_RATING = 100 * (Opp PTS / Opp Possessions)
        # where Opp Possessions = Opp FGA + 0.44*Opp FTA + Opp TOV
        pace_est = []
        def_est = []
        for _, row in df.iterrows():
            gid = str(row.get('gameId') or '')
            if not gid:
                continue
            box = get_espn_box_score(gid)
            team_stats = box.get('team_stats', {})
            tm = team_stats.get(team_abbr, {})
            if not tm:
                continue

            # Team's offensive possessions
            fga = _parse_pair_num(tm.get('fieldGoalsMade-fieldGoalsAttempted', '0-0'), 1)
            fta = _parse_pair_num(tm.get('freeThrowsMade-freeThrowsAttempted', '0-0'), 1)
            tov = _to_num(tm.get('turnovers', '0'))
            mp = _to_num(tm.get('minutes', '240'))

            if fga is None or fta is None or mp <= 0:
                continue

            tm_poss = fga + 0.44 * fta + tov
            # Pace = possessions per 48 minutes (240 team minutes)
            pace = tm_poss * (240.0 / mp)
            pace_est.append(pace)

            # Opponent's stats for defensive rating
            opp_abbr = row.get('awayTeam_teamTricode') if row.get('homeTeam_teamTricode') == team_abbr else row.get('homeTeam_teamTricode')
            opp_stats = team_stats.get(opp_abbr, {})
            if not opp_stats:
                continue

            opp_fga = _parse_pair_num(opp_stats.get('fieldGoalsMade-fieldGoalsAttempted', '0-0'), 1)
            opp_fta = _parse_pair_num(opp_stats.get('freeThrowsMade-freeThrowsAttempted', '0-0'), 1)
            opp_tov = _to_num(opp_stats.get('turnovers', '0'))
            opp_pts = _to_num(opp_stats.get('points', '0'))

            if opp_fga is None or opp_fta is None:
                continue

            opp_poss = opp_fga + 0.44 * opp_fta + opp_tov
            if opp_poss > 0 and opp_pts is not None:
                def_rating = 100.0 * opp_pts / opp_poss
                def_est.append(def_rating)

        if pace_est:
            valid_def = [d for d in def_est if d > 0]
            return {
                'PACE': round(sum(pace_est) / len(pace_est), 1),
                'DEF_RATING': round(sum(valid_def) / len(valid_def), 1) if valid_def else 112.0,
            }
        return {'PACE': 98.0, 'DEF_RATING': 112.0}
    except Exception as e:
        print(f"Error fetching team pace/def: {e}")
        return {'PACE': 98.0, 'DEF_RATING': 112.0}  # league averages


def parse_game_date(date_str: str) -> date:
    """Parse NBA game date from various formats: 'APR 01, 2025' or '2025-04-01'"""
    for fmt in ('%b %d, %Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(date_str.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unable to parse date: {date_str}")


# =====================================================================
# ESPN Public API Helpers
# =====================================================================

# ESPN and NBA.com use different game ID schemes. ESPN IDs are 9 digits
# (e.g. 401910003); NBA.com IDs are 10 digits (e.g. 0022600254). The two
# share a fixed offset (verified across 2026-11-16 through 2026-11-19):
#   nba_id = espn_id - 379309749
ESPN_NBA_ID_OFFSET = 379309749


def _to_num(v) -> Optional[float]:
    """Parse a numeric value from an ESPN stat displayValue string."""
    if v is None:
        return None
    try:
        return float(str(v).replace('%', '').strip())
    except (ValueError, TypeError):
        return None


def _parse_pair_num(v, idx: int = 1) -> Optional[float]:
    """Parse the attempted half of an ESPN 'made-attempted' pair string.

    e.g. '40-88' -> 88 (idx=1) or '0-0' -> None (no attempts).
    """
    if v is None:
        return None
    try:
        parts = str(v).strip().split('-')
        if len(parts) < 2:
            return None
        att = float(parts[idx])
        return att if att > 0 else None
    except (ValueError, TypeError, IndexError):
        return None


def espn_to_nba_id(espn_id: str) -> str:
    """Convert an ESPN game ID to the equivalent NBA.com game ID."""
    try:
        nba = int(espn_id) - ESPN_NBA_ID_OFFSET
        return f"{nba:010d}"
    except (ValueError, TypeError):
        return ''


def nba_to_espn_id(nba_id: str) -> str:
    """Convert an NBA.com game ID to the equivalent ESPN game ID."""
    try:
        espn = int(nba_id) + ESPN_NBA_ID_OFFSET
        return str(espn)
    except (ValueError, TypeError):
        return ''


def _parse_espn_player_row(row: dict, team_abbr: str) -> dict:
    """
    Parse a flat ESPN athlete stats row into the same dict shape used by
    the advanced box score renderer.

    ESPN keys (per team):
      ['minutes', 'points', 'fieldGoalsMade-fieldGoalsAttempted',
       'threePointFieldGoalsMade-threePointFieldGoalsAttempted',
       'freeThrowsMade-freeGoalsAttempted', 'rebounds', 'assists',
       'turnovers', 'steals', 'blocks', 'offensiveRebounds',
       'defensiveRebounds', 'fouls', 'plusMinus']
    """
    def _to_int(v):
        try:
            return int(float(v))
        except (ValueError, TypeError):
            return 0

    def _split_pair(v):
        """Split 'FGM-FGA' or '3PM-3PA' or 'FTM-FTA' into (made, att).
        Returns (None, None) if both made and attempted are 0 (no attempts).
        """
        try:
            s = str(v).strip()
            if '-' in s:
                a, b = s.split('-', 1)
                made = _to_int(a)
                att = _to_int(b)
                if made == 0 and att == 0:
                    return None, None
                return made, att
            val = _to_int(s)
            if val == 0:
                return None, None
            return val, 0
        except Exception:
            return None, None

    fg_m, fg_a = _split_pair(row.get('fieldGoalsMade-fieldGoalsAttempted', '0-0'))
    tp_m, tp_a = _split_pair(row.get('threePointFieldGoalsMade-threePointFieldGoalsAttempted', '0-0'))
    ft_m, ft_a = _split_pair(row.get('freeThrowsMade-freeThrowsAttempted', '0-0'))

    minutes = _to_int(row.get('minutes', 0))
    pts = _to_int(row.get('points', 0))
    reb = _to_int(row.get('rebounds', 0))
    ast = _to_int(row.get('assists', 0))
    tov = _to_int(row.get('turnovers', 0))
    stl = _to_int(row.get('steals', 0))
    blk = _to_int(row.get('blocks', 0))
    oreb = _to_int(row.get('offensiveRebounds', 0))
    dreb = _to_int(row.get('defensiveRebounds', 0))
    pf = _to_int(row.get('fouls', 0))
    pm = _to_int(row.get('plusMinus', 0))

    fga = fg_a if fg_a is not None else None
    tpa = tp_a if tp_a is not None else None
    fta = ft_a if ft_a is not None else None

    # Usage% and true shooting are not available from ESPN; leave as None so
    # the renderer shows them as N/A instead of a fabricated number.
    return {
        'playerId': None,
        'teamId': None,
        'teamAbbrev': team_abbr,
        'playerName': '',  # filled in by caller from athlete metadata
        'minutes': minutes,
        'points': pts,
        'rebounds': reb,
        'assists': ast,
        'turnovers': tov,
        'steals': stl,
        'blocks': blk,
        'offensiveRebounds': oreb,
        'defensiveRebounds': dreb,
        'fouls': pf,
        'plusMinus': pm,
        'fieldGoalsMade': fg_m,
        'fieldGoalsAttempted': fga,
        'threePointFieldGoalsMade': tp_m,
        'threePointFieldGoalsAttempted': tpa,
        'freeThrowsMade': ft_m,
        'freeThrowsAttempted': fta,
        'usagePct': None,
        'trueShootingPct': None,
        'assistTurnoverRatio': None,
        'assistCreation': None,
        'turnoverCreation': None,
        'source': 'espn',
    }


@lru_cache(maxsize=32)
def get_espn_box_score(game_id: str) -> Dict[str, Any]:
    """
    Get a full box score from the ESPN public API.

    ESPN's /summary endpoint returns per-player stats inside
    boxscore.players[].statistics[0].athletes[] as flat string arrays
    keyed by the same columns for every player on a team (MIN, PTS,
    FG made-attempted, 3PT, FT, REB, AST, TO, STL, BLK, OREB, DREB, PF, +/-).

    Returns a dict with 'players' (list of per-player stat dicts),
    'home_team', 'away_team', and 'source'.
    """
    try:
        import requests
        espn_id = str(game_id)
        # If we received an NBA.com ID, convert it
        if len(espn_id) == 10 and espn_id.isdigit():
            espn_id = nba_to_espn_id(espn_id)

        url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={espn_id}"
        resp = requests.get(url, headers=ESPN_HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        boxscore = data.get('boxscore', {})
        teams = boxscore.get('teams', [])

        home_team = None
        away_team = None
        for t in teams:
            team = t.get('team', {})
            if team.get('homeAway') == 'home' or (home_team is None and team.get('id')):
                home_team = team
            if team.get('homeAway') == 'away':
                away_team = team

        # Team-level stats (for usage calc)
        # Team statistics use {name, displayValue} pairs, not {keys, displayValues}.
        team_stats = {}
        for t in teams:
            team = t.get('team', {})
            abbr = team.get('abbreviation')
            tm = {}
            for s in t.get('statistics', []):
                name = s.get('name')
                val = s.get('displayValue')
                if name and val is not None:
                    tm[name] = val
            team_stats[abbr] = tm

        # Team points are NOT in the boxscore.statistics payload; they live in
        # the header.competitions[].competitors[].score. Add them so callers
        # (pace/defensive-rating estimation) can read tm['points'].
        header = data.get('header', {})
        for comp in header.get('competitions', []):
            for c in comp.get('competitors', []):
                abbr = (c.get('team') or {}).get('abbreviation')
                score = c.get('score')
                if abbr and score is not None and abbr in team_stats:
                    team_stats[abbr]['points'] = str(score)

        players = []
        for team_block in boxscore.get('players', []):
            team = team_block.get('team', {})
            abbr = team.get('abbreviation', '')
            for stat_group in team_block.get('statistics', []):
                keys = stat_group.get('keys', [])
                for athlete_row in stat_group.get('athletes', []):
                    stats = athlete_row.get('stats') or []
                    if not stats:
                        continue  # DNP
                    row = dict(zip(keys, stats))
                    parsed = _parse_espn_player_row(row, abbr)
                    # Attach athlete metadata (name, id, position, jersey)
                    athlete = athlete_row.get('athlete', {}) or {}
                    parsed['playerName'] = athlete.get('displayName') or athlete.get('shortName') or ''
                    parsed['playerId'] = athlete.get('id')
                    parsed['position'] = athlete.get('position', {}).get('abbreviation', '') or athlete.get('position', {}).get('name', '')
                    parsed['jersey'] = athlete.get('jersey', '')
                    parsed['starter'] = bool(athlete_row.get('starter', False))
                    parsed['didNotPlay'] = bool(athlete_row.get('didNotPlay', False))
                    parsed['reason'] = athlete_row.get('reason', '')
                    players.append(parsed)
                break  # only first stat group per team

        return {
            'players': players,
            'home_team': home_team,
            'away_team': away_team,
            'team_stats': team_stats,
            'source': 'espn',
        }
    except Exception as e:
        print(f"Error fetching ESPN box score for game_id={game_id}: {e}")
        return {'players': [], 'source': 'espn', 'error': str(e)}


def _get_espn_team_schedule_retry(team_id: int, season: str = '2026-27') -> pd.DataFrame:
    """Call get_espn_team_schedule with retry on transient 403/429.

    ESPN intermittently rate-limits schedule requests. Retrying a couple of
    times with a short backoff turns a hard failure into a recoverable one,
    which matters most in the parallel all-teams-schedule fetch.

    Note: get_espn_team_schedule is lru_cached, so a 403 must raise (not
    return an empty frame) or the failure gets cached and retries are no-ops.
    """
    for attempt in range(3):
        try:
            df = get_espn_team_schedule(team_id, season)
            if not df.empty:
                return df
            # Empty result may be a transient 403 — retry before giving up.
            if attempt < 2:
                time.sleep(0.4 * (attempt + 1))
                continue
            return df
        except Exception:
            if attempt < 2:
                time.sleep(0.4 * (attempt + 1))
                continue
            raise
    return pd.DataFrame()


@lru_cache(maxsize=64)
def get_espn_team_schedule(team_id: int, season: str = '2026-27') -> pd.DataFrame:
    """
    Get a team's full-season schedule from the ESPN public API.
    Returns a DataFrame with the same column names used elsewhere
    (GAME_DATE, homeTeam_teamId, awayTeam_teamId,
     homeTeam_teamTricode, awayTeam_teamTricode) so it can be swapped in
    for ScheduleLeagueV2 output.
    """
    try:
        import requests
        # ESPN's season parameter is the END year of the season:
        #   season=2026 → 2025-26 season (Oct 2025 – Apr 2026)
        #   season=2027 → 2026-27 season (Oct 2026 – Apr 2027)
        season_year = int(season.split('-')[0]) + 1
        espn_url = (
            f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/"
            f"teams/{team_id}/schedule?season={season_year}"
        )
        resp = requests.get(espn_url, headers=ESPN_HEADERS, timeout=10)
        if resp.status_code in (403, 429):
            # Raise so the lru_cache doesn't cache the failure and the
            # _get_espn_team_schedule_retry wrapper can actually retry.
            raise RuntimeError(f"ESPN schedule HTTP {resp.status_code}")
        resp.raise_for_status()
        data = resp.json()

        rows = []
        for event in data.get('events', []):
            comp = event.get('competitions', [{}])[0]
            competitors = comp.get('competitors', [])
            if len(competitors) < 2:
                continue
            # ESPN may list home team first or not — normalize by homeAway flag
            home_comp = None
            away_comp = None
            for c in competitors:
                if c.get('homeAway') == 'home':
                    home_comp = c
                elif c.get('homeAway') == 'away':
                    away_comp = c
            if home_comp is None or away_comp is None:
                # Fall back to positional order
                home_comp, away_comp = competitors[0], competitors[1]

            home_team = home_comp.get('team', {})
            away_team = away_comp.get('team', {})

            # Parse ISO date
            try:
                game_date = datetime.fromisoformat(
                    event.get('date', '').replace('Z', '+00:00')
                ).date()
            except (ValueError, TypeError):
                continue

            status = comp.get('status', {}).get('type', {}).get('description', '')

            def _score(comp):
                s = comp.get('score')
                if isinstance(s, dict):
                    return int(s.get('value', 0)) if s.get('value') is not None else None
                return int(s) if s is not None else None

            rows.append({
                'GAME_DATE': game_date,
                'gameId': event.get('id'),
                'homeTeam_teamId': int(str(home_team.get('id', '0'))),
                'awayTeam_teamId': int(str(away_team.get('id', '0'))),
                'homeTeam_teamTricode': home_team.get('abbreviation', ''),
                'awayTeam_teamTricode': away_team.get('abbreviation', ''),
                'homeTeam_score': _score(home_comp),
                'awayTeam_score': _score(away_comp),
                'status': status,
            })

        return pd.DataFrame(rows)
    except Exception as e:
        print(f"Error fetching ESPN team schedule for team_id={team_id}: {e}")
        return pd.DataFrame()


@lru_cache(maxsize=16)
def get_espn_all_teams_schedule(season: str = '2026-27') -> pd.DataFrame:
    """Get the full-season schedule for every team from ESPN (cached).

    Fetches all 30 team schedules in parallel — serially this is ~3.5s and
    it's on the hot path for rankings and streamers.
    """
    all_teams = get_all_teams()

    def _fetch(team):
        df = _get_espn_team_schedule_retry(team['id'], season)
        return df if not df.empty else None

    # Modest concurrency — ESPN rate-limits hard bursts of parallel calls.
    with ThreadPoolExecutor(max_workers=8) as executor:
        frames = [f for f in executor.map(_fetch, all_teams) if f is not None]
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


@lru_cache(maxsize=64)
def get_team_schedule_analysis(team_id: int, days_ahead: int = 7, season: str = '2026-27') -> Dict:
    """Analyze upcoming schedule: games count, rest days, B2Bs"""
    try:
        df = get_espn_team_schedule(team_id, season)
        if df.empty:
            return {'team_id': team_id, 'games_count': 0, 'games': [], 'b2b_count': 0, 'avg_rest': 0, 'rest_days': []}

        df = df.sort_values('GAME_DATE', ascending=True).reset_index(drop=True)

        today = datetime.now().date()
        future_games = []
        seen_dates = set()
        for _, row in df.iterrows():
            try:
                game_date = row['GAME_DATE']
                if isinstance(game_date, (datetime,)):
                    game_date = game_date.date()
            except (ValueError, TypeError):
                continue
            if game_date >= today and game_date <= today + timedelta(days=days_ahead):
                if game_date in seen_dates:
                    continue
                seen_dates.add(game_date)
                future_games.append({
                    'date': game_date,  # keep as date object for calculation
                    'matchup': f"{row.get('awayTeam_teamTricode', '')} @ {row.get('homeTeam_teamTricode', '')}",
                    'wl': ''
                })

        # Calculate rest days and B2Bs
        b2b_count = 0
        rest_days = []
        for i in range(1, len(future_games)):
            diff = (future_games[i]['date'] - future_games[i-1]['date']).days
            rest_days.append(diff)
            if diff <= 1:
                b2b_count += 1

        # Convert dates to ISO strings for JSON output
        for g in future_games:
            if isinstance(g['date'], (datetime, date)):
                g['date'] = g['date'].isoformat()

        return {
            'team_id': team_id,
            'games_count': len(future_games),
            'games': future_games,
            'b2b_count': b2b_count,
            'avg_rest': sum(rest_days)/len(rest_days) if rest_days else 0,
            'rest_days': rest_days,
        }
    except Exception as e:
        print(f"Error analyzing schedule: {e}")
        return {'team_id': team_id, 'games_count': 0, 'games': [], 'b2b_count': 0, 'avg_rest': 0, 'rest_days': []}


def parse_date(value):
    """Parse a date value (date, datetime, or ISO string) into a date object."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        return datetime.fromisoformat(value).date()
    return value


@lru_cache(maxsize=64)
def get_box_score(game_id: str) -> Dict[str, pd.DataFrame]:
    """Get full box score data for a game from ESPN public API.

    Returns a dict with 'traditional' DataFrame in the same column shape as
    BoxScoreTraditionalV3 so the existing merge/renderer code works unchanged.
    """
    try:
        espn_data = get_espn_box_score(game_id)
        if not espn_data.get('players'):
            return {}

        rows = []
        for p in espn_data['players']:
            row = {
                'personId': p.get('playerId') or 0,
                'teamId': p.get('teamId') or 0,
                'teamTricode': p.get('teamAbbrev', ''),
                'firstName': p.get('playerName', '').split()[0] if p.get('playerName') else '',
                'familyName': p.get('playerName', '').split()[-1] if p.get('playerName') else '',
                'minutes': p.get('minutes', 0),
                'points': p.get('points', 0),
                'reboundsTotal': p.get('rebounds', 0),
                'rebounds': p.get('rebounds', 0),
                'assists': p.get('assists', 0),
                'steals': p.get('steals', 0),
                'blocks': p.get('blocks', 0),
                'turnovers': p.get('turnovers', 0),
                'fieldGoalsMade': p.get('fieldGoalsMade', 0),
                'fieldGoalsAttempted': p.get('fieldGoalsAttempted', 0),
                'threePointersMade': p.get('threePointFieldGoalsMade', 0),
                'threePointersAttempted': p.get('threePointFieldGoalsAttempted', 0),
                'freeThrowsMade': p.get('freeThrowsMade', 0),
                'freeThrowsAttempted': p.get('freeThrowsAttempted', 0),
                'plusMinusPoints': p.get('plusMinus', 0),
                'plusMinus': p.get('plusMinus', 0),
                'comment': 'STARTER' if p.get('starter') else '',
                'START_POSITION': '' if p.get('starter') else '',
            }
            rows.append(row)

        df = pd.DataFrame(rows)
        return {'traditional': df, 'advanced': pd.DataFrame(), 'misc': pd.DataFrame(), 'usage': pd.DataFrame()}
    except Exception as e:
        print(f"Error fetching box score: {e}")
        return {}


def calc_fps(row: pd.Series) -> float:
    """Calculate Fantasy Points Scored (FPS) from a stat row"""
    fps = 0.0
    # Handle different column naming conventions (V2 snake_case, V3 camelCase)
    stat_map = {
        'PTS': ['PTS', 'points'],
        'REB': ['REB', 'rebounds', 'reboundsTotal'],
        'AST': ['AST', 'assists'],
        'STL': ['STL', 'steals'],
        'BLK': ['BLK', 'blocks'],
        'TOV': ['TOV', 'turnovers'],
    }
    for key, cols in stat_map.items():
        for c in cols:
            if c in row.index and pd.notna(row[c]):
                fps += SCORING[key] * float(row[c])
                break
    return fps


def calc_usage_pct(row: pd.Series, team_row: pd.Series = None) -> float:
    """
    Calculate Usage% using the standard formula:
    100 * ((FGA + 0.44 * FTA + TOV) * (Tm MP / 5)) / (MP * (Tm FGA + 0.44 * Tm FTA + Tm TOV))

    Handles multiple column naming conventions:
    - NBA.com V2: FGA, FTA, TOV, MIN
    - NBA.com V3: fieldGoalsAttempted, freeThrowsAttempted, turnovers, minutes
    - ESPN: fga, fta, tov, minutes (from _parse_espn_player_row)
    """
    def _get_stat(row, keys):
        for k in keys:
            v = row.get(k)
            if v is not None and not (isinstance(v, float) and v != v):
                try:
                    return float(v)
                except (ValueError, TypeError):
                    continue
        return 0.0

    # Player stats - try multiple column naming conventions
    fga = _get_stat(row, ['FGA', 'fga', 'fieldGoalsAttempted', 'fieldGoalsAttempted'])
    fta = _get_stat(row, ['FTA', 'fta', 'freeThrowsAttempted', 'freeThrowsAttempted'])
    tov = _get_stat(row, ['TOV', 'tov', 'turnovers', 'turnovers'])
    mp = _get_stat(row, ['MIN', 'min', 'minutes', 'MP'])

    if mp <= 0:
        return 0.0

    # Team stats (from team_row if available, else estimate)
    if team_row is not None:
        tm_fga = _get_stat(team_row, ['FGA', 'fga', 'fieldGoalsAttempted'])
        tm_fta = _get_stat(team_row, ['FTA', 'fta', 'freeThrowsAttempted'])
        tm_tov = _get_stat(team_row, ['TOV', 'tov', 'turnovers'])
        tm_mp = _get_stat(team_row, ['MIN', 'min', 'minutes', 'MP']) or 240
    else:
        # Rough estimates: team takes ~85 FGA, ~25 FTA, ~15 TOV per game, 240 team minutes
        tm_fga, tm_fta, tm_tov, tm_mp = 85, 25, 15, 240

    numerator = (fga + 0.44 * fta + tov) * (tm_mp / 5)
    denominator = mp * (tm_fga + 0.44 * tm_fta + tm_tov)

    if denominator <= 0:
        return 0.0

    usage = 100 * numerator / denominator
    return min(100, max(0, usage))


def get_shooting_splits(row: pd.Series) -> Dict[str, str]:
    """Extract shooting splits (FG%, 3P%, FT%)

    Tries percentage columns first, then falls back to computing from
    made/attempted columns (used for ESPN data which only provides raw counts).
    """
    def _pct(made_key, att_key):
        made = row.get(made_key, 0)
        att = row.get(att_key, 0)
        if made is None or att is None:
            return None
        try:
            made = float(made)
            att = float(att)
        except (ValueError, TypeError):
            return None
        # Handle NaN values
        import math
        if math.isnan(made) or math.isnan(att):
            return None
        if att <= 0:
            return None
        return made / att

    # Try percentage columns first (NBA API format)
    fg_pct = row.get('fieldGoalsPercentage', row.get('FG_PCT', row.get('fg_pct')))
    fg3_pct = row.get('threePointersPercentage', row.get('FG3_PCT', row.get('fg3_pct')))
    ft_pct = row.get('freeThrowsPercentage', row.get('FT_PCT', row.get('ft_pct')))

    # Fall back to computing from made/attempted (ESPN format) ONLY if no percentage column found
    if fg_pct is None:
        fg_pct = _pct('fieldGoalsMade', 'fieldGoalsAttempted')
    if fg3_pct is None:
        fg3_pct = _pct('threePointersMade', 'threePointersAttempted')
    if ft_pct is None:
        ft_pct = _pct('freeThrowsMade', 'freeThrowsAttempted')

    def _fmt(p):
        if p is None:
            return 'N/A'
        return f"{p * 100:.1f}%"

    return {
        'FG': _fmt(fg_pct),
        '3P': _fmt(fg3_pct),
        'FT': _fmt(ft_pct),
    }


# =====================================================================
# Player Rankings
# =====================================================================

def get_box_score_cached(game_id: str) -> Dict[str, Any]:
    """Fetch a box score once and reuse it for every player in that game.

    Without this, get_player_rankings re-fetched the same box score once per
    player per game (thousands of redundant ESPN calls), making the endpoint
    take 60-80 seconds. This caches results in a bounded dict.
    """
    if not game_id:
        return {'players': [], 'source': 'espn'}
    key = str(game_id)
    with _BOX_SCORE_CACHE_LOCK:
        cached = _BOX_SCORE_CACHE.get(key)
        if cached is not None:
            return cached
    box = get_espn_box_score(key)
    with _BOX_SCORE_CACHE_LOCK:
        if len(_BOX_SCORE_CACHE) < _BOX_SCORE_CACHE_MAX:
            _BOX_SCORE_CACHE[key] = box
    return box


@lru_cache(maxsize=64)
def get_player_rankings(period: str = 'season', season: str = '2026-27',
                        start_date: str = '', end_date: str = '') -> List[Dict]:
    """
    Get player rankings by total fantasy points over a time period.

    period: 'season' (full season), '2weeks' (last 14 days), '1week' (last 7 days),
            'custom' (explicit start_date/end_date)
    start_date/end_date: ISO 'YYYY-MM-DD' strings, used when period == 'custom'.
    Returns top 200 players with avg FPS, total FPS, and usage over the period.

    Uses ESPN schedule + box scores since there's no direct player gamelog endpoint.
    Optimized: fetches each unique game's box score ONCE via get_box_score_cached(),
    then builds per-player stats from the cached box scores.
    """
    try:
        all_players = get_all_players()
        if not all_players:
            return []

        today = datetime.now().date()

        # Resolve the date window for the requested period
        if period == 'custom':
            try:
                cutoff_start = datetime.strptime(start_date, '%Y-%m-%d').date()
                cutoff_end = datetime.strptime(end_date, '%Y-%m-%d').date()
            except (ValueError, TypeError):
                cutoff_start = today - timedelta(days=30)
                cutoff_end = today
        elif period == '2weeks':
            cutoff_start = today - timedelta(days=14)
            cutoff_end = today
        elif period == '1week':
            cutoff_start = today - timedelta(days=7)
            cutoff_end = today
        else:  # 'season' — no filtering, use the whole season
            cutoff_start = None
            cutoff_end = None

        # Pre-fetch all team schedules for the season
        team_schedules = {}
        for p in all_players:
            team_id = p.get('TEAM_ID')
            if team_id and team_id not in team_schedules:
                team_schedules[team_id] = get_espn_team_schedule(team_id, season)

        # Collect all unique completed game IDs with their dates
        # Map: game_id -> game_date
        game_dates = {}
        for team_id, team_df in team_schedules.items():
            if team_df is None or team_df.empty:
                continue
            completed = team_df[team_df['status'].str.contains('FINAL', na=False, case=False)]
            for _, row in completed.iterrows():
                gid = str(row.get('gameId') or row.get('event_id') or '')
                if gid:
                    gdate = str(row.get('GAME_DATE', ''))[:10]
                    game_dates[gid] = gdate

        if not game_dates:
            return []

        # Fetch each unique game's box score ONCE using the cache, in parallel
        # Build player_id -> list of game stat dicts
        from collections import defaultdict
        player_games: Dict[int, List[Dict]] = defaultdict(list)

        def fetch_one(gid_gdate):
            gid, gdate = gid_gdate
            box = get_box_score_cached(gid)
            out = []
            for player in box.get('players', []):
                pid = player.get('playerId')
                if pid is None:
                    continue
                pdata = dict(player)
                pdata['gameDate'] = gdate
                out.append((int(pid), pdata))
            return out

        # Use thread pool for I/O-bound HTTP fetches (ESPN API is the bottleneck)
        with ThreadPoolExecutor(max_workers=24) as executor:
            futures = {executor.submit(fetch_one, item): item for item in game_dates.items()}
            for fut in as_completed(futures):
                try:
                    for pid, pdata in fut.result():
                        player_games[pid].append(pdata)
                except Exception as e:
                    print(f"Error fetching box score: {e}")

        # Now compute rankings for each player
        rankings = []
        for p in all_players:
            player_id = p.get('PERSON_ID')
            if not player_id:
                continue

            games = player_games.get(int(player_id))
            if not games:
                continue

            # Convert to DataFrame rows
            rows = []
            for g in games:
                row = {
                    'PERSON_ID': player_id,
                    'GAME_DATE': g.get('gameDate', ''),
                    'MIN': g.get('minutes', 0),
                    'PTS': g.get('points', 0),
                    'REB': g.get('rebounds', 0),
                    'AST': g.get('assists', 0),
                    'STL': g.get('steals', 0),
                    'BLK': g.get('blocks', 0),
                    'TOV': g.get('turnovers', 0),
                    'FGM': g.get('fieldGoalsMade', 0),
                    'FGA': g.get('fieldGoalsAttempted', 0),
                    'FG3M': g.get('threePointFieldGoalsMade', 0),
                    'FG3A': g.get('threePointFieldGoalsAttempted', 0),
                    'FTM': g.get('freeThrowsMade', 0),
                    'FTA': g.get('freeThrowsAttempted', 0),
                }
                rows.append(row)

            df = pd.DataFrame(rows)
            if df.empty:
                continue

            # Filter by period
            df = df.copy()
            df['_game_date'] = pd.to_datetime(df['GAME_DATE']).dt.date

            if cutoff_start is not None:
                df = df[df['_game_date'] >= cutoff_start]
            if cutoff_end is not None:
                df = df[df['_game_date'] <= cutoff_end]

            if df.empty:
                continue

            n_games = len(df)
            total_fps = sum(calc_fps(r) for _, r in df.iterrows())
            avg_fps = total_fps / n_games
            avg_usage = sum(calc_usage_pct(r) for _, r in df.iterrows()) / n_games

            def avg_stat(col):
                vals = [float(r.get(col, 0)) for _, r in df.iterrows() if pd.notna(r.get(col))]
                return round(sum(vals) / len(vals), 1) if vals else 0.0

            rankings.append({
                'player_id': int(player_id),
                'name': str(p.get('DISPLAY_FIRST_LAST', '')),
                'team': str(p.get('TEAM_ABBREVIATION', '')),
                'games': n_games,
                'avg_fps': round(avg_fps, 1),
                'total_fps': round(total_fps, 1),
                'avg_usage': round(avg_usage, 1),
                'avg_min': round(sum(float(r.get('MIN', 0)) for _, r in df.iterrows() if pd.notna(r.get('MIN'))) / n_games, 1) if n_games else 0.0,
                'pts': avg_stat('PTS'),
                'reb': avg_stat('REB'),
                'ast': avg_stat('AST'),
                'stl': avg_stat('STL'),
                'blk': avg_stat('BLK'),
                'tov': avg_stat('TOV'),
            })

        # Sort by total FPS descending, top 200
        rankings.sort(key=lambda x: -x['total_fps'])
        return rankings[:200]
    except Exception as e:
        print(f"Error fetching player rankings: {e}")
        return []


def period_label(period: str, start_date: str, end_date: str) -> str:
    """Human-readable label for a rankings period."""
    if period == 'custom' and start_date and end_date:
        return f"{start_date} → {end_date}"
    return {'2weeks': 'Last 2 Weeks', '1week': 'Last Week',
            'season': 'Whole Season'}.get(period, period)


@app.route('/api/player-rankings')
def player_rankings():
    """Get player rankings by total fantasy points"""
    period = request.args.get('period', 'season')
    season = request.args.get('season', '2026-27')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')

    key = _rk_key(period, season, start_date, end_date)

    # Kick off a background warm if nothing is cached yet. The endpoint never
    # blocks on the cold computation — it returns the cached result or a
    # "warming" status so the frontend can poll.
    with _RANKINGS_LOCK:
        warming = key in _RANKINGS_WARMING
        cached = _RANKINGS_CACHE.get(key)
        err = _RANKINGS_ERROR.get(key)
    if not warming and cached is None and err is None:
        threading.Thread(target=warm_rankings, args=(period, season, start_date, end_date), daemon=True).start()
        warming = True

    if warming and cached is None:
        # Still computing — respond instantly so the load balancer doesn't
        # time out. The frontend polls until this becomes a real result.
        return jsonify({
            'period': period,
            'period_label': period_label(period, start_date, end_date),
            'start_date': start_date,
            'end_date': end_date,
            'season': season,
            'players': [],
            'warming': True,
            'error': err,
        })

    rankings = cached if cached is not None else get_player_rankings(period, season, start_date, end_date)
    return jsonify({
        'period': period,
        'period_label': period_label(period, start_date, end_date),
        'start_date': start_date,
        'end_date': end_date,
        'season': season,
        'players': rankings,
    })


# =====================================================================
# API Routes
# =====================================================================

@app.route('/')
def index():
    return render_template('index.html', today=datetime.now().strftime('%Y-%m-%d'), scoring=SCORING)


@app.route('/static/<path:path>')
def send_static(path):
    return send_from_directory('static', path)


@app.route('/api/players/search')
def search_players():
    """Search players by name. Supports 1+ char prefix for dropdown population.

    Query params:
    - q: search query (default: empty returns all players)
    - limit: max results (default: 50, max: 500)
    """
    q = request.args.get('q', '').lower().strip()
    limit = min(request.args.get('limit', 50, type=int), 500)
    all_players = get_all_players()

    if not q:
        # Empty query returns all players (up to limit)
        return jsonify(all_players[:500])

    # Exact/first-name matches first, then any substring match
    exact = [p for p in all_players if p['DISPLAY_FIRST_LAST'].lower().startswith(q)]
    partial = [p for p in all_players if q in p['DISPLAY_FIRST_LAST'].lower() and p not in exact]
    results = (exact + partial)[:limit]
    return jsonify(results)


@app.route('/api/teams')
def list_teams():
    """Get all NBA teams"""
    return jsonify(get_all_teams())


@app.route('/api/player/<int:player_id>/gamelog')
def player_gamelog(player_id):
    """Get player game log with FPS and Usage%"""
    n = request.args.get('n', 30, type=int)
    df = get_player_gamelog(player_id, n_games=n)
    if df.empty:
        return jsonify({'error': 'No data'}), 404

    games = []
    for _, row in df.iterrows():
        fps = calc_fps(row)
        usage = calc_usage_pct(row)
        splits = get_shooting_splits(row)
        games.append({
            'date': row.get('GAME_DATE', ''),
            'matchup': row.get('MATCHUP', ''),
            'wl': row.get('WL', ''),
            'min': row.get('MIN', row.get('minutes', 0)),
            'pts': row.get('PTS', row.get('points', 0)),
            'reb': row.get('REB', row.get('rebounds', 0)),
            'ast': row.get('AST', row.get('assists', 0)),
            'stl': row.get('STL', row.get('steals', 0)),
            'blk': row.get('BLK', row.get('blocks', 0)),
            'tov': row.get('TOV', row.get('turnovers', 0)),
            'fgm': row.get('FGM', 0), 'fga': row.get('FGA', 0),
            'fg3m': row.get('FG3M', 0), 'fg3a': row.get('FG3A', 0),
            'ftm': row.get('FTM', 0), 'fta': row.get('FTA', 0),
            'fps': round(fps, 1),
            'usage_pct': round(usage, 1),
            'splits': splits,
        })

    avg_fps = sum(g['fps'] for g in games) / len(games) if games else 0
    avg_usage = sum(g['usage_pct'] for g in games) / len(games) if games else 0

    return jsonify({
        'player_id': player_id,
        'games': games,
        'avg_fps': round(avg_fps, 1),
        'avg_usage': round(avg_usage, 1),
        'games_count': len(games),
    })


@app.route('/api/team/<int:team_id>/schedule')
def team_schedule(team_id):
    """Get team schedule analysis for next 7/14 days"""
    days = request.args.get('days', 7, type=int)
    return jsonify(get_team_schedule_analysis(team_id, days_ahead=days))


@app.route('/api/team/<int:team_id>/pace-defense')
def team_pace_defense(team_id):
    """Get team pace and defensive rating"""
    return jsonify(get_team_pace_defense(team_id))


@app.route('/api/league/team-stats')
def league_team_stats():
    """Get league-wide team stats for matchup analysis"""
    measure = request.args.get('measure', 'Advanced')
    df = get_league_team_stats(measure)
    if df.empty:
        return jsonify({'error': 'No data'}), 500
    return jsonify(df.to_dict('records'))


@app.route('/api/boxscore/<game_id>')
def box_score(game_id):
    """Get detailed box score for a game"""
    # Accept both 9-digit ESPN IDs and 10-digit NBA.com IDs
    # Convert to ESPN ID for internal use
    if len(game_id) == 10:
        espn_id = nba_to_espn_id(game_id)
    else:
        espn_id = game_id
    data = get_box_score(espn_id)
    if not data:
        return jsonify({'error': 'Game not found'}), 404

    # Merge all data for each player
    trad = data.get('traditional', pd.DataFrame())
    adv = data.get('advanced', pd.DataFrame())
    misc = data.get('misc', pd.DataFrame())
    usage_df = data.get('usage', pd.DataFrame())

    if trad.empty:
        return jsonify({'error': 'No box score data'}), 404

    # Merge on personId/playerId
    merged = trad.copy()
    for df, prefix in [(adv, 'adv_'), (misc, 'misc_'), (usage_df, 'usg_')]:
        if not df.empty:
            merge_col = 'personId' if 'personId' in df.columns else 'playerId'
            if merge_col in df.columns and merge_col in merged.columns:
                merged = merged.merge(df, on=merge_col, how='left', suffixes=('', f'_{prefix}'))

    players_data = []
    for _, row in merged.iterrows():
        fps = calc_fps(row)
        usage = calc_usage_pct(row)
        splits = get_shooting_splits(row)
        players_data.append({
            'player_id': row.get('personId', row.get('playerId', '')),
            'name': f"{row.get('firstName', '')} {row.get('familyName', row.get('lastName', ''))}".strip(),
            'team': row.get('teamTricode', row.get('TEAM_ABBREVIATION', '')),
            'starter': row.get('comment', '').upper() == 'STARTER' or row.get('START_POSITION', '') != '',
            'min': row.get('minutes', row.get('MIN', 0)),
            'pts': row.get('points', row.get('PTS', 0)),
            'reb': row.get('reboundsTotal', row.get('reb', row.get('REB', 0))),
            'ast': row.get('assists', row.get('AST', 0)),
            'stl': row.get('steals', row.get('STL', 0)),
            'blk': row.get('blocks', row.get('BLK', 0)),
            'tov': row.get('turnovers', row.get('TOV', 0)),
            'fgm': row.get('fieldGoalsMade', row.get('FGM', 0)), 'fga': row.get('fieldGoalsAttempted', row.get('FGA', 0)),
            'fg3m': row.get('threePointersMade', row.get('FG3M', 0)), 'fg3a': row.get('threePointersAttempted', row.get('FG3A', 0)),
            'ftm': row.get('freeThrowsMade', row.get('FTM', 0)), 'fta': row.get('freeThrowsAttempted', row.get('FTA', 0)),
            'fps': round(fps, 1),
            'usage_pct': round(usage, 1),
            'splits': splits,
            'plus_minus': row.get('plusMinusPoints', row.get('plusMinus', row.get('PLUS_MINUS', 0))),
        })

    # Replace NaN with null for JSON compatibility
    cleaned_players = []
    for p in players_data:
        cleaned = {k: (None if isinstance(v, float) and (v != v) else v) for k, v in p.items()}
        cleaned_players.append(cleaned)
    return jsonify({'game_id': game_id, 'players': cleaned_players})


@app.route('/api/scoreboard')
def scoreboard():
    """Get today's games - uses ESPN public API (stats.nba.com blocks cloud IPs)"""
    date_str = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    # ESPN expects YYYYMMDD format (no dashes)
    espn_date = date_str.replace('-', '')

    # stats.nba.com consistently times out on Render/cloud infrastructure.
    # Use ESPN public API as primary source - it's fast, reliable, and no auth needed.
    try:
        import requests
        espn_url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={espn_date}"
        resp = requests.get(espn_url, headers=ESPN_HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        # Transform ESPN structure to our expected format
        games = []
        for event in data.get('events', []):
            competition = event['competitions'][0]
            home = competition['competitors'][0]
            away = competition['competitors'][1]
            games.append({
                'gameId': event.get('id'),
                'nbaGameId': espn_to_nba_id(event.get('id', '')),
                'home': {
                    'teamId': home.get('id'),
                    'teamTricode': home['team']['abbreviation'],
                    'teamCity': home['team'].get('location', ''),
                    'teamName': home['team'].get('name', ''),
                    'score': int(home.get('score', 0)),
                    'wins': int(home.get('wins', 0)),
                    'losses': int(home.get('losses', 0)),
                },
                'away': {
                    'teamId': away.get('id'),
                    'teamTricode': away['team']['abbreviation'],
                    'teamCity': away['team'].get('location', ''),
                    'teamName': away['team'].get('name', ''),
                    'score': int(away.get('score', 0)),
                    'wins': int(away.get('wins', 0)),
                    'losses': int(away.get('losses', 0)),
                },
                'gameStatus': event['status']['type']['description'],
                'gameStatusText': event['status']['type'].get('detail', ''),
                'gameEt': event['date'],
                'period': event['status'].get('period', 0),
                'gameCode': event.get('shortName', ''),
            })
        return jsonify({'date': date_str, 'games': games, 'source': 'espn'})
    except Exception as e:
        print(f"Error fetching scoreboard from ESPN: {e}")
        return jsonify({
            'error': 'Failed to fetch scoreboard from ESPN',
            'espn_error': str(e),
            'date': date_str,
            'games': []
        })


@app.route('/api/start-sit', methods=['POST'])
def start_sit():
    """
    Who Should I Start? Optimizer
    Compares two players with context:
    - Recent 7-day performance
    - Injury context (teammates out -> usage boost)
    - Matchup (opponent pace/def rating)
    """
    data = request.get_json()
    player_a_name = data.get('player_a', '').strip()
    player_b_name = data.get('player_b', '').strip()

    if not player_a_name or not player_b_name:
        return jsonify({'error': 'Both player names required'}), 400

    # Clear cached ESPN responses so a transient 403/rate-limit that produced
    # an empty result doesn't poison the lookup for the rest of the session.
    _clear_espn_caches()

    pid_a = get_player_id(player_a_name)
    pid_b = get_player_id(player_b_name)

    if not pid_a or not pid_b:
        return jsonify({'error': 'One or both players not found'}), 404

    def analyze_player(pid, name):
        gl = get_player_gamelog(pid, n_games=15)
        if gl.empty:
            return {'name': name, 'error': 'No game log'}

        # Ensure we have the most recent games by date (descending)
        if 'GAME_DATE' in gl.columns:
            gl = gl.sort_values('GAME_DATE', ascending=False)
        # Last 7 games average
        recent = gl.head(7)
        avg_fps = sum(calc_fps(r) for _, r in recent.iterrows()) / len(recent) if len(recent) else 0
        avg_usage = sum(calc_usage_pct(r) for _, r in recent.iterrows()) / len(recent) if len(recent) else 0

        # Get team for matchup
        team_abbr = gl.iloc[0].get('TEAM_ABBREVIATION', '') if len(gl) else ''
        team_id = get_team_id(team_abbr) if team_abbr else None

        # Get NEXT upcoming matchup from team schedule analysis
        next_matchup = ''
        opp_abbr = ''
        opp_id = None
        if team_id:
            schedule = get_team_schedule_analysis(team_id, days_ahead=7)
            if schedule.get('games'):
                next_game = schedule['games'][0]  # First upcoming game
                next_matchup = next_game.get('matchup', '')
                # Parse opponent from matchup: "AWAY @ HOME"
                if '@' in next_matchup:
                    away, home = next_matchup.split('@')
                    away = away.strip()
                    home = home.strip()
                    opp_abbr = home if away == team_abbr else away
                opp_id = get_team_id(opp_abbr) if opp_abbr else None

        # Fallback: use most recent game's opponent if no upcoming games
        if not opp_abbr:
            matchup_str = gl.iloc[0].get('MATCHUP', '') if len(gl) else ''
            if matchup_str:
                # Matchup format: "TEAM vs OPP" or "TEAM @ OPP"
                parts = matchup_str.replace('@', ' ').replace('vs', ' ').split()
                if parts:
                    opp_abbr = parts[-1]
                    opp_id = get_team_id(opp_abbr)

        pace_def = get_team_pace_defense(opp_id) if opp_id else {'PACE': 98.0, 'DEF_RATING': 112.0}
        opp_pace = float(pace_def.get('PACE', 98.0) or 98.0)
        opp_def = float(pace_def.get('DEF_RATING', 112.0) or 112.0)
        if opp_def <= 0:
            opp_def = 112.0  # guard against division by zero / bad data

        # Pace adjustment: higher pace = more possessions = more fantasy points
        pace_factor = opp_pace / 98.0  # 98 = league avg pace
        def_factor = 112.0 / opp_def  # lower def rating = better for offense

        # Combined adjustment (cap at +/-15%)
        matchup_mult = min(1.15, max(0.85, pace_factor * def_factor))
        proj_fps = avg_fps * matchup_mult

        return {
            'name': name,
            'player_id': pid,
            'team': team_abbr,
            'opponent': opp_abbr,
            'recent_games': len(recent),
            'avg_fps_7': round(avg_fps, 1),
            'avg_usage_7': round(avg_usage, 1),
            'next_matchup': next_matchup if next_matchup else (gl.iloc[0].get('MATCHUP', '') if len(gl) else ''),
            'opp_pace': round(pace_def['PACE'], 1),
            'opp_def_rating': round(pace_def['DEF_RATING'], 1),
            'pace_factor': round(pace_factor, 3),
            'def_factor': round(def_factor, 3),
            'matchup_multiplier': round(matchup_mult, 3),
            'projected_fps': round(proj_fps, 1),
        }

    result_a = analyze_player(pid_a, player_a_name)
    result_b = analyze_player(pid_b, player_b_name)

    # Determine winner
    winner = 'A' if result_a.get('projected_fps', 0) > result_b.get('projected_fps', 0) else 'B'
    confidence = abs(result_a.get('projected_fps', 0) - result_b.get('projected_fps', 0))

    return jsonify({
        'player_a': result_a,
        'player_b': result_b,
        'recommendation': winner,
        'confidence': round(confidence, 1),
        'reasoning': f"{result_a['name']} projected {result_a.get('projected_fps', 0)} FPS vs {result_b['name']} {result_b.get('projected_fps', 0)} FPS"
    })


@app.route('/api/trade-analyzer', methods=['POST'])
def trade_analyzer():
    """
    Trade Analyzer using VORP / FPS projections
    Compares two rosters side by side
    """
    data = request.get_json()
    roster_a = data.get('roster_a', [])  # list of player names
    roster_b = data.get('roster_b', [])

    if not roster_a or not roster_b:
        return jsonify({'error': 'Both rosters required'}), 400

    def analyze_roster(roster_names):
        players_data = []
        total_fps = 0
        for name in roster_names:
            pid = get_player_id(name.strip())
            if not pid:
                players_data.append({'name': name, 'error': 'Not found'})
                continue
            gl = get_player_gamelog(pid, n_games=10)
            if gl.empty:
                players_data.append({'name': name, 'error': 'No data'})
                continue
            # Ensure sorting by date
            if 'GAME_DATE' in gl.columns:
                gl = gl.sort_values('GAME_DATE', ascending=False)
            recent = gl.head(7)
            avg_fps = sum(calc_fps(r) for _, r in recent.iterrows()) / len(recent) if len(recent) else 0
            players_data.append({
                'name': name,
                'player_id': pid,
                'avg_fps': round(avg_fps, 1),
                'games': len(recent),
            })
            total_fps += avg_fps
        return {'players': players_data, 'total_fps': round(total_fps, 1), 'count': len([p for p in players_data if 'error' not in p])}

    result_a = analyze_roster(roster_a)
    result_b = analyze_roster(roster_b)

    diff = result_a['total_fps'] - result_b['total_fps']
    winner = 'A' if diff > 0 else 'B'

    return jsonify({
        'roster_a': result_a,
        'roster_b': result_b,
        'difference': round(diff, 1),
        'winner': winner,
        'summary': f"Roster {winner} wins by {abs(diff):.1f} FPS/game"
    })


@app.route('/api/ros-heatmap')
def ros_heatmap():
    """
    Rest of Season Heatmap
    Shows team game density for upcoming weeks
    """
    weeks = request.args.get('weeks', 4, type=int)
    all_teams = get_all_teams()

    today = datetime.now().date()
    heatmap = []

    for team in all_teams:
        tid = team['id']
        sched = get_team_schedule_analysis(tid, days_ahead=weeks*7)

        # Weekly breakdown
        weekly = []
        for w in range(weeks):
            week_start = today + timedelta(days=w*7)
            week_end = week_start + timedelta(days=6)
            week_games = [g for g in sched['games']
                          if week_start <= parse_date(g['date']) <= week_end]
            weekly.append({
                'week': w + 1,
                'start': str(week_start),
                'end': str(week_end),
                'games': len(week_games),
                'b2b': sum(1 for i in range(1, len(week_games))
                       if (parse_date(week_games[i]['date']) - parse_date(week_games[i-1]['date'])).days <= 1)
            })

        heatmap.append({
            'team_id': tid,
            'abbreviation': team['abbreviation'],
            'name': team['full_name'],
            'total_games': sched['games_count'],
            'b2b_count': sched['b2b_count'],
            'weekly': weekly,
        })

    # Sort by total games descending
    heatmap.sort(key=lambda x: -x['total_games'])
    return jsonify({'weeks': weeks, 'teams': heatmap})


@lru_cache(maxsize=32)
def get_weekly_schedule_analyzer(week: int, season: str = '2026-27') -> Dict[str, Any]:
    """
    Get comprehensive weekly schedule data for all teams.
    Returns team-by-team schedule grid with opponent stats (off rating, def rating, pace).
    Week 1 = first week of regular season (typically mid-October)
    """
    try:
        # Get all team game logs for the season
        all_teams_list = get_all_teams()

        # Get team advanced stats for off/def ratings and pace
        adv_stats_df = get_league_team_stats('Advanced', season)
        if adv_stats_df.empty:
            # Use fallback/mock data if API fails
            return generate_mock_weekly_schedule(week, season, all_teams_list)

        # Create lookup for team stats
        team_stats = {}
        for _, row in adv_stats_df.iterrows():
            team_stats[row['TEAM_ID']] = {
                'OFF_RATING': float(row.get('OFF_RATING', 112)),
                'DEF_RATING': float(row.get('DEF_RATING', 112)),
                'PACE': float(row.get('PACE', 98)),
            }

        # Determine the date range for the given week
        # Yahoo-style: Week 1 starts Oct 20 and runs to the following Sunday;
        # all subsequent weeks run Mon-Sun
        try:
            start_year = int(season.split('-')[0])
        except (ValueError, IndexError):
            start_year = 2024
        season_start = datetime(start_year, 10, 20).date()

        if week == 1:
            week_start = season_start
            # End of week 1 = the Sunday after Oct 20
            week_end = week_start + timedelta(days=(6 - week_start.weekday()))
        else:
            # Week 1 end was a Sunday; week 2 starts the next Monday
            w1_end = season_start + timedelta(days=(6 - season_start.weekday()))
            week_start = w1_end + timedelta(days=1) + timedelta(days=(week - 2) * 7)
            week_end = week_start + timedelta(days=6)

        # Day labels
        day_labels = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

        # Fetch full season schedule (one call, much faster than per-team calls)
        # ScheduleLeagueV2 returns one row per game with home/away team info
        schedule_df = ScheduleLeagueV2(league_id='00', season=season).get_data_frames()[0]
        # Normalize date format
        schedule_df['GAME_DATE'] = pd.to_datetime(schedule_df['gameDate']).dt.date
        # Filter to our week
        week_schedule = schedule_df[
            (schedule_df['GAME_DATE'] >= week_start) &
            (schedule_df['GAME_DATE'] <= week_end)
        ].copy()

        teams_data = []

        for team in all_teams_list:
            tid = team['id']
            abbr = team['abbreviation']

            # Get this team's games for the week (home or away)
            home_games = week_schedule[week_schedule['homeTeam_teamId'] == tid]
            away_games = week_schedule[week_schedule['awayTeam_teamId'] == tid]

            # Filter games for this week and ensure no duplicate game dates per team
            week_games = []
            seen_dates = set()

            # Process home games
            for _, row in home_games.iterrows():
                game_date = row['GAME_DATE']
                if game_date in seen_dates:
                    continue
                seen_dates.add(game_date)

                opp_tricode = row['awayTeam_teamTricode']
                opp_id = get_team_id(opp_tricode)
                opp_stats = team_stats.get(opp_id, {'OFF_RATING': 112, 'DEF_RATING': 112, 'PACE': 98})

                week_games.append({
                    'date': game_date,
                    'day': day_labels[game_date.weekday()],
                    'matchup': f"{abbr} vs. {opp_tricode}",
                    'is_home': True,
                    'opp_tricode': opp_tricode,
                    'opp_id': opp_id,
                    'opp_off_rating': opp_stats['OFF_RATING'],
                    'opp_def_rating': opp_stats['DEF_RATING'],
                    'opp_pace': opp_stats['PACE'],
                    'wl': '',
                })

            # Process away games
            for _, row in away_games.iterrows():
                game_date = row['GAME_DATE']
                if game_date in seen_dates:
                    continue
                seen_dates.add(game_date)

                opp_tricode = row['homeTeam_teamTricode']
                opp_id = get_team_id(opp_tricode)
                opp_stats = team_stats.get(opp_id, {'OFF_RATING': 112, 'DEF_RATING': 112, 'PACE': 98})

                week_games.append({
                    'date': game_date,
                    'day': day_labels[game_date.weekday()],
                    'matchup': f"{abbr} @ {opp_tricode}",
                    'is_home': False,
                    'opp_tricode': opp_tricode,
                    'opp_id': opp_id,
                    'opp_off_rating': opp_stats['OFF_RATING'],
                    'opp_def_rating': opp_stats['DEF_RATING'],
                    'opp_pace': opp_stats['PACE'],
                    'wl': '',
                })

            # Count games and B2B (before converting dates to strings)
            b2b_count = 0
            sorted_games = sorted(week_games, key=lambda x: x['date'])
            for i in range(1, len(sorted_games)):
                if (sorted_games[i]['date'] - sorted_games[i-1]['date']).days <= 1:
                    b2b_count += 1

            # Build day-by-day grid
            day_map = {day: [] for day in day_labels}
            for g in week_games:
                day_map[g['day']].append(g)

            # Convert date objects to ISO strings for JSON output
            for day_games in day_map.values():
                for g in day_games:
                    if isinstance(g['date'], (datetime, date)):
                        g['date'] = g['date'].isoformat()

            teams_data.append({
                'team_id': tid,
                'abbreviation': team['abbreviation'],
                'name': team['full_name'],
                'games_count': len(week_games),
                'b2b_count': b2b_count,
                'days': day_map,
            })

        # Sort by games count descending
        teams_data.sort(key=lambda x: -x['games_count'])

        return {
            'week': week,
            'week_start': str(week_start),
            'week_end': str(week_end),
            'days': day_labels,
            'teams': teams_data,
        }
    except Exception as e:
        print(f"Error in weekly schedule analyzer: {e}")
        # Return mock data as fallback
        return generate_mock_weekly_schedule(week, season, get_all_teams())


def generate_mock_weekly_schedule(week: int, season: str, all_teams_list: List[Dict]) -> Dict[str, Any]:
    """Generate mock schedule data for testing when NBA API is unavailable"""
    import random

    try:
        start_year = int(season.split('-')[0])
    except (ValueError, IndexError):
        start_year = 2024
    season_start = datetime(start_year, 10, 20).date()

    if week == 1:
        week_start = season_start
        week_end = week_start + timedelta(days=(6 - week_start.weekday()))
    else:
        w1_end = season_start + timedelta(days=(6 - season_start.weekday()))
        week_start = w1_end + timedelta(days=1) + timedelta(days=(week - 2) * 7)
        week_end = week_start + timedelta(days=6)

    day_labels = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

    # Mock opponent stats
    mock_opp_stats = {
        'ATL': {'OFF_RATING': 114.2, 'DEF_RATING': 116.8, 'PACE': 99.5},
        'BOS': {'OFF_RATING': 119.5, 'DEF_RATING': 110.2, 'PACE': 98.3},
        'CLE': {'OFF_RATING': 117.8, 'DEF_RATING': 111.5, 'PACE': 97.2},
        'NOP': {'OFF_RATING': 113.5, 'DEF_RATING': 115.2, 'PACE': 100.1},
        'CHI': {'OFF_RATING': 112.8, 'DEF_RATING': 114.5, 'PACE': 99.8},
        'DAL': {'OFF_RATING': 116.2, 'DEF_RATING': 113.8, 'PACE': 99.2},
        'DEN': {'OFF_RATING': 118.5, 'DEF_RATING': 112.5, 'PACE': 97.5},
        'GSW': {'OFF_RATING': 115.8, 'DEF_RATING': 113.2, 'PACE': 101.2},
        'HOU': {'OFF_RATING': 111.5, 'DEF_RATING': 110.8, 'PACE': 98.8},
        'LAC': {'OFF_RATING': 114.8, 'DEF_RATING': 112.8, 'PACE': 98.5},
        'LAL': {'OFF_RATING': 115.5, 'DEF_RATING': 114.2, 'PACE': 99.8},
        'MEM': {'OFF_RATING': 113.2, 'DEF_RATING': 111.8, 'PACE': 100.5},
        'MIA': {'OFF_RATING': 112.2, 'DEF_RATING': 111.2, 'PACE': 97.8},
        'MIL': {'OFF_RATING': 117.2, 'DEF_RATING': 113.5, 'PACE': 98.2},
        'MIN': {'OFF_RATING': 113.8, 'DEF_RATING': 109.8, 'PACE': 98.0},
        'BKN': {'OFF_RATING': 112.5, 'DEF_RATING': 115.5, 'PACE': 100.2},
        'NYK': {'OFF_RATING': 115.2, 'DEF_RATING': 112.2, 'PACE': 97.5},
        'OKC': {'OFF_RATING': 116.8, 'DEF_RATING': 111.2, 'PACE': 100.8},
        'ORL': {'OFF_RATING': 111.8, 'DEF_RATING': 109.5, 'PACE': 96.8},
        'PHI': {'OFF_RATING': 114.5, 'DEF_RATING': 113.8, 'PACE': 98.8},
        'PHX': {'OFF_RATING': 117.5, 'DEF_RATING': 115.8, 'PACE': 101.5},
        'POR': {'OFF_RATING': 111.2, 'DEF_RATING': 116.2, 'PACE': 99.2},
        'SAC': {'OFF_RATING': 115.8, 'DEF_RATING': 114.8, 'PACE': 100.8},
        'SAS': {'OFF_RATING': 110.8, 'DEF_RATING': 116.5, 'PACE': 99.5},
        'TOR': {'OFF_RATING': 112.8, 'DEF_RATING': 114.2, 'PACE': 98.2},
        'UTA': {'OFF_RATING': 112.2, 'DEF_RATING': 115.8, 'PACE': 99.0},
        'WAS': {'OFF_RATING': 110.5, 'DEF_RATING': 117.2, 'PACE': 100.5},
        'DET': {'OFF_RATING': 111.5, 'DEF_RATING': 114.8, 'PACE': 98.8},
        'IND': {'OFF_RATING': 116.5, 'DEF_RATING': 114.2, 'PACE': 99.2},
        'CHA': {'OFF_RATING': 109.8, 'DEF_RATING': 117.5, 'PACE': 100.2},
    }

    teams_data = []

    for team in all_teams_list:
        tid = team['id']
        abbr = team['abbreviation']

        # Generate 2-4 games per team for the week
        num_games = random.randint(2, 4)
        game_days = random.sample(day_labels, num_games)

        week_games = []
        b2b_count = 0
        prev_date = None

        for day in sorted(game_days, key=lambda d: day_labels.index(d)):
            day_idx = day_labels.index(day)
            game_date = week_start + timedelta(days=day_idx)

            # Pick a random opponent
            opp_abbr = random.choice([t['abbreviation'] for t in all_teams_list if t['abbreviation'] != abbr])
            is_home = random.choice([True, False])
            opp_stats = mock_opp_stats.get(opp_abbr, {'OFF_RATING': 112, 'DEF_RATING': 112, 'PACE': 98})

            matchup = f"{abbr} {'vs.' if is_home else '@'} {opp_abbr}"

            week_games.append({
                'date': game_date,
                'day': day,
                'matchup': matchup,
                'is_home': is_home,
                'opp_tricode': opp_abbr,
                'opp_id': get_team_id(opp_abbr),
                'opp_off_rating': opp_stats['OFF_RATING'],
                'opp_def_rating': opp_stats['DEF_RATING'],
                'opp_pace': opp_stats['PACE'],
                'wl': '',
            })

            if prev_date and (game_date - prev_date).days <= 1:
                b2b_count += 1
            prev_date = game_date

        day_map = {day: [] for day in day_labels}
        for g in week_games:
            day_map[g['day']].append(g)

        # Convert date objects to ISO strings for JSON output
        for day_games in day_map.values():
            for g in day_games:
                if isinstance(g['date'], (datetime, date)):
                    g['date'] = g['date'].isoformat()

        teams_data.append({
            'team_id': tid,
            'abbreviation': abbr,
            'name': team['full_name'],
            'games_count': len(week_games),
            'b2b_count': b2b_count,
            'days': day_map,
        })

    teams_data.sort(key=lambda x: -x['games_count'])

    return {
        'week': week,
        'week_start': str(week_start),
        'week_end': str(week_end),
        'days': day_labels,
        'teams': teams_data,
    }


@app.route('/api/weekly-schedule')
def weekly_schedule():
    """
    Weekly Schedule Analyzer
    Returns team-by-team weekly grid with opponent stats
    """
    week = request.args.get('week', 1, type=int)
    season = request.args.get('season', '2026-27')
    days_filter = request.args.get('days', '')  # comma-separated day abbreviations

    data = get_weekly_schedule_analyzer(week, season)

    # Filter by days if provided
    if days_filter:
        selected_days = [d.strip() for d in days_filter.split(',')]
        for team in data['teams']:
            filtered_days = {day: team['days'][day] for day in selected_days if day in team['days']}
            team['days'] = filtered_days
            # Recalculate games count for filtered days
            team['games_count'] = sum(len(games) for games in filtered_days.values())
        # Re-sort by filtered games count
        data['teams'].sort(key=lambda x: -x['games_count'])

    return jsonify(data)


@app.route('/api/waiver-streamers')
def waiver_streamers():
    """
    Waiver Wire Streamer Finder
    Finds available players on teams with high game density
    """
    days = request.args.get('days', 7, type=int)
    min_games = request.args.get('min_games', 3, type=int)

    all_teams = get_all_teams()
    streamers = []

    for team in all_teams:
        sched = get_team_schedule_analysis(team['id'], days_ahead=days)
        if sched['games_count'] >= min_games:
            # Get players on this team (simplified - would need roster endpoint)
            # For now, return team schedule info
            streamers.append({
                'team': team['abbreviation'],
                'team_name': team['full_name'],
                'games': sched['games_count'],
                'b2b': sched['b2b_count'],
                'avg_rest': round(sched['avg_rest'], 1),
                'schedule': sched['games'],
            })

    streamers.sort(key=lambda x: (-x['games'], x['b2b']))
    return jsonify({'days': days, 'min_games': min_games, 'teams': streamers})


@app.route('/api/win-projector', methods=['POST'])
def win_projector():
    """
    Head-to-Head Win Projector
    Calculates win probability using normal distribution model
    """
    data = request.get_json()

    # My team
    my_cur = data.get('my_cur', 0)
    my_proj = data.get('my_proj', 0)
    my_played = data.get('my_played', 0)
    my_total = data.get('my_total', 0)

    # Opponent
    opp_cur = data.get('opp_cur', 0)
    opp_proj = data.get('opp_proj', 0)
    opp_played = data.get('opp_played', 0)
    opp_total = data.get('opp_total', 0)

    # Settings
    sd = data.get('sd', 9.5)
    mode = data.get('mode', 'rate')  # 'rate' or 'live'

    my_rem = max(0, my_total - my_played)
    opp_rem = max(0, opp_total - opp_played)

    if mode == 'rate':
        my_rem_pts = (my_proj / my_total * my_rem) if my_total > 0 else 0
        opp_rem_pts = (opp_proj / opp_total * opp_rem) if opp_total > 0 else 0
    else:
        my_rem_pts = max(0, my_proj - my_cur)
        opp_rem_pts = max(0, opp_proj - opp_cur)

    my_final = my_cur + my_rem_pts
    opp_final = opp_cur + opp_rem_pts

    mean = my_final - opp_final
    games_rem = my_rem + opp_rem
    sd_total = math.sqrt(games_rem) * sd

    # Normal CDF
    def norm_cdf(z):
        return 0.5 * (1 + math.erf(z / math.sqrt(2)))

    if sd_total == 0:
        p = 1.0 if mean > 0 else (0.0 if mean < 0 else 0.5)
    else:
        p = norm_cdf(mean / sd_total)

    need = max(0, opp_final - my_cur)
    need_per_game = need / my_rem if my_rem > 0 else 0
    proj_per_game = my_rem_pts / my_rem if my_rem > 0 else 0

    return jsonify({
        'win_probability': round(p * 100, 1),
        'my_final': round(my_final, 1),
        'opp_final': round(opp_final, 1),
        'margin': round(mean, 1),
        'sd': round(sd_total, 1),
        'range_low': round(mean - sd_total, 1),
        'range_high': round(mean + sd_total, 1),
        'need': round(need, 1),
        'need_per_game': round(need_per_game, 1),
        'proj_per_game': round(proj_per_game, 1),
        'my_rem': my_rem,
        'opp_rem': opp_rem,
    })


@app.route('/api/player/<int:player_id>/minutes-projection')
def player_minutes_projection(player_id):
    """
    Minutes Slider / Per-36 Projection

    Returns the player's season per-game averages and per-36-minute rates so the
    frontend can project fantasy points at any minute level.

    Projection math (linear per-36 scaling):
        projected_stat(M) = per36_stat * (M / 36)
        projected_fps(M) = sum(projected_stat(M) * SCORING[stat])
    """
    df = get_player_gamelog(player_id, n_games=30)
    if df.empty:
        return jsonify({'error': 'No data'}), 404

    def _avg(*cols):
        for c in cols:
            if c in df.columns:
                vals = pd.to_numeric(df[c], errors='coerce').dropna()
                if len(vals):
                    return round(float(vals.mean()), 3)
        return 0.0

    avg_min = _avg('MIN', 'minutes', 'MP')
    avg_pts = _avg('PTS', 'points')
    avg_reb = _avg('REB', 'rebounds')
    avg_ast = _avg('AST', 'assists')
    avg_stl = _avg('STL', 'steals')
    avg_blk = _avg('BLK', 'blocks')
    avg_tov = _avg('TOV', 'turnovers')

    # Per-36-minute rates (guard against zero minutes)
    m = avg_min if avg_min and avg_min > 0 else 36.0
    scale = 36.0 / m

    def per36(v):
        return round(v * scale, 3)

    per36 = {
        'pts': per36(avg_pts),
        'reb': per36(avg_reb),
        'ast': per36(avg_ast),
        'stl': per36(avg_stl),
        'blk': per36(avg_blk),
        'tov': per36(avg_tov),
    }

    # Baseline FPS at the player's actual average minutes
    baseline_fps = round(
        SCORING['PTS'] * avg_pts +
        SCORING['REB'] * avg_reb +
        SCORING['AST'] * avg_ast +
        SCORING['STL'] * avg_stl +
        SCORING['BLK'] * avg_blk +
        SCORING['TOV'] * avg_tov, 1)

    return jsonify({
        'player_id': player_id,
        'games': len(df),
        'avg_min': round(avg_min, 1),
        'avg': {
            'pts': round(avg_pts, 1), 'reb': round(avg_reb, 1),
            'ast': round(avg_ast, 1), 'stl': round(avg_stl, 1),
            'blk': round(avg_blk, 1), 'tov': round(avg_tov, 1),
        },
        'per36': per36,
        'baseline_fps': baseline_fps,
        'scoring': SCORING,
    })


@app.route('/api/season-outlook', methods=['POST'])
def season_outlook():
    """Season outlook using binomial distribution"""
    data = request.get_json()
    W = data.get('W', 0)
    L = data.get('L', 0)
    T = data.get('T', 0)
    left = data.get('left', 0)
    avg_p = data.get('avg_p', 50) / 100
    target = data.get('target', 0)
    p_this = data.get('p_this', 0.5)

    probs = [p_this] + [avg_p] * (left - 1)

    # Binomial distribution
    dist = [1.0]
    for prob in probs:
        new_dist = [0.0] * (len(dist) + 1)
        for k, val in enumerate(dist):
            new_dist[k] += val * (1 - prob)
            new_dist[k + 1] += val * prob
        dist = new_dist

    exp_wins = W + 0.5 * T + sum(probs)
    exp_losses = L + 0.5 * T + left - sum(probs)
    total_games = W + L + T + left
    win_pct = exp_wins / total_games

    wins_needed = max(0, target - W)
    p_target = sum(dist[wins_needed:]) if wins_needed < len(dist) else 0

    return jsonify({
        'exp_wins': round(exp_wins, 1),
        'exp_losses': round(exp_losses, 1),
        'win_pct': round(win_pct * 100, 1),
        'p_target': round(p_target * 100, 1),
        'distribution': [round(d * 100, 1) for d in dist],
        'wins_needed': wins_needed,
    })


# =====================================================================
# Main
# =====================================================================
if __name__ == '__main__':
    # Pre-warm rankings cache in the background so the first request is fast
    threading.Thread(target=warm_rankings, args=('season', '2026-27'), daemon=True).start()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)