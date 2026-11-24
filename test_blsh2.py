import sys
sys.path.insert(0, '.')
from app import app, get_buy_low_sell_high, get_player_rankings

with app.test_client() as client:
    # Debug BLSH
    period_rankings = get_player_rankings('1month', '2026-27', '', '')
    season_rankings = get_player_rankings('season', '2026-27', '', '')

    print(f"Period rankings: {len(period_rankings)}")
    print(f"Season rankings: {len(season_rankings)}")

    # Build lookup
    season_fps_by_id = {}
    for p in season_rankings:
        pid = p.get('player_id')
        if pid:
            season_fps_by_id[pid] = p.get('avg_fps', 0)

    print(f"Season FPS lookup size: {len(season_fps_by_id)}")

    # Check first few period players
    for p in period_rankings[:10]:
        player_id = p.get('player_id')
        period_fps = p.get('avg_fps', 0)
        season_fps = season_fps_by_id.get(player_id, 0)
        diff = period_fps - season_fps
        print(f"  {p.get('name')}: period={period_fps}, season={season_fps}, diff={diff}")

    # Now call the actual function
    result = get_buy_low_sell_high('1month', '2026-27', 3, '')
    print(f"\nBLSH result: sell_high={len(result.get('sell_high', []))}, buy_low={len(result.get('buy_low', []))}")
    if result.get('sell_high'):
        print(f"First sell: {result['sell_high'][0]}")
    if result.get('buy_low'):
        print(f"First buy: {result['buy_low'][0]}")
    if result.get('error'):
        print(f"Error: {result['error']}")