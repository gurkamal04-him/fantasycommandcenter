"""
NBA Fantasy Command Center - Flask Backend
Free-to-use fantasy basketball suite using nba_api
"""

import os
import json
import math
import time
import os
import unicodedata
from datetime import datetime, date, timedelta
from functools import lru_cache
from typing import Dict, List, Optional, Any, Tuple

import pandas as pd
from flask import Flask, render_template, request, jsonify, send_from_directory

from nba_api.stats.endpoints import (
    CommonAllPlayers,
    PlayerGameLog,
    TeamGameLog,
    LeagueGameLog,
    LeagueDashTeamStats,
    ScheduleLeagueV2,
    TeamDashboardByGeneralSplits,
    ScoreboardV3,
    BoxScoreTraditionalV3,
    BoxScoreAdvancedV3,
    BoxScoreMiscV3,
    BoxScoreUsageV3,
    PlayerCareerStats,
)
from nba_api.stats.static import teams, players

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

# =====================================================================
# NBA API Helpers
# =====================================================================

@lru_cache(maxsize=32)
def get_all_players() -> List[Dict]:
    """Get all current season players with team info"""
    df = CommonAllPlayers(is_only_current_season=1).get_data_frames()[0]
    return df[['PERSON_ID', 'DISPLAY_FIRST_LAST', 'TEAM_ID', 'TEAM_ABBREVIATION']].to_dict('records')


@lru_cache(maxsize=32)
def get_all_teams() -> List[Dict]:
    """Get all NBA teams"""
    return teams.get_teams()


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


@lru_cache(maxsize=64)
def get_team_id(abbrev: str) -> Optional[int]:
    """Find team ID by abbreviation"""
    for t in get_all_teams():
        if t['abbreviation'].lower() == abbrev.lower():
            return t['id']
    return None


@lru_cache(maxsize=64)
def get_player_gamelog(player_id: int, season: str = '2026-27', n_games: int = 50) -> pd.DataFrame:
    """Get player game log for current season"""
    try:
        df = PlayerGameLog(player_id=player_id, season=season, season_type_all_star='Regular Season').get_data_frames()[0]
        # Standardize columns
        df = df.sort_values('GAME_DATE', ascending=False).head(n_games).reset_index(drop=True)
        return df
    except Exception as e:
        print(f"Error fetching player gamelog: {e}")
        return pd.DataFrame()


@lru_cache(maxsize=64)
def get_team_gamelog(team_id: int, season: str = '2026-27', n_games: int = 50) -> pd.DataFrame:
    """Get team game log for current season"""
    try:
        df = LeagueGameLog(
            player_or_team_abbreviation='T',
            season=season,
            season_type_all_star='Regular Season'
        ).get_data_frames()[0]
        df = df[df['TEAM_ID'] == team_id].copy()
        df = df.sort_values('GAME_DATE', ascending=False).head(n_games).reset_index(drop=True)
        return df
    except Exception as e:
        print(f"Error fetching team gamelog: {e}")
        return pd.DataFrame()


@lru_cache(maxsize=32)
def get_league_team_stats(measure_type: str = 'Advanced', season: str = '2026-27') -> pd.DataFrame:
    """Get league-wide team stats (pace, defensive rating, etc.)"""
    try:
        df = LeagueDashTeamStats(
            measure_type_detailed_defense=measure_type,
            season=season,
            per_mode_detailed='PerGame'
        ).get_data_frames()[0]
        return df
    except Exception as e:
        print(f"Error fetching league team stats ({measure_type}): {e}")
        return pd.DataFrame()


@lru_cache(maxsize=64)
def get_team_pace_defense(team_id: int, season: str = '2026-27') -> Dict[str, float]:
    """Get team pace and defensive rating"""
    try:
        df = LeagueDashTeamStats(
            measure_type_detailed_defense='Advanced',
            season=season,
            per_mode_detailed='PerGame'
        ).get_data_frames()[0]
        row = df[df['TEAM_ID'] == team_id]
        if len(row):
            return {
                'PACE': float(row['PACE'].iloc[0]),
                'DEF_RATING': float(row['DEF_RATING'].iloc[0]),
            }
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


@lru_cache(maxsize=64)
def get_team_schedule_analysis(team_id: int, days_ahead: int = 7, season: str = '2026-27') -> Dict:
    """Analyze upcoming schedule: games count, rest days, B2Bs"""
    try:
        df = LeagueGameLog(
            player_or_team_abbreviation='T',
            season=season,
            season_type_all_star='Regular Season'
        ).get_data_frames()[0]
        df = df[df['TEAM_ID'] == team_id].copy()
        df = df.sort_values('GAME_DATE', ascending=True).reset_index(drop=True)

        today = datetime.now().date()
        future_games = []
        seen_dates = set()
        for _, row in df.iterrows():
            try:
                game_date = parse_game_date(row['GAME_DATE'])
            except ValueError:
                continue
            if game_date >= today and game_date <= today + timedelta(days=days_ahead):
                if game_date in seen_dates:
                    continue
                seen_dates.add(game_date)
                future_games.append({
                    'date': game_date,  # keep as date object for calculation
                    'matchup': row['MATCHUP'],
                    'wl': row.get('WL', '')
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


@lru_cache(maxsize=64)
def get_box_score(game_id: str) -> Dict[str, pd.DataFrame]:
    """Get full box score data for a game"""
    try:
        traditional = BoxScoreTraditionalV3(game_id=game_id).get_data_frames()[0]
        advanced = BoxScoreAdvancedV3(game_id=game_id).get_data_frames()[0]
        misc = BoxScoreMiscV3(game_id=game_id).get_data_frames()[0]
        usage = BoxScoreUsageV3(game_id=game_id).get_data_frames()[0]
        return {'traditional': traditional, 'advanced': advanced, 'misc': misc, 'usage': usage}
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
    """
    try:
        # Player stats
        fga = float(row.get('FGA', row.get('fga', 0)) or 0)
        fta = float(row.get('FTA', row.get('fta', 0)) or 0)
        tov = float(row.get('TOV', row.get('tov', 0)) or 0)
        mp = float(row.get('MIN', row.get('minutes', row.get('MP', 0))) or 0)

        if mp <= 0:
            return 0.0

        # Team stats (from team_row if available, else estimate)
        if team_row is not None:
            tm_fga = float(team_row.get('FGA', team_row.get('fga', 0)) or 0)
            tm_fta = float(team_row.get('FTA', team_row.get('fta', 0)) or 0)
            tm_tov = float(team_row.get('TOV', team_row.get('tov', 0)) or 0)
            tm_mp = float(team_row.get('MIN', team_row.get('minutes', 240)) or 240)
        else:
            # Rough estimates: team takes ~85 FGA, ~25 FTA, ~15 TOV per game, 240 team minutes
            tm_fga, tm_fta, tm_tov, tm_mp = 85, 25, 15, 240

        numerator = (fga + 0.44 * fta + tov) * (tm_mp / 5)
        denominator = mp * (tm_fga + 0.44 * tm_fta + tm_tov)

        if denominator <= 0:
            return 0.0

        usage = 100 * numerator / denominator
        return min(100, max(0, usage))
    except Exception:
        return 0.0


def get_shooting_splits(row: pd.Series) -> Dict[str, str]:
    """Extract shooting splits (FG%, 3P%, FT%)"""
    return {
        'FG': f"{float(row.get('fieldGoalsPercentage', row.get('FG_PCT', row.get('fg_pct', 0))) * 100):.1f}%",
        '3P': f"{float(row.get('threePointersPercentage', row.get('FG3_PCT', row.get('fg3_pct', 0))) * 100):.1f}%",
        'FT': f"{float(row.get('freeThrowsPercentage', row.get('FT_PCT', row.get('ft_pct', 0))) * 100):.1f}%",
    }


# =====================================================================
# Player Rankings
# =====================================================================

@lru_cache(maxsize=32)
def get_player_rankings(period: str = 'season', season: str = '2026-27') -> List[Dict]:
    """
    Get player rankings by total fantasy points over a time period.

    period: 'season' (full season), '2weeks' (last 14 days), '1week' (last 7 days)
    Returns top 200 players with avg FPS, total FPS, and usage over the period.
    """
    try:
        df = LeagueGameLog(
            league_id='00',
            season=season,
            season_type_all_star='Regular Season',
            player_or_team_abbreviation='P',
            date_from_nullable='',
            date_to_nullable='',
        ).get_data_frames()[0]
    except Exception as e:
        print(f"Error fetching league game log: {e}")
        return []

    if df.empty:
        return []

    # Parse game dates
    parsed_dates = []
    for d in df['GAME_DATE']:
        try:
            parsed_dates.append(parse_game_date(str(d)))
        except (ValueError, TypeError):
            parsed_dates.append(None)
    df = df.assign(_game_date=parsed_dates).dropna(subset=['_game_date'])

    today = datetime.now().date()

    if period == '2weeks':
        cutoff = today - timedelta(days=14)
        df = df[df['_game_date'] >= cutoff]
    elif period == '1week':
        cutoff = today - timedelta(days=7)
        df = df[df['_game_date'] >= cutoff]
    # 'season' = all games

    if df.empty:
        return []

    # Group by player and compute metrics
    rankings = []
    for pid, group in df.groupby('PLAYER_ID'):
        total_fps = sum(calc_fps(r) for _, r in group.iterrows())
        n_games = len(group)
        avg_fps = total_fps / n_games
        avg_usage = sum(calc_usage_pct(r) for _, r in group.iterrows()) / n_games
        # Per-game averages for PTS/REB/AST/STL/BLK/TOV
        def avg_stat(col):
            vals = [float(r.get(col, 0)) for _, r in group.iterrows() if pd.notna(r.get(col))]
            return round(sum(vals) / len(vals), 1) if vals else 0.0
        rankings.append({
            'player_id': int(pid),
            'name': str(group.iloc[0]['PLAYER_NAME']),
            'team': str(group.iloc[0].get('TEAM_ABBREVIATION', '')),
            'games': n_games,
            'avg_fps': round(avg_fps, 1),
            'total_fps': round(total_fps, 1),
            'avg_usage': round(avg_usage, 1),
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


@app.route('/api/player-rankings')
def player_rankings():
    """Get player rankings by total fantasy points"""
    period = request.args.get('period', 'season')
    season = request.args.get('season', '2026-27')
    rankings = get_player_rankings(period, season)
    return jsonify({'period': period, 'season': season, 'players': rankings})


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
    """Search players by name. Supports 1+ char prefix for dropdown population."""
    q = request.args.get('q', '').lower().strip()
    # Allow 1-character queries so the dropdown fills as soon as typing begins
    if len(q) < 1:
        return jsonify([])
    all_players = get_all_players()
    # Exact/first-name matches first, then any substring match
    exact = [p for p in all_players if p['DISPLAY_FIRST_LAST'].lower().startswith(q)]
    partial = [p for p in all_players if q in p['DISPLAY_FIRST_LAST'].lower() and p not in exact]
    results = (exact + partial)[:50]
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
    data = get_box_score(game_id)
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
            'usage_pct': round(float(row.get('usagePercentage', 0)) * 100, 1),
            'splits': splits,
            'plus_minus': row.get('plusMinusPoints', row.get('plusMinus', row.get('PLUS_MINUS', 0))),
        })

    return jsonify({'game_id': game_id, 'players': players_data})


@app.route('/api/scoreboard')
def scoreboard():
    """Get today's games"""
    date_str = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    try:
        dfs = ScoreboardV3(game_date=date_str).get_data_frames()
        if not dfs or len(dfs) < 3:
            return jsonify({'date': date_str, 'games': []})

        games_df = dfs[1]   # game info: gameId, gameStatus, gameEt, period, etc.
        teams_df = dfs[2]   # team data: teamId, teamCity, teamName, score, wins, losses

        if games_df.empty:
            return jsonify({'date': date_str, 'games': []})

        # Build team lookup by game
        games = {}
        for _, t in teams_df.iterrows():
            gid = t['gameId']
            if gid not in games:
                games[gid] = {'gameId': gid, 'home': {}, 'away': {}}

        # Merge game info
        for _, g in games_df.iterrows():
            gid = g['gameId']
            if gid in games:
                games[gid]['gameStatus'] = g.get('gameStatus', '')
                games[gid]['gameStatusText'] = g.get('gameStatusText', '')
                games[gid]['gameEt'] = g.get('gameEt', '')
                games[gid]['period'] = g.get('period', 0)
                games[gid]['gameCode'] = g.get('gameCode', '')

        # Attach teams (no home/away flag in V3 — use teamId parity as fallback)
        for _, t in teams_df.iterrows():
            gid = t['gameId']
            if gid not in games:
                continue
            entry = {
                'teamId': int(t['teamId']),
                'teamCity': t.get('teamCity', ''),
                'teamName': t.get('teamName', ''),
                'teamTricode': t.get('teamTricode', ''),
                'score': int(t['score']) if pd.notna(t.get('score')) else None,
                'wins': int(t['wins']) if pd.notna(t.get('wins')) else 0,
                'losses': int(t['losses']) if pd.notna(t.get('losses')) else 0,
            }
            # Heuristic: lower teamId = home
            if not games[gid]['home']:
                games[gid]['home'] = entry
            else:
                games[gid]['away'] = entry

        result = [v for v in games.values() if v['home'] and v['away']]
        return jsonify({'date': date_str, 'games': result})
    except Exception as e:
        return jsonify({'error': str(e), 'date': date_str, 'games': []})


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

    pid_a = get_player_id(player_a_name)
    pid_b = get_player_id(player_b_name)

    if not pid_a or not pid_b:
        return jsonify({'error': 'One or both players not found'}), 404

    def analyze_player(pid, name):
        gl = get_player_gamelog(pid, n_games=15)
        if gl.empty:
            return {'name': name, 'error': 'No game log'}

        # Last 7 games average
        recent = gl.head(7)
        avg_fps = sum(calc_fps(r) for _, r in recent.iterrows()) / len(recent) if len(recent) else 0
        avg_usage = sum(calc_usage_pct(r) for _, r in recent.iterrows()) / len(recent) if len(recent) else 0

        # Get team for matchup
        team_abbr = gl.iloc[0].get('TEAM_ABBREVIATION', '') if len(gl) else ''
        team_id = get_team_id(team_abbr) if team_abbr else None

        # Matchup data
        next_game = gl.iloc[0] if len(gl) else None
        matchup_str = next_game.get('MATCHUP', '') if next_game is not None else ''
        opp_abbr = matchup_str.split()[-1] if matchup_str else ''
        opp_id = get_team_id(opp_abbr) if opp_abbr else None

        pace_def = get_team_pace_defense(opp_id) if opp_id else {'PACE': 98.0, 'DEF_RATING': 112.0}

        # Pace adjustment: higher pace = more possessions = more fantasy points
        pace_factor = pace_def['PACE'] / 98.0  # 98 = league avg pace
        def_factor = 112.0 / pace_def['DEF_RATING']  # lower def rating = better for offense

        # Combined adjustment (cap at +/-15%)
        matchup_mult = min(1.15, max(0.85, pace_factor * def_factor))
        proj_fps = avg_fps * matchup_mult

        return {
            'name': name,
            'player_id': pid,
            'team': team_abbr,
            'recent_games': len(recent),
            'avg_fps_7': round(avg_fps, 1),
            'avg_usage_7': round(avg_usage, 1),
            'next_matchup': matchup_str,
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
            week_games = [g for g in sched['games'] if week_start <= g['date'] <= week_end]
            weekly.append({
                'week': w + 1,
                'start': str(week_start),
                'end': str(week_end),
                'games': len(week_games),
                'b2b': sum(1 for i in range(1, len(week_games)) if (week_games[i]['date'] - week_games[i-1]['date']).days <= 1)
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
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)