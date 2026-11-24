#!/usr/bin/env python3
"""
Test suite for the background refresh system.
Tests TTLCache behavior, scheduler timing calculations, backoff logic, and endpoint metadata.
Run with: python test_refresh.py
"""

import time
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import sys
import os

# Add the app directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import from app after path setup
from app import (
    TTLCache,
    _get_pt_now,
    _get_next_live_run,
    _get_next_daily_run,
    _update_backoff,
    _should_run_job,
    _REFRESH_STATE,
    _REFRESH_LOCK,
    PT_ZONE,
)

# =============================================================================
# TEST: TTLCache behavior
# =============================================================================

def test_ttl_cache_basic():
    """Test basic TTL cache set/get with expiry."""
    cache = TTLCache(max_entries=10, default_ttl=1)
    cache.set("key1", "value1", ttl=1)
    assert cache.get("key1")[0] == "value1", "Should get value immediately"
    time.sleep(1.1)
    assert cache.get("key1")[0] is None, "Should be None after TTL expiry"
    print("[PASS] test_ttl_cache_basic passed")


def test_ttl_cache_stale_while_revalidate():
    """Test stale-while-revalidate behavior."""
    cache = TTLCache(max_entries=10, default_ttl=1)
    cache.set("key1", "value1", ttl=1)
    time.sleep(1.1)
    # With stale_ok=True, should return stale value
    value, is_stale, fetched_at, expires_at, is_final = cache.get("key1", stale_ok=True)
    assert value == "value1", "Should return stale value"
    assert is_stale is True, "Should be marked stale"
    # Without stale_ok, should return None
    assert cache.get("key1")[0] is None, "Should return None without stale_ok"
    print("[PASS] test_ttl_cache_stale_while_revalidate passed")


def test_ttl_cache_max_entries_eviction():
    """Test LRU eviction when max_entries exceeded."""
    cache = TTLCache(max_entries=3, default_ttl=60)
    cache.set("key1", "value1")
    cache.set("key2", "value2")
    cache.set("key3", "value3")
    assert cache.get("key1")[0] == "value1"
    assert cache.get("key2")[0] == "value2"
    assert cache.get("key3")[0] == "value3"
    # Add 4th entry - should evict least recently used (key1)
    cache.set("key4", "value4")
    assert cache.get("key1")[0] is None, "key1 should be evicted"
    assert cache.get("key2")[0] == "value2"
    assert cache.get("key3")[0] == "value3"
    assert cache.get("key4")[0] == "value4"
    print("[PASS] test_ttl_cache_max_entries_eviction passed")


def test_ttl_cache_is_final():
    """Test is_final flag for permanent caching."""
    cache = TTLCache(max_entries=10, default_ttl=1)
    cache.set("key1", "value1", ttl=1, is_final=True)
    time.sleep(1.1)
    # Should still return value because is_final=True
    value, is_stale, fetched_at, expires_at, is_final = cache.get("key1", stale_ok=True)
    assert value == "value1", "Final entries should never expire"
    assert is_final is True, "Should return is_final=True"
    assert is_stale is False, "Final entries should not be stale"
    print("[PASS] test_ttl_cache_is_final passed")


def test_ttl_cache_thread_safety():
    """Test thread-safe operations."""
    cache = TTLCache(max_entries=100, default_ttl=60)
    errors = []

    def writer():
        try:
            for i in range(100):
                cache.set(f"key{i}", f"value{i}")
                time.sleep(0.001)
        except Exception as e:
            errors.append(e)

    def reader():
        try:
            for _ in range(100):
                cache.get("key0")
                time.sleep(0.001)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=writer) for _ in range(3)]
    threads += [threading.Thread(target=reader) for _ in range(3)]

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0, f"Thread safety errors: {errors}"
    print("[PASS] test_ttl_cache_thread_safety passed")


# =============================================================================
# TEST: Scheduler timing calculations
# =============================================================================

def test_get_next_live_run():
    """Test LIVE job runs every 15 minutes at :00, :15, :30, :45."""
    # Test at various times
    test_times = [
        ("2026-01-15 10:00:00", "2026-01-15 10:15:00"),
        ("2026-01-15 10:07:30", "2026-01-15 10:15:00"),
        ("2026-01-15 10:15:00", "2026-01-15 10:30:00"),
        ("2026-01-15 10:29:59", "2026-01-15 10:30:00"),
        ("2026-01-15 10:30:00", "2026-01-15 10:45:00"),
        ("2026-01-15 10:45:00", "2026-01-15 11:00:00"),
        ("2026-01-15 10:59:59", "2026-01-15 11:00:00"),
    ]

    for input_time_str, expected_str in test_times:
        input_dt = datetime.fromisoformat(input_time_str).replace(tzinfo=PT_ZONE)
        expected_dt = datetime.fromisoformat(expected_str).replace(tzinfo=PT_ZONE)

        # Temporarily patch _get_pt_now
        import app
        original = app._get_pt_now
        app._get_pt_now = lambda: input_dt
        try:
            result = _get_next_live_run()
            assert result == expected_dt, f"For {input_time_str}: expected {expected_dt}, got {result}"
        finally:
            app._get_pt_now = original

    print("[PASS] test_get_next_live_run passed")


def test_get_next_daily_run():
    """Test DAILY job runs at 4:00 AM PT."""
    test_times = [
        ("2026-01-15 03:59:59", "2026-01-15 04:00:00"),  # Before 4 AM -> today
        ("2026-01-15 04:00:00", "2026-01-16 04:00:00"),  # At 4 AM -> tomorrow
        ("2026-01-15 12:00:00", "2026-01-16 04:00:00"),  # After 4 AM -> tomorrow
        ("2026-01-15 23:59:59", "2026-01-16 04:00:00"),  # Late night -> tomorrow
    ]

    for input_time_str, expected_str in test_times:
        input_dt = datetime.fromisoformat(input_time_str).replace(tzinfo=PT_ZONE)
        expected_dt = datetime.fromisoformat(expected_str).replace(tzinfo=PT_ZONE)

        import app
        original = app._get_pt_now
        app._get_pt_now = lambda: input_dt
        try:
            result = _get_next_daily_run()
            assert result == expected_dt, f"For {input_time_str}: expected {expected_dt}, got {result}"
        finally:
            app._get_pt_now = original

    print("[PASS] test_get_next_daily_run passed")


# =============================================================================
# TEST: Backoff logic
# =============================================================================

def test_update_backoff():
    """Test exponential backoff: 900s -> 1800s -> 3600s (cap)."""
    # Reset state
    _REFRESH_STATE['live']['backoff_seconds'] = 900
    _REFRESH_STATE['live']['backoff_until'] = 0
    _REFRESH_STATE['live']['status'] = 'idle'

    import time as time_module
    now = time_module.time()

    # First failure -> 1800s backoff (900 * 2, capped at 3600)
    _update_backoff('live', success=False)
    assert _REFRESH_STATE['live']['backoff_seconds'] == 1800
    assert _REFRESH_STATE['live']['backoff_until'] >= now + 1800 - 1  # allow 1s tolerance

    # Second failure -> 3600s backoff (capped)
    _update_backoff('live', success=False)
    assert _REFRESH_STATE['live']['backoff_seconds'] == 3600
    assert _REFRESH_STATE['live']['backoff_until'] >= now + 3600 - 1

    # Third failure -> still 3600s (cap)
    _update_backoff('live', success=False)
    assert _REFRESH_STATE['live']['backoff_seconds'] == 3600

    # Success resets backoff
    _update_backoff('live', success=True)
    assert _REFRESH_STATE['live']['backoff_seconds'] == 900
    assert _REFRESH_STATE['live']['backoff_until'] == 0

    print("[PASS] test_update_backoff passed")


def test_should_run_job():
    """Test _should_run_job respects status, backoff, and lock."""
    # Reset state
    _REFRESH_STATE['live']['status'] = 'idle'
    _REFRESH_STATE['live']['backoff_until'] = 0

    # Should run when idle
    assert _should_run_job('live') is True

    # Should not run when already running
    _REFRESH_STATE['live']['status'] = 'running'
    assert _should_run_job('live') is False
    _REFRESH_STATE['live']['status'] = 'idle'

    # Should not run during backoff
    import time as time_module
    _REFRESH_STATE['live']['backoff_until'] = time_module.time() + 100
    assert _should_run_job('live') is False
    _REFRESH_STATE['live']['backoff_until'] = 0

    # Should run after backoff expires
    _REFRESH_STATE['live']['backoff_until'] = time_module.time() - 100
    assert _should_run_job('live') is True

    print("[PASS] test_should_run_job passed")


# =============================================================================
# TEST: Endpoint cache metadata
# =============================================================================

def test_endpoint_metadata():
    """Test that endpoints return cache metadata fields."""
    from app import app as flask_app

    with flask_app.test_client() as client:
        # Test /api/scoreboard
        resp = client.get('/api/scoreboard')
        data = resp.get_json()
        assert 'generated_at' in data, "scoreboard should have generated_at"
        assert 'next_refresh_at' in data, "scoreboard should have next_refresh_at"
        assert 'stale' in data, "scoreboard should have stale"

        # Test /api/daily-top10
        resp = client.get('/api/daily-top10')
        data = resp.get_json()
        assert 'generated_at' in data, "daily-top10 should have generated_at"
        assert 'next_refresh_at' in data, "daily-top10 should have next_refresh_at"
        assert 'stale' in data, "daily-top10 should have stale"

        # Test /api/boxscore/<game_id> - use a test game ID
        # Note: this might 404 but should still have the right structure
        resp = client.get('/api/boxscore/401000000')
        if resp.status_code == 200:
            data = resp.get_json()
            assert 'generated_at' in data, "boxscore should have generated_at"
            assert 'expires_at' in data, "boxscore should have expires_at"
            assert 'is_final' in data, "boxscore should have is_final"
            assert 'stale' in data, "boxscore should have stale"

    print("[PASS] test_endpoint_metadata passed")


def test_refresh_status_endpoint():
    """Test /api/refresh-status returns job state and cache stats."""
    from app import app as flask_app

    with flask_app.test_client() as client:
        resp = client.get('/api/refresh-status')
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        data = resp.get_json()

        assert 'live' in data, "Should have live job state"
        assert 'daily' in data, "Should have daily job state"
        assert 'caches' in data, "Should have cache stats"
        assert 'server_time' in data, "Should have server_time"

        live = data['live']
        assert 'status' in live
        assert 'last_run' in live
        assert 'next_run' in live
        assert 'backoff_seconds' in live
        assert 'backoff_until' in live

        daily = data['daily']
        assert 'status' in daily
        assert 'last_run' in daily
        assert 'next_run' in daily

        caches = data['caches']
        assert 'scoreboard_entries' in caches
        assert 'top10_entries' in caches
        assert 'box_score_entries' in caches
        assert 'rankings_entries' in caches
        assert 'base_data_entries' in caches
        assert 'player_profile_epoch' in caches

    print("[PASS] test_refresh_status_endpoint passed")


def test_manual_refresh_endpoints():
    """Test /api/refresh/live and /api/refresh/daily POST endpoints."""
    from app import app as flask_app

    with flask_app.test_client() as client:
        # Test /api/refresh/live
        resp = client.post('/api/refresh/live')
        data = resp.get_json()
        assert resp.status_code in (200, 429), f"Expected 200 or 429, got {resp.status_code}"
        assert 'status' in data
        assert data['status'] in ('started', 'skipped')

        # Test /api/refresh/daily
        resp = client.post('/api/refresh/daily')
        data = resp.get_json()
        assert resp.status_code in (200, 429), f"Expected 200 or 429, got {resp.status_code}"
        assert 'status' in data
        assert data['status'] in ('started', 'skipped')

    print("[PASS] test_manual_refresh_endpoints passed")


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    print("Running background refresh system tests...\n")

    try:
        test_ttl_cache_basic()
        test_ttl_cache_stale_while_revalidate()
        test_ttl_cache_max_entries_eviction()
        test_ttl_cache_is_final()
        test_ttl_cache_thread_safety()

        test_get_next_live_run()
        test_get_next_daily_run()

        test_update_backoff()
        test_should_run_job()

        test_endpoint_metadata()
        test_refresh_status_endpoint()
        test_manual_refresh_endpoints()

        print("\n[SUCCESS] All tests passed!")
    except AssertionError as e:
        print(f"\n[FAIL] Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n[FAIL] Test error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)