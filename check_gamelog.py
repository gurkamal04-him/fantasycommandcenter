import sys
sys.path.insert(0, '.')
from app import app, get_player_gamelog

with app.test_client() as client:
    # Check gamelog directly
    gamelog = get_player_gamelog(4278562, '2026-27')
    print('Gamelog count:', len(gamelog))
    for g in gamelog[:5]:
        print(g)
    print('...')
    for g in gamelog[-5:]:
        print(g)

    # Check player page endpoint
    resp = client.get('/api/player/4278562?season=2026-27')
    data = resp.get_json()
    print('\nPlayer page games_played:', data.get('games_played'))
    print('Player page gamelog count:', len(data.get('gamelog', [])))