import sys
import time
sys.path.insert(0, '.')
from app import app, get_espn_team_schedule, get_espn_all_teams_schedule, get_all_players

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

    # Check NYK team schedule
    all_players = get_all_players()
    player = next(p for p in all_players if p.get('PERSON_ID') == 4278562)
    team_id = player.get('TEAM_ID')
    print(f'Brunson team_id: {team_id}')

    # Team-specific schedule
    schedule = get_espn_team_schedule(team_id, '2026-27')
    print(f'NYK schedule shape: {schedule.shape}')
    completed = schedule[schedule['status'].str.contains('FINAL', na=False, case=False)]
    print(f'NYK completed games: {len(completed)}')
    for _, row in completed.iterrows():
        print(f"  {row.get('GAME_DATE')} {row.get('homeTeam_teamTricode')} vs {row.get('awayTeam_teamTricode')} - {row.get('status')}")

    # All teams schedule
    all_schedule = get_espn_all_teams_schedule('2026-27')
    print(f'\nAll teams schedule shape: {all_schedule.shape}')
    all_completed = all_schedule[all_schedule['status'].str.contains('FINAL', na=False, case=False)]
    print(f'All teams completed games: {len(all_completed)}')

    # Check how many NYK games in all schedule
    nyk_games = all_completed[
        (all_completed['homeTeam_teamTricode'] == 'NYK') |
        (all_completed['awayTeam_teamTricode'] == 'NYK')
    ]
    print(f'NYK games in all schedule: {len(nyk_games)}')
    for _, row in nyk_games.iterrows():
        print(f"  {row.get('GAME_DATE')} {row.get('awayTeam_teamTricode')} @ {row.get('homeTeam_teamTricode')} - {row.get('status')}")