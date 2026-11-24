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
        print(f'Progress: {data.get("progress")}% - {data.get("stage")}')
        time.sleep(5)
    else:
        print('Timed out waiting for ready')
        sys.exit(1)

    # Check season rankings
    resp = client.get('/api/player-rankings?period=season&season=2026-27')
    data = resp.get_json()
    print('Season rankings count:', len(data.get('players', [])))
    if data.get('players'):
        print('First season player:', data['players'][0])
    print()
    # Check 1month rankings
    resp = client.get('/api/player-rankings?period=1month&season=2026-27')
    data = resp.get_json()
    print('1month rankings count:', len(data.get('players', [])))
    if data.get('players'):
        print('First 1month player:', data['players'][0])

    # Test BLSH
    resp = client.get('/api/buy-low-sell-high?period=1month&season=2026-27&min_games=3')
    data = resp.get_json()
    print()
    print('BLSH Sell high count:', len(data.get('sell_high', [])))
    print('BLSH Buy low count:', len(data.get('buy_low', [])))
    if data.get('sell_high'):
        print('First sell high:', data['sell_high'][0])
    if data.get('buy_low'):
        print('First buy low:', data['buy_low'][0])
    if data.get('error'):
        print('Error:', data['error'])