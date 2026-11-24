import sys
import time
sys.path.insert(0, '.')
from app import app, get_player_rankings

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

    # Call get_player_rankings directly (bypassing lru_cache by calling the wrapped function)
    from app import get_player_rankings
    # Clear the cache
    get_player_rankings.cache_clear()

    rankings = get_player_rankings('season', '2026-27', '', '')
    for p in rankings:
        if 'Brunson' in p['name']:
            print('Brunson (fresh call):', p)
            break

    # Also check the endpoint
    resp = client.get('/api/player-rankings?period=season&season=2026-27')
    data = resp.get_json()
    for p in data.get('players', []):
        if 'Brunson' in p['name']:
            print('Brunson (endpoint):', p)
            break