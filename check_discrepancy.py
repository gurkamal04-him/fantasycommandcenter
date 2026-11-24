import sys
import time
sys.path.insert(0, '.')
from app import app, get_player_gamelog, get_all_players

with app.test_client() as client:
    # Wait for ready
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

    # Check Brunson in player profile
    resp = client.get('/api/player/4278562?season=2026-27')
    data = resp.get_json()
    print('Player profile games_played:', data.get('games_played'))

    # Check Brunson in rankings
    resp = client.get('/api/player-rankings?period=season&season=2026-27')
    data = resp.get_json()
    for p in data.get('players', []):
        if 'Brunson' in p['name']:
            print('Rankings games:', p['games'])
            break

    # Check gamelog directly with team_id
    all_players = get_all_players()
    player = next(p for p in all_players if p.get('PERSON_ID') == 4278562)
    team_id = player.get('TEAM_ID')
    print(f'Brunson team_id: {team_id}')

    # Check gamelog with team_id (what player profile uses)
    df = get_player_gamelog(4278562, '2026-27', n_games=82, team_id=team_id)
    print(f'Gamelog with team_id: {len(df)} games')

    # Check gamelog without team_id (what rankings might use)
    df2 = get_player_gamelog(4278562, '2026-27', n_games=82, team_id=None)
    print(f'Gamelog without team_id: {len(df2)} games')