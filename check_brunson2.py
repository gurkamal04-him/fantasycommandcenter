import sys
sys.path.insert(0, '.')
from app import app

with app.test_client() as client:
    # Check Brunson in rankings
    resp = client.get('/api/player-rankings?period=season&season=2026-27')
    data = resp.get_json()
    for p in data.get('players', []):
        if 'Brunson' in p['name']:
            print('Rankings:', p)
            break