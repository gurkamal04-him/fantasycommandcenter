import sys
sys.path.insert(0, '.')
from app import app

with app.test_client() as client:
    # Test BLSH endpoint directly
    resp = client.get('/api/buy-low-sell-high?period=1month&season=2026-27&min_games=3')
    data = resp.get_json()
    print('BLSH Sell high count:', len(data.get('sell_high', [])))
    print('BLSH Buy low count:', len(data.get('buy_low', [])))
    if data.get('sell_high'):
        print('First sell high:', data['sell_high'][0])
    if data.get('buy_low'):
        print('First buy low:', data['buy_low'][0])
    if data.get('error'):
        print('Error:', data['error'])
    print('Warming:', data.get('warming'))