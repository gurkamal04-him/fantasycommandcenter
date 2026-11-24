import sys
import time
sys.path.insert(0, '.')
from app import app, get_all_players, get_espn_team_schedule

with app.test_client() as client:
    for i in range(60):
        resp = client.get('/api/ready')
        data = resp.get_json()
        if data.get('ready'):
            print('Ready!')
            break
        time.sleep(5)
    else:
        print('Timed out')
        sys.exit(1)

    # Replicate rankings logic for NYK
    all_players = get_all_players()
    team_schedules = {}
    for p in all_players:
        team_id = p.get('TEAM_ID')
        if team_id and team_id not in team_schedules:
            team_schedules[team_id] = get_espn_team_schedule(team_id, '2026-27')

    # Collect all unique completed game IDs with their dates
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

    print(f'Total unique completed games: {len(game_dates)}')

    # Check NYK games specifically
    nyk_team_id = 18
    if nyk_team_id in team_schedules:
        team_df = team_schedules[nyk_team_id]
        completed = team_df[team_df['status'].str.contains('FINAL', na=False, case=False)]
        print(f'\nNYK completed games from team_schedules: {len(completed)}')
        for _, row in completed.iterrows():
            gid = str(row.get('gameId') or row.get('event_id') or '')
            gdate = str(row.get('GAME_DATE', ''))[:10]
            print(f'  {gdate} game_id={gid}')