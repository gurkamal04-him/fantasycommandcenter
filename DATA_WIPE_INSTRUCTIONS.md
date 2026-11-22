# Data Wipe & Reload Instructions for Deployed Site

This document explains how to completely wipe all cached data and force a fresh reload on the deployed Fantasy Command Center site (https://fantasycommandcenter.stream/).

## What Gets Cached

The application caches several types of data:

1. **Browser localStorage** - "My Team" roster (persists across sessions)
2. **Server-side LRU caches** - ESPN API responses (player rosters, box scores, schedules, rankings)
3. **Browser memory** - Player autocomplete cache (loaded on first focus)

---

## For End Users (Client-Side Only)

### Option 1: Clear "My Team" Roster Only
1. Open browser DevTools (F12)
2. Go to **Application** → **Local Storage** → `https://fantasycommandcenter.stream`
3. Find key `nba-fantasy-my-team-v1` and delete it
4. Refresh the page

### Option 2: Complete Client-Side Reset
1. Open browser DevTools (F12)
2. Go to **Application** → **Storage**
3. Click **Clear site data** (or right-click the origin → **Clear**)
4. Refresh the page

### Option 3: Hard Refresh (Clears Browser Memory Cache)
- **Windows/Linux**: `Ctrl + Shift + R`
- **Mac**: `Cmd + Shift + R`
- This forces re-download of all JS/CSS and clears the in-memory player cache

---

## For Developers/Admins (Server-Side Cache Clear)

The server maintains LRU caches for ESPN API responses. These are in-memory and reset when the server restarts. On the deployed platform (Streamlit/Render/etc.), you have these options:

### Method 1: Deploy a New Version (Recommended)
Any code deployment automatically restarts the server, clearing all LRU caches:
```bash
git commit --allow-empty -m "Force cache clear"
git push origin master
```

### Method 2: Use the API Endpoint
The app exposes a cache-clear endpoint:
```bash
curl -X POST https://fantasycommandcenter.stream/api/clear-cache
```
This clears all `lru_cache` decorated functions.

### Method 3: Manual Server Restart
If using a platform with manual restart (Render, Railway, Fly.io, etc.):
1. Go to your deployment dashboard
2. Trigger a "Restart" or "Redeploy"
3. Wait for the service to come back healthy

---

## What Each Cache Contains

| Cache | TTL / Scope | Cleared By |
|-------|-------------|------------|
| `get_all_players()` | 32 entries, per-process | Server restart / `/api/clear-cache` |
| `get_espn_box_score()` | 32 entries, per-process | Server restart / `/api/clear-cache` |
| `get_espn_team_schedule()` | 64 entries, per-process | Server restart / `/api/clear-cache` |
| `get_player_rankings()` | 16 entries, per-process | Server restart / `/api/clear-cache` |
| `_RANKINGS_CACHE` | Dict, per-process | Server restart / `/api/clear-cache` |
| `_BOX_SCORE_CACHE` | Dict (max 50), per-process | Server restart / `/api/clear-cache` |
| Player autocomplete (browser) | Session memory | Hard refresh / new tab |

---

## Verification Steps After Wipe

1. **Load the site** - Should show "Warming up data caches..." overlay
2. **Wait for ready** - Overlay disappears when `/api/ready` returns `{"ready": true}`
3. **Test player search** - Type in top search bar, should show players immediately
4. **Test box score** - Navigate to Box Score page, pick a game, verify names render correctly (no "Jr." issues)
5. **Test game times** - Check Scoreboard page, verify times are correct (not 1 hour early)
6. **Test My Team** - Add a player, verify it persists across refresh

---

## Troubleshooting

### "No players loading" in search
1. Hard refresh (`Ctrl+Shift+R`)
2. Check browser console for errors
3. Verify `/api/players/search` returns data:
   ```bash
   curl "https://fantasycommandcenter.stream/api/players/search?limit=10"
   ```

### Box score shows wrong names
- Server cache may have stale data. Trigger `/api/clear-cache` or redeploy.

### Game times still wrong
- Both scoreboard and box score endpoints now apply +1 hour shift
- If still wrong, check browser console: `formatGameTimePT()` function in `app.js`

### My Team not saving
- Check localStorage quota (5MB limit)
- Clear localStorage and try again
- Check browser console for quota errors

---

## Automated Cache Clear (Optional)

Add to your CI/CD pipeline to auto-clear on every deploy:
```yaml
# Example GitHub Actions step
- name: Clear server caches
  run: |
    curl -X POST https://fantasycommandcenter.stream/api/clear-cache
    echo "Server caches cleared"
```