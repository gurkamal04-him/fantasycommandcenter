import sys
sys.path.insert(0, '.')
from app import app
import time

with app.test_client() as client:
    # Test BLSH endpoint - wait for warming
    for i in range(30):
        resp = client.get('/api/buy-low-sell-high?period=1month&season=2026-27&min_games=3')
        data = resp.get_json()
        print(f'Attempt {i+1}: Warming={data.get("warming")}, Sell={len(data.get("sell_high", []))}, Buy={len(data.get("buy_low", []))}')
        if not data.get('warming') and (data.get('sell_high') or data.get('buy_low')):
            print('Done!')
            if data.get('sell_high'):
                print('First sell:', data['sell_high'][0])
            if data.get('buy_low'):
                print('First buy:', data['buy_low'][0])
            break
        time.sleep(1)