import sys
sys.path.insert(0, '.')
from app import app
import time

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

    # Check Brunson in rankings
    resp = client.get('/api/player-rankings?period=season&season=2026-27')
    data = resp.get_json()
    print('Total players:', len(data.get('players', [])))
    for p in data.get('players', []):
        if 'Brunson' in p['name']:
            print('Found Brunson:', p)
            break
    else:
        print('Brunson NOT found in rankings')
        for p in data.get('players', [])[:10]:
            print(p)