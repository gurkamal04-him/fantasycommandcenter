import sys
sys.path.insert(0, '.')
from app import app

with app.test_client() as client:
    # Test different periods
    periods = ['1week', '2weeks', 'season']
    for period in periods:
        resp = client.get(f'/api/buy-low-sell-high?period={period}&season=2026-27&min_games=3')
        data = resp.get_json()
        print(f'{period}: Sell={len(data.get("sell_high", []))}, Buy={len(data.get("buy_low", []))}, Warming={data.get("warming")}')

    # Test with team filter
    resp = client.get('/api/buy-low-sell-high?period=1month&season=2026-27&min_games=3&team=NYK')
    data = resp.get_json()
    print(f'\nTeam NYK: Sell={len(data.get("sell_high", []))}, Buy={len(data.get("buy_low", []))}')
    if data.get('sell_high'):
        for p in data['sell_high'][:5]:
            print(f'  {p["name"]} ({p["team"]}): diff={p["diff"]}, diff_pct={p["diff_pct"]}%')

    # Test custom range
    resp = client.get('/api/buy-low-sell-high?period=custom&season=2026-27&min_games=3&start_date=2026-10-01&end_date=2026-11-01')
    data = resp.get_json()
    print(f'\nCustom range: Sell={len(data.get("sell_high", []))}, Buy={len(data.get("buy_low", []))}, Warming={data.get("warming")}')