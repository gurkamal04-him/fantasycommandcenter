import sys
sys.path.insert(0, '.')
from app import app

with app.test_client() as client:
    resp = client.get('/api/player-rankings?period=season&season=2026-27')
    data = resp.get_json()
    print('Total players:', len(data.get('players', [])))
    for p in data.get('players', []):
        if 'Brunson' in p['name']:
            print('Found Brunson:', p)
            break
    else:
        print('Brunson NOT found in rankings')
        # Print first 10
        for p in data.get('players', [])[:10]:
            print(p)