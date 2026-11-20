/* =================================================================== */
/* NBA Fantasy Command Center - Frontend Logic                         */
/* =================================================================== */

(function() {
  'use strict';

  // ===================================================================
  // Utilities
  // ===================================================================
  const $ = (sel, ctx = document) => ctx.querySelector(sel);
  const $$ = (sel, ctx = document) => Array.from(ctx.querySelectorAll(sel));

  const fmt = (n, d = 1) => Number(n).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
  const signed = (n, d = 1) => (n > 0 ? '+' : n < 0 ? '-' : '') + fmt(Math.abs(n), d);

  const api = (path, options = {}) => fetch(path, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options
  }).then(r => {
    if (!r.ok) throw new Error(r.statusText);
    return r.json();
  });

  function show(el) { el.hidden = false; }
  function hide(el) { el.hidden = true; }
  function setText(el, text) { if (el) el.textContent = text; }

  // The rankings endpoint warms its cache in a background thread, so the
  // first response can come back with warming:true. Poll until it's ready.
  async function fetchRankingsWithPoll(url, { signal, maxAttempts = 60 } = {}) {
    for (let attempt = 0; attempt < maxAttempts; attempt++) {
      const r = await fetch(url, { signal });
      if (!r.ok) throw new Error(r.statusText);
      const data = await r.json();
      if (data.warming) {
        await new Promise(res => setTimeout(res, 2000));
        continue;
      }
      return data;
    }
    throw new Error('Timed out waiting for rankings');
  }

  // ===================================================================
  // Navigation / Page Switching
  // ===================================================================
  const pages = $$('.page');
  const navLinks = $$('.nav-link[data-page]');

  function activatePage(pageId) {
    pages.forEach(p => p.hidden = p.id !== pageId);
    navLinks.forEach(l => l.classList.toggle('active', l.dataset.page === pageId));
    window.scrollTo(0, 0);
  }

  navLinks.forEach(link => {
    link.addEventListener('click', e => {
      // Allow normal navigation for Hub (href="/") - it's a full page reload
      if (link.getAttribute('href') === '/') return;
      e.preventDefault();
      activatePage(link.dataset.page);
    });
  });

  // Hash-based deep linking
  window.addEventListener('hashchange', () => {
    const hash = location.hash.slice(1);
    if (hash && $(`#${hash}`)) activatePage(hash);
  });
  if (location.hash) {
    const hash = location.hash.slice(1);
    if ($(`#${hash}`)) activatePage(hash);
  }

  // ===================================================================
  // Autocomplete for player search
  // ===================================================================
  let playerCache = [];
  let playerCacheLoaded = false;

  async function loadPlayerCache() {
    if (playerCacheLoaded) return;
    playerCacheLoaded = true;
    try {
      const data = await api('/api/players/search?limit=500');
      playerCache = data;
    } catch (e) {
      console.warn('Player cache load failed', e);
    }
  }

  function setupAutocomplete(inputId, resultsId) {
    const input = $(`#${inputId}`);
    const results = $(`#${resultsId}`);
    if (!input || !results) return;

    let debounceTimer;
    let selectedIndex = -1;

        // When a player is selected, navigate to their profile page
        input.addEventListener('change', () => {
            const pid = input.dataset.playerId;
            if (pid) {
                window.location.href = `/player/${pid}`;
            }
        });

        // Preload the full player list on focus so the dropdown shows right away
        input.addEventListener('focus', () => {
            if (!input.value.trim()) {
                loadPlayerCache().then(() => {
                    if (playerCache.length) renderAutocomplete(playerCache, input, results);
                });
            }
        });

    input.addEventListener('input', () => {
      clearTimeout(debounceTimer);
      const q = input.value.trim().toLowerCase();
      if (q.length < 1) {
        if (playerCache.length) renderAutocomplete(playerCache, input, results);
        else hide(results);
        return;
      }
      // Use cached full list when available for instant results; fall back to API
      if (playerCache.length) {
        const matches = playerCache.filter(p =>
          p['DISPLAY_FIRST_LAST'].toLowerCase().startsWith(q) ||
          p['DISPLAY_FIRST_LAST'].toLowerCase().includes(q)
        );
        renderAutocomplete(matches, input, results);
        return;
      }
      debounceTimer = setTimeout(async () => {
        try {
          const data = await api(`/api/players/search?q=${encodeURIComponent(q)}`);
          renderAutocomplete(data, input, results);
        } catch (e) { console.error(e); }
      }, 150);
    });

    input.addEventListener('keydown', e => {
      const items = $$('.autocomplete-item', results);
      if (!items.length) return;
      if (e.key === 'ArrowDown') { e.preventDefault(); selectedIndex = Math.min(selectedIndex + 1, items.length - 1); updateSelection(items); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); selectedIndex = Math.max(selectedIndex - 1, -1); updateSelection(items); }
      else if (e.key === 'Enter' && selectedIndex >= 0) { e.preventDefault(); items[selectedIndex].click(); }
      else if (e.key === 'Escape') { hide(results); }
    });

    document.addEventListener('click', e => {
      if (!results.contains(e.target) && e.target !== input) hide(results);
    });
  }

  function renderAutocomplete(players, input, results) {
    if (!players.length) { hide(results); return; }
    results.innerHTML = players.map(p => `
      <div class="autocomplete-item" data-id="${p.PERSON_ID}" data-name="${p.DISPLAY_FIRST_LAST}">
        <div class="autocomplete-name">${p.DISPLAY_FIRST_LAST}</div>
        <div class="autocomplete-team">${p.TEAM_ABBREVIATION || 'FA'}</div>
      </div>
    `).join('');
    $$('.autocomplete-item', results).forEach(item => {
      item.addEventListener('click', () => {
        input.value = item.dataset.name;
        input.dataset.playerId = item.dataset.id;
        hide(results);
        input.dispatchEvent(new Event('change'));
      });
    });
    show(results);
  }

  function updateSelection(items) {
    items.forEach((item, i) => item.classList.toggle('selected', i === selectedIndex));
    if (selectedIndex >= 0) items[selectedIndex].scrollIntoView({ block: 'nearest' });
  }

  setupAutocomplete('ss-player-a', 'ss-ac-a');
  setupAutocomplete('ss-player-b', 'ss-ac-b');

  // ===================================================================
  // Trade Analyzer - autocomplete for roster inputs
  // ===================================================================
  let tradeAcCount = 0;
  function setupTradeAutocomplete(row) {
    const input = row.querySelector('input');
    if (!input) return;
    const acId = `trade-ac-${tradeAcCount++}`;
    const ac = document.createElement('div');
    ac.className = 'autocomplete';
    ac.id = acId;
    ac.hidden = true;
    input.parentNode.insertBefore(ac, input.nextSibling);
    setupAutocomplete(input.id || (input.id = `trade-input-${tradeAcCount}`), acId);
  }

  // Attach autocomplete to existing trade roster inputs
  $$('#trade-roster-a .player-row, #trade-roster-b .player-row').forEach(setupTradeAutocomplete);

  // Attach autocomplete to newly added trade roster inputs
  function addRosterInput(containerId) {
    const container = $(`#${containerId}`);
    const row = document.createElement('div');
    row.className = 'player-row';
    row.innerHTML = `<input type="text" placeholder="Player name" autocomplete="off"><button class="btn btn-sm btn-remove" aria-label="Remove">×</button>`;
    row.querySelector('.btn-remove').addEventListener('click', () => row.remove());
    container.appendChild(row);
    setupTradeAutocomplete(row);
  }

  // ===================================================================
  // Quick Win Projector (Hub)
  // ===================================================================
  const qpIds = ['qp-my-cur','qp-my-proj','qp-my-gp','qp-my-tot','qp-opp-cur','qp-opp-proj','qp-opp-gp','qp-opp-tot'];
  const qpKeyMap = {
    'qp-my-cur': 'my_cur', 'qp-my-proj': 'my_proj', 'qp-my-gp': 'my_played', 'qp-my-tot': 'my_total',
    'qp-opp-cur': 'opp_cur', 'qp-opp-proj': 'opp_proj', 'qp-opp-gp': 'opp_played', 'qp-opp-tot': 'opp_total'
  };
  $('#qp-calc')?.addEventListener('click', async () => {
    const vals = {};
    qpIds.forEach(id => { vals[qpKeyMap[id]] = Number($(`#${id}`).value) || 0; });
    try {
      const res = await api('/api/win-projector', { method: 'POST', body: JSON.stringify(vals) });
      renderQuickProjectorResult(res);
    } catch (e) { console.error(e); }
  });

  function renderQuickProjectorResult(r) {
    const pctEl = $('#qp-pct'), chipEl = $('#qp-chip'), barEl = $('#qp-bar'), noteEl = $('#qp-note');
    const pct = r.win_probability;
    pctEl.textContent = fmt(pct) + '%';
    chipEl.textContent = pct >= 85 ? 'Strong favorite' : pct >= 60 ? 'Favored' : pct > 40 ? 'Toss-up' : pct > 15 ? 'Underdog' : 'Long shot';
    chipEl.className = 'chip ' + (pct >= 60 ? 'good' : pct <= 40 ? 'bad' : 'mid');
    barEl.style.width = pct + '%';
    noteEl.textContent = `Projected: You ${fmt(r.my_final)} – Opp ${fmt(r.opp_final)} | Margin ${signed(r.margin)} | Need ${fmt(r.need)} (${fmt(r.need_per_game)}/game)`;
    show($('#qp-result'));
  }

  // ===================================================================
  // Scoreboard (Hub)
  // ===================================================================
  const todayStr = TODAY;
  $('#scoreboard-date').value = todayStr;

  async function loadScoreboard(date) {
    const el = $('#scoreboard-games');
    el.innerHTML = '<div class="loading">Loading games…</div>';
    try {
      const data = await api(`/api/scoreboard?date=${date}`);
      renderScoreboard(data.games);
    } catch (e) {
      el.innerHTML = '<div class="empty">Failed to load games</div>';
      console.error(e);
    }
  }

  function renderScoreboard(games) {
    const el = $('#scoreboard-games');
    if (!games.length) { el.innerHTML = '<div class="empty">No games scheduled</div>'; return; }
    el.innerHTML = games.map(g => {
      const home = g.home || {};
      const away = g.away || {};
      const status = g.gameStatusText || 'Scheduled';
      const time = g.gameEt || '';
      let timeDisplay = status;
      let dateDisplay = '';
      if (time) {
        const d = new Date(time);
        if (!isNaN(d.getTime())) {
          timeDisplay = formatGameTimePT(time);
          dateDisplay = d.toLocaleDateString([], {weekday: 'short', month: 'short', day: 'numeric', timeZone: 'America/Los_Angeles'});
        }
      }
      return `
        <div class="sb-game">
          <div class="sb-team">
            <span class="sb-team-name">${away.teamTricode || 'AWAY'}</span>
            <span class="sb-team-record">${away.score !== undefined ? away.score : ''}</span>
          </div>
          <div class="sb-status">
            ${dateDisplay ? `<div class="sb-date" style="font-size:11px;color:var(--ink-muted);margin-bottom:2px;">${dateDisplay}</div>` : ''}
            <div class="sb-time">${timeDisplay}</div>
            ${g.gameId ? `<span class="sb-badge" onclick="loadBoxScore('${g.gameId}')" style="cursor:pointer">Box Score</span>` : ''}
          </div>
          <div class="sb-team" style="text-align:right">
            <span class="sb-team-name">${home.teamTricode || 'HOME'}</span>
            <span class="sb-team-record">${home.score !== undefined ? home.score : ''}</span>
          </div>
        </div>
      `;
    }).join('');
  }

  // Convert ESPN gameEt (ISO UTC) to Pacific time string
  function formatGameTimePT(etStr) {
    if (!etStr) return '';
    const d = new Date(etStr);
    if (isNaN(d.getTime())) return etStr;
    // Use proper timezone conversion to Pacific Time
    return d.toLocaleTimeString([], {hour: 'numeric', minute: '2-digit', timeZone: 'America/Los_Angeles'});
  }

  $('#sb-refresh')?.addEventListener('click', () => loadScoreboard($('#scoreboard-date').value));
  loadScoreboard(todayStr);

  // ===================================================================
  // Box Score "Today's Games" Panel
  // ===================================================================
  async function loadBSTodayGames(date) {
    const el = $('#bs-scoreboard-games');
    if (!el) return;
    el.innerHTML = '<div class="loading">Loading games…</div>';
    try {
      const data = await api(`/api/scoreboard?date=${date}`);
      renderBSTodayGames(data.games);
    } catch (e) {
      el.innerHTML = '<div class="empty">Failed to load games</div>';
      console.error(e);
    }
  }

  function renderBSTodayGames(games) {
    const el = $('#bs-scoreboard-games');
    if (!el) return;
    if (!games.length) { el.innerHTML = '<div class="empty">No games scheduled</div>'; return; }
    el.innerHTML = games.map(g => {
      const home = g.home || {};
      const away = g.away || {};
      const status = g.gameStatusText || 'Scheduled';
      const time = g.gameEt || '';
      let timeDisplay = status;
      let dateDisplay = '';
      if (time) {
        const d = new Date(time);
        if (!isNaN(d.getTime())) {
          timeDisplay = formatGameTimePT(time);
          dateDisplay = d.toLocaleDateString([], {weekday: 'short', month: 'short', day: 'numeric', timeZone: 'America/Los_Angeles'});
        }
      }
      return `
        <div class="sb-game">
          <div class="sb-team">
            <span class="sb-team-name">${away.teamTricode || 'AWAY'}</span>
            <span class="sb-team-record">${away.score !== undefined ? away.score : ''}</span>
          </div>
          <div class="sb-status">
            ${dateDisplay ? `<div class="sb-date" style="font-size:11px;color:var(--ink-muted);margin-bottom:2px;">${dateDisplay}</div>` : ''}
            <div class="sb-time">${timeDisplay}</div>
            ${g.gameId ? `<button class="btn btn-sm btn-ghost" onclick="loadBoxScore('${g.gameId}')">Box Score</button>` : ''}
          </div>
          <div class="sb-team" style="text-align:right">
            <span class="sb-team-name">${home.teamTricode || 'HOME'}</span>
            <span class="sb-team-record">${home.score !== undefined ? home.score : ''}</span>
          </div>
        </div>
      `;
    }).join('');
  }

  // Initialize box score today's games panel
  const bsDateInput = $('#bs-scoreboard-date');
  if (bsDateInput) {
    bsDateInput.value = TODAY;
    loadBSTodayGames(TODAY);
    $('#bs-sb-refresh')?.addEventListener('click', () => loadBSTodayGames(bsDateInput.value));
  }

  // ===================================================================
  // Top Streamers (Hub)
  // ===================================================================
  async function loadStreamers() {
    const el = $('#streamer-list');
    el.innerHTML = '<div class="loading">Loading…</div>';
    try {
      const data = await api('/api/waiver-streamers?days=7&min_games=3');
      renderStreamers(data.teams);
    } catch (e) { el.innerHTML = '<div class="empty">Failed to load</div>'; console.error(e); }
  }

  function renderStreamers(teams) {
    const el = $('#streamer-list');
    if (!teams.length) { el.innerHTML = '<div class="empty">No high-density teams</div>'; return; }
    el.innerHTML = teams.slice(0, 8).map(t => `
      <div class="streamer-item">
        <span class="streamer-team">${t.team}</span>
        <span class="streamer-name">${t.team_name}</span>
        <span class="streamer-games">${t.games}</span>
        <span class="streamer-b2b">${t.b2b} B2B · ${t.avg_rest}d rest</span>
      </div>
    `).join('');
  }

  loadStreamers();

  // ===================================================================
  // Schedule Summarizer
  // ===================================================================
  $('#sched-run')?.addEventListener('click', async () => {
    const days = Number($('#sched-days').value);
    const minGames = Number($('#sched-min-games').value);
    const tbody = $('#sched-table tbody');
    tbody.innerHTML = '<tr><td colspan="5" class="loading">Analyzing…</td></tr>';
    try {
      const teams = await api('/api/teams');
      const results = [];
      for (const team of teams) {
        const sched = await api(`/api/team/${team.id}/schedule?days=${days}`);
        if (sched.games_count >= minGames) results.push({ team, sched });
      }
      results.sort((a,b) => b.sched.games_count - a.sched.games_count);
      renderScheduleTable(results);
    } catch (e) { tbody.innerHTML = '<tr><td colspan="5" class="empty">Failed</td></tr>'; console.error(e); }
  });

  function renderScheduleTable(results) {
    const tbody = $('#sched-table tbody');
    tbody.innerHTML = results.map(r => {
      const gamesHtml = r.sched.games.map((g, i) => {
        const prev = r.sched.games[i - 1];
        const isB2b = prev && (new Date(g.date) - new Date(prev.date)) / 86400000 <= 1;
        return `
        <span class="sched-game${isB2b ? ' b2b' : ''}">
          ${new Date(g.date).toLocaleDateString([], {month:'short',day:'numeric'})} ${g.matchup}
        </span>
      `}).join('') || '—';
      return `
        <tr>
          <td><span class="team-abbr">${r.team.abbreviation}</span> ${r.team.full_name}</td>
          <td class="num">${r.sched.games_count}</td>
          <td class="num">${r.sched.b2b_count}</td>
          <td class="num">${r.sched.avg_rest.toFixed(1)}</td>
          <td>${gamesHtml}</td>
        </tr>
      `;
    }).join('') || '<tr><td colspan="5" class="empty">No teams match criteria</td></tr>';
  }

  // ===================================================================
  // ROS Heatmap
  // ===================================================================
  $('#heatmap-run')?.addEventListener('click', async () => {
    const weeks = Number($('#heatmap-weeks').value);
    const wrap = $('#heatmap-table-wrap');
    wrap.innerHTML = '<div class="loading">Generating heatmap…</div>';
    try {
      const data = await api(`/api/ros-heatmap?weeks=${weeks}`);
      renderHeatmap(data);
    } catch (e) { wrap.innerHTML = '<div class="empty">Failed</div>'; console.error(e); }
  });

  function renderHeatmap(data) {
    const wrap = $('#heatmap-table-wrap');
    const weeks = data.weeks;
    const headers = ['Team', 'Total', ...Array.from({length: weeks}, (_,i) => `Wk ${i+1}`)];
    const rows = data.teams.map(t => {
      const cells = t.weekly.map(w => {
        const cls = w.games === 0 ? 'cell-0' : w.games === 1 ? 'cell-1' : w.games === 2 ? 'cell-2' : w.games === 3 ? 'cell-3' : w.games === 4 ? 'cell-4' : 'cell-5';
        const b2b = w.b2b > 0 ? `<span class="b2b-badge">${w.b2b}B2B</span>` : '';
        return `<td class="${cls}">${w.games}${b2b}</td>`;
      });
      return `<tr><td><span class="team-abbr">${t.abbreviation}</span></td><td class="num">${t.total_games}</td>${cells.join('')}</tr>`;
    });
    wrap.innerHTML = `
      <table class="heatmap-table">
        <thead><tr>${headers.map(h => `<th>${h}</th>`).join('')}</tr></thead>
        <tbody>${rows.join('')}</tbody>
      </table>
    `;
  }

  // ===================================================================
  // Weekly Schedule Analyzer (Main Grid View)
  // ===================================================================
  let currentWeek = 1;
  let selectedDays = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
  let wsSortBy = 'games_desc';
  let wsTeamsData = [];

  // Initialize week buttons (W1-W23)
  function initWeekButtons() {
    const container = $('#week-buttons');
    if (!container) return;
    container.innerHTML = Array.from({length: 23}, (_, i) => i + 1)
      .map(w => `<button class="btn btn-sm week-btn${w === 1 ? ' active' : ''}" data-week="${w}">W${w}</button>`)
      .join('');
    $$('.week-btn', container).forEach(btn => {
      btn.addEventListener('click', () => {
        $$('.week-btn', container).forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        currentWeek = Number(btn.dataset.week);
      });
    });
  }

  // Initialize day checkboxes
  function initDayCheckboxes() {
    const container = $('#day-checkboxes');
    if (!container) return;
    const dayLabels = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
    container.innerHTML = dayLabels.map(day => `
      <label class="day-check">
        <input type="checkbox" value="${day}" checked>
        <span>${day}</span>
      </label>
    `).join('');
    $$('input[type="checkbox"]', container).forEach(cb => {
      cb.addEventListener('change', () => {
        selectedDays = $$('input[type="checkbox"]:checked', container).map(c => c.value);
      });
    });
  }

  // Sort selector
  $('#ws-sort')?.addEventListener('change', (e) => {
    wsSortBy = e.target.value;
    if (wsTeamsData.length) renderWeeklySchedule({ teams: wsTeamsData, week: currentWeek, week_start: '', week_end: '' });
  });

  // Load weekly schedule data
  $('#ws-load')?.addEventListener('click', async () => {
    const season = $('#ws-season').value;
    const tbody = $('#ws-tbody');
    const resultsEl = $('#weekly-schedule-results');
    const metaEl = $('#ws-meta');
    const legendEl = $('#ws-legend');

    show(resultsEl);
    show(legendEl);
    metaEl.innerHTML = '<div class="loading">Loading weekly schedule…</div>';
    tbody.innerHTML = '';

    try {
      const daysParam = selectedDays.join(',');
      const data = await api(`/api/weekly-schedule?week=${currentWeek}&season=${season}&days=${daysParam}`);
      wsTeamsData = data.teams || [];
      renderWeeklySchedule(data);
    } catch (e) {
      metaEl.innerHTML = '<div class="empty">Failed to load schedule</div>';
      console.error(e);
    }
  });

  function renderWeeklySchedule(data) {
    const dayLabels = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun']; // ensure same order
    const tbody = $('#ws-tbody');
    const metaEl = $('#ws-meta');
    let teams = data.teams || [];
    const week = data.week;
    const weekStart = data.week_start;
    const weekEnd = data.week_end;

    // Apply sorting
    teams = sortTeams(teams, wsSortBy);

    // Add game counts to meta info
    const gamesCount = teams.reduce((sum, team) => sum + (team.games_count || 0), 0);
    const b2bCount = teams.reduce((sum, team) => sum + (team.b2b_count || 0), 0);

    metaEl.innerHTML = `
      <div class="ws-meta-info">
        <strong>Week ${week}</strong> | ${weekStart} to ${weekEnd} |
        <span class="team-count">${teams.length} teams</span> |
        <span class="game-count">${gamesCount} total games</span> |
        <span class="b2b-count">${b2bCount} B2Bs</span>
      </div>
    `;

    if (!teams.length) {
      tbody.innerHTML = '';
      return;
    }

    // Compute total games per day across all teams
    const dayGamesTotals = dayLabels.reduce((obj, day) => {
      obj[day] = 0;
      return obj;
    }, {});
    teams.forEach(team => {
      const days = team.days || {};
      dayLabels.forEach(day => {
        const games = days[day] || [];
        if (games.length) dayGamesTotals[day] += 1; // each team contributes at most 1 game per day
      });
    });
    // Update day header labels with counts
    dayLabels.forEach(day => {
      const th = document.querySelector(`th[data-day="${day}"]`);
      if (th) {
        const count = dayGamesTotals[day] || 0;
        th.innerHTML = `${day}<span class="ws-day-count">(${count})</span>`;
      }
    });

    tbody.innerHTML = teams.map(team => {
      const abbr = team.abbreviation;
      const gamesCount = team.games_count || 0;
      const b2bCount = team.b2b_count || 0;
      const days = team.days || {};

      const dayCells = dayLabels.map(day => {
        const games = days[day] || [];
        // Enforce at most one game per day
        const game = games[0];
        if (!game) return '<td class="ws-empty">—</td>';
        return `<td class="ws-day-cell">${renderGameChip(game)}</td>`;
      }).join('');

      const gamesClass = gamesCount >= 4 ? 'ws-high' : gamesCount >= 3 ? 'ws-med' : '';

      return `
        <tr class="${gamesClass}">
          <td class="ws-team-cell"><span class="team-abbr">${abbr}</span> <span class="ws-games-count">(${gamesCount})</span></td>
          ${dayCells}
        </tr>
      `;
    }).join('');
  }

  function sortTeams(teams, sortBy) {
    const arr = [...teams];
    switch (sortBy) {
      case 'games_asc':
        return arr.sort((a, b) => (a.games_count || 0) - (b.games_count || 0));
      case 'team':
        return arr.sort((a, b) => a.abbreviation.localeCompare(b.abbreviation));
      case 'b2b':
        return arr.sort((a, b) => (b.b2b_count || 0) - (a.b2b_count || 0));
      case 'games_desc':
      default:
        return arr.sort((a, b) => (b.games_count || 0) - (a.games_count || 0));
    }
  }

  function renderGameChip(game) {
    const isHome = game.is_home;
    const opp = game.opp_tricode;
    const loc = isHome ? 'vs' : '@';
    const d = new Date(game.date);
    const dayNum = d.getDate();
    const dayName = game.day || '';

    return `
      <span class="ws-chip ${isHome ? 'home' : 'away'}" title="${game.date}">
        <span class="ws-day">${dayName}</span>
        <span class="ws-date">${dayNum}</span>
        <span class="ws-loc">${loc}</span>
        <span class="ws-opp">${opp}</span>
      </span>
    `;
  }

  // Export weekly schedule to CSV
  $('#ws-export')?.addEventListener('click', () => {
    if (!wsTeamsData.length) return alert('Load a week first');
    exportWeeklyScheduleCSV(wsTeamsData, currentWeek);
  });

  function exportWeeklyScheduleCSV(teams, week) {
    const dayLabels = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
    const headers = ['Team', 'Games', 'B2B', 'Games This Week'];

    const rows = teams.map(team => {
      const days = team.days || {};
      const row = [team.abbreviation, team.games_count || 0, team.b2b_count || 0];

      dayLabels.forEach(day => {
        const games = days[day] || [];
        row.push(games.map(g => `${g.is_home ? 'vs' : '@'}${g.opp_tricode}`).join('; '));
      });

      return row;
    });

    const csv = [headers.join(','), ...rows.map(r => r.map(v => `"${v}"`).join(','))].join('\n');
    downloadCSV(csv, `weekly-schedule-week-${week}.csv`);
  }

  // Initialize on load
  initWeekButtons();
  initDayCheckboxes();
  setupAutocomplete('global-search', 'search-results');

  // ===================================================================
  // ROS Heatmap
  // ===================================================================
  let heatmapSortBy = 'total_desc';
  let heatmapData = [];

  $('#heatmap-sort')?.addEventListener('change', (e) => {
    heatmapSortBy = e.target.value;
    if (heatmapData.length) renderHeatmap({ weeks: heatmapData[0]?.weekly?.length || 4, teams: heatmapData });
  });

  $('#heatmap-run')?.addEventListener('click', async () => {
    const weeks = Number($('#heatmap-weeks').value);
    const wrap = $('#heatmap-table-wrap');
    wrap.innerHTML = '<div class="loading">Generating heatmap…</div>';
    try {
      const data = await api(`/api/ros-heatmap?weeks=${weeks}`);
      heatmapData = data.teams || [];
      renderHeatmap(data);
    } catch (e) { wrap.innerHTML = '<div class="empty">Failed</div>'; console.error(e); }
  });

  function renderHeatmap(data) {
    const wrap = $('#heatmap-table-wrap');
    let teams = data.teams || [];
    const weeks = data.weeks;

    // Apply sorting
    teams = sortHeatmapTeams(teams, heatmapSortBy);

    const headers = ['Team', 'Total', 'B2B', ...Array.from({length: weeks}, (_,i) => `Wk ${i+1}`)];
    const rows = teams.map(t => {
      const cells = t.weekly.map(w => {
        const cls = w.games === 0 ? 'cell-0' : w.games === 1 ? 'cell-1' : w.games === 2 ? 'cell-2' : w.games === 3 ? 'cell-3' : w.games === 4 ? 'cell-4' : 'cell-5';
        const b2b = w.b2b > 0 ? `<span class="b2b-badge">${w.b2b}B2B</span>` : '';
        return `<td class="${cls}">${w.games}${b2b}</td>`;
      });
      return `<tr><td><span class="team-abbr">${t.abbreviation}</span></td><td class="num">${t.total_games}</td><td class="num">${t.b2b_count}</td>${cells.join('')}</tr>`;
    });
    wrap.innerHTML = `
      <table class="heatmap-table">
        <thead><tr>${headers.map(h => `<th>${h}</th>`).join('')}</tr></thead>
        <tbody>${rows.join('')}</tbody>
      </table>
    `;
  }

  function sortHeatmapTeams(teams, sortBy) {
    const arr = [...teams];
    switch (sortBy) {
      case 'team':
        return arr.sort((a, b) => a.abbreviation.localeCompare(b.abbreviation));
      case 'b2b':
        return arr.sort((a, b) => (b.b2b_count || 0) - (a.b2b_count || 0));
      case 'total_desc':
      default:
        return arr.sort((a, b) => (b.total_games || 0) - (a.total_games || 0));
    }
  }

  // Export heatmap to CSV
  $('#heatmap-export')?.addEventListener('click', () => {
    if (!heatmapData.length) return alert('Generate heatmap first');
    exportHeatmapCSV(heatmapData);
  });

  function exportHeatmapCSV(teams) {
    const weeks = teams[0]?.weekly?.length || 4;
    const headers = ['Team', 'Total Games', 'Total B2B', ...Array.from({length: weeks}, (_,i) => `Wk ${i+1} Games`), ...Array.from({length: weeks}, (_,i) => `Wk ${i+1} B2B`)];

    const rows = teams.map(t => {
      const row = [t.abbreviation, t.total_games, t.b2b_count];
      t.weekly.forEach(w => row.push(w.games));
      t.weekly.forEach(w => row.push(w.b2b));
      return row;
    });

    const csv = [headers.join(','), ...rows.map(r => r.map(v => `"${v}"`).join(','))].join('\n');
    downloadCSV(csv, 'ros-heatmap.csv');
  }

  // ===================================================================
  // Schedule Summarizer (Lookahead)
  // ===================================================================
  let schedSortBy = 'games_desc';
  let schedResults = [];

  $('#sched-sort')?.addEventListener('change', (e) => {
    schedSortBy = e.target.value;
    if (schedResults.length) renderScheduleTable(schedResults);
  });

  $('#sched-run')?.addEventListener('click', async () => {
    const days = Number($('#sched-days').value);
    const minGames = Number($('#sched-min-games').value);
    const tbody = $('#sched-table tbody');
    tbody.innerHTML = '<tr><td colspan="5" class="loading">Analyzing…</td></tr>';
    try {
      const teams = await api('/api/teams');
      const results = [];
      for (const team of teams) {
        const sched = await api(`/api/team/${team.id}/schedule?days=${days}`);
        if (sched.games_count >= minGames) results.push({ team, sched });
      }
      schedResults = results;
      renderScheduleTable(results);
    } catch (e) { tbody.innerHTML = '<tr><td colspan="5" class="empty">Failed</td></tr>'; console.error(e); }
  });

  function renderScheduleTable(results) {
    const tbody = $('#sched-table tbody');
    let sorted = sortScheduleResults(results, schedSortBy);
    tbody.innerHTML = sorted.map(r => {
      const gamesHtml = r.sched.games.map(g => `
        <span class="sched-game${(g.date - (r.sched.games[r.sched.games.indexOf(g)-1]?.date || g.date)).days <= 1 ? ' b2b' : ''}">
          ${new Date(g.date).toLocaleDateString([], {month:'short',day:'numeric'})} ${g.matchup}
        </span>
      `).join('') || '—';
      return `
        <tr>
          <td><span class="team-abbr">${r.team.abbreviation}</span> ${r.team.full_name}</td>
          <td class="num">${r.sched.games_count}</td>
          <td class="num">${r.sched.b2b_count}</td>
          <td class="num">${r.sched.avg_rest.toFixed(1)}</td>
          <td>${gamesHtml}</td>
        </tr>
      `;
    }).join('') || '<tr><td colspan="5" class="empty">No teams match criteria</td></tr>';
  }

  function sortScheduleResults(results, sortBy) {
    const arr = [...results];
    switch (sortBy) {
      case 'b2b':
        return arr.sort((a, b) => (b.sched.b2b_count || 0) - (a.sched.b2b_count || 0));
      case 'rest':
        return arr.sort((a, b) => (a.sched.avg_rest || 0) - (b.sched.avg_rest || 0));
      case 'team':
        return arr.sort((a, b) => a.team.abbreviation.localeCompare(b.team.abbreviation));
      case 'games_desc':
      default:
        return arr.sort((a, b) => (b.sched.games_count || 0) - (a.sched.games_count || 0));
    }
  }

  // Export schedule summarizer to CSV
  $('#sched-export')?.addEventListener('click', () => {
    if (!schedResults.length) return alert('Run analysis first');
    exportScheduleCSV(schedResults);
  });

  function exportScheduleCSV(results) {
    const headers = ['Team', 'Games', 'B2B', 'Avg Rest', 'Schedule'];
    const rows = results.map(r => [
      r.team.abbreviation,
      r.sched.games_count,
      r.sched.b2b_count,
      r.sched.avg_rest.toFixed(1),
      r.sched.games.map(g => `${new Date(g.date).toLocaleDateString()} ${g.matchup}`).join('; ')
    ]);
    const csv = [headers.join(','), ...rows.map(r => r.map(v => `"${v}"`).join(','))].join('\n');
    downloadCSV(csv, 'schedule-summarizer.csv');
  }

  // ===================================================================
  // CSV Export Utility
  // ===================================================================
  function downloadCSV(csv, filename) {
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    link.click();
    URL.revokeObjectURL(link.href);
  }

  // ===================================================================
  // Box Score
  // ===================================================================
  $('#bs-load')?.addEventListener('click', async () => {
    let gameId = $('#bs-game-id').value.trim();
    if (!gameId) return alert('Enter a valid Game ID');
    // Accept both 9-digit ESPN IDs and 10-digit NBA.com IDs
    if (gameId.length !== 9 && gameId.length !== 10) return alert('Enter a valid 9-digit ESPN Game ID or 10-digit NBA.com Game ID');
    const results = $('#boxscore-results');
    const tbody = $('#boxscore-results tbody');
    const meta = $('#bs-meta');
    show(results);
    tbody.innerHTML = '<tr><td colspan="15" class="loading">Loading box score…</td></tr>';
    meta.textContent = '';
    try {
      const data = await api(`/api/boxscore/${gameId}`);
      renderBoxScore(data);
    } catch (e) { tbody.innerHTML = '<tr><td colspan="15" class="empty">Failed to load</td></tr>'; console.error(e); }
  });

  // Box Score state
  let bsPlayersData = [];
  let bsSortBy = 'fps';
  let bsSortDir = 'desc';

  function renderBoxScore(data) {
    const tbody = $('#boxscore-results tbody');
    const meta = $('#bs-meta');
    let metaHtml = `<span class="bs-meta-item"><strong>Game ID:</strong> ${data.game_id}</span><span class="bs-meta-item"><strong>Players:</strong> ${data.players.length}</span>`;

    // Display game date/time in PCT if available
    if (data.game_date) {
      const d = new Date(data.game_date);
      if (!isNaN(d.getTime())) {
        const pctTime = d.toLocaleTimeString([], {hour: 'numeric', minute: '2-digit', timeZone: 'America/Los_Angeles'});
        const pctDate = d.toLocaleDateString([], {weekday: 'short', month: 'short', day: 'numeric', timeZone: 'America/Los_Angeles'});
        metaHtml += `<span class="bs-meta-item"><strong>Game Time (PCT):</strong> ${pctDate} ${pctTime}</span>`;
      }
    }

    meta.innerHTML = metaHtml;

    bsPlayersData = data.players || [];
    sortAndRenderBoxScore();
  }

  function sortAndRenderBoxScore() {
    const tbody = $('#boxscore-results tbody');
    if (!bsPlayersData.length) {
      tbody.innerHTML = '<tr><td colspan="15" class="empty">No player data</td></tr>';
      return;
    }

    // Sort
    const sorted = [...bsPlayersData].sort((a, b) => {
      const va = a[bsSortBy] ?? 0;
      const vb = b[bsSortBy] ?? 0;
      const cmp = va < vb ? -1 : va > vb ? 1 : 0;
      return bsSortDir === 'desc' ? -cmp : cmp;
    });

    tbody.innerHTML = sorted.map(p => {
      const fgVal = p.fga > 0 ? (p.fgm/p.fga * 100) : 0;
      const fg3Val = p.fg3a > 0 ? (p.fg3m/p.fg3a * 100) : 0;
      const ftVal = p.fta > 0 ? (p.ftm/p.fta * 100) : 0;

      const fgPct = fgVal.toFixed(1);
      const fg3Pct = fg3Val.toFixed(1);
      const ftPct = ftVal.toFixed(1);

      // Color based on distance from league average (FG: 45.9%, 3P: 35.1%, FT: 78.1%)
      const getDiffColor = (val, avg, att) => {
        if (att === 0) return '';
        const diff = val - avg;
        if (Math.abs(diff) < 0.5) return 'color: var(--ink)';
        if (diff > 0) {
          const intensity = Math.min(1, diff / 15);
          return `color: rgb(${Math.round(100 * (1 - intensity) + 34)}, ${Math.round(180 + 50 * intensity)}, ${Math.round(100 * (1 - intensity) + 94)})`;
        } else {
          const intensity = Math.min(1, Math.abs(diff) / 15);
          return `color: rgb(${Math.round(200 + 55 * intensity)}, ${Math.round(80 * (1 - intensity))}, ${Math.round(80 * (1 - intensity))})`;
        }
      };

      const fgColor = getDiffColor(fgVal, 45.9, p.fga);
      const fg3Color = getDiffColor(fg3Val, 35.1, p.fg3a);
      const ftColor = getDiffColor(ftVal, 78.1, p.fta);

      // FPS color: green if >= 25 (avg), red if < 25
      const fpsVal = p.fps ?? 0;
      const fpsClass = fpsVal >= 25 ? 'fps-good' : 'fps-bad';

      return `
      <tr>
        <td class="player-name">${p.name}</td>
        <td class="team-abbr">${p.team}</td>
        <td class="num">${p.min}</td>
        <td class="num">${p.pts}</td>
        <td class="num">${p.reb}</td>
        <td class="num">${p.ast}</td>
        <td class="num">${p.stl}</td>
        <td class="num">${p.blk}</td>
        <td class="num">${p.tov}</td>
        <td class="num">
           <div style="font-weight:600; ${fgColor}">${fgPct}%</div>
           <div style="font-size:10px; color:var(--ink-muted)">${p.fgm}/${p.fga}</div>
        </td>
        <td class="num">
           <div style="font-weight:600; ${fg3Color}">${fg3Pct}%</div>
           <div style="font-size:10px; color:var(--ink-muted)">${p.fg3m}/${p.fg3a}</div>
        </td>
        <td class="num">
           <div style="font-weight:600; ${ftColor}">${ftPct}%</div>
           <div style="font-size:10px; color:var(--ink-muted)">${p.ftm}/${p.fta}</div>
        </td>
        <td class="num usage">${p.usage_pct}%</td>
        <td class="num fps ${fpsClass}">${fmt(fpsVal)}</td>
        <td class="num ${p.plus_minus > 0 ? 'plus' : p.plus_minus < 0 ? 'minus' : ''}">${signed(p.plus_minus, 0)}</td>
      </tr>
    `;
    }).join('');
  }

  // Global for scoreboard onclick
  window.loadBoxScore = (gameId) => {
    activatePage('boxscore');
    $('#bs-game-id').value = gameId;
    $('#bs-load').click();
  };

  // Box Score sorting
  $$('.th-sort', $('#bs-table')).forEach(th => {
    th.addEventListener('click', () => {
      const key = th.dataset.sort;
      if (bsSortBy === key) {
        bsSortDir = bsSortDir === 'desc' ? 'asc' : 'desc';
      } else {
        bsSortBy = key;
        bsSortDir = 'desc';
      }
      // Update visual indicators
      $$('.th-sort').forEach(t => t.classList.remove('sort-asc', 'sort-desc'));
      th.classList.add(bsSortDir === 'desc' ? 'sort-desc' : 'sort-asc');
      sortAndRenderBoxScore();
    });
  });

  // ===================================================================
  // Player Rankings
  // ===================================================================
  let rkPlayersData = [];
  let rkSortBy = 'total_fps';
  let rkSortDir = 'desc';
  let rkSearchTerm = '';

  // Live player-name search: filters the rendered table as you type.
  $('#rk-search')?.addEventListener('input', (e) => {
    rkSearchTerm = e.target.value.trim().toLowerCase();
    sortAndRenderRankings();
  });

  // Show/hide the custom date-range inputs when the period changes
  $('#rk-period')?.addEventListener('change', () => {
    const custom = $('#rk-custom-range');
    if ($('#rk-period').value !== 'custom') {
      custom.hidden = true;
    } else {
      custom.hidden = false;
      // Pre-fill with the last 30 days if blank
      const today = new Date();
      const iso = (d) => d.toISOString().slice(0, 10);
      if (!$('#rk-start-date').value) {
        const start = new Date(today);
        start.setDate(start.getDate() - 30);
        $('#rk-start-date').value = iso(start);
      }
      if (!$('#rk-end-date').value) {
        $('#rk-end-date').value = iso(today);
      }
    }
  });

  $('#rk-load')?.addEventListener('click', async () => {
    const period = $('#rk-period').value;
    const season = $('#rk-season').value;
    const sortBy = $('#rk-sort').value;
    rkSortBy = sortBy;
    rkSortDir = 'desc'; // reset to desc when loading new data
    const startDate = $('#rk-start-date').value;
    const endDate = $('#rk-end-date').value;
    const tbody = $('#rk-tbody');
    const meta = $('#rk-meta');
    const wrap = $('#rankings-results');
    show(wrap);
    tbody.innerHTML = '<tr><td colspan="13" class="loading">Loading rankings…</td></tr>';
    meta.textContent = '';
    try {
      let url = `/api/player-rankings?period=${encodeURIComponent(period)}&season=${encodeURIComponent(season)}`;
      if (period === 'custom') {
        if (!startDate || !endDate) {
          tbody.innerHTML = '<tr><td colspan="13" class="empty">Please select a start and end date for the custom range</td></tr>';
          return;
        }
        url += `&start_date=${encodeURIComponent(startDate)}&end_date=${encodeURIComponent(endDate)}`;
      }
      const data = await fetchRankingsWithPoll(url);
      rkPlayersData = data.players || [];
      meta.textContent = `Period: ${data.period_label || period} | Season: ${season} | Players: ${rkPlayersData.length}`;
      sortAndRenderRankings();
    } catch (e) {
      tbody.innerHTML = '<tr><td colspan="13" class="empty">Failed to load rankings</td></tr>';
      console.error(e);
    }
  });

  function sortAndRenderRankings() {
    const tbody = $('#rk-tbody');
    if (!rkPlayersData.length) {
      tbody.innerHTML = '<tr><td colspan="13" class="empty">No ranking data</td></tr>';
      return;
    }

    // Filter by search term (matches first or last name, case-insensitive)
    let filtered = rkPlayersData;
    if (rkSearchTerm) {
      filtered = rkPlayersData.filter(p => {
        const name = (p.name || '').toLowerCase();
        // Match against the full name and each individual first/last token
        const tokens = name.split(/[\s'-]+/).filter(Boolean);
        return tokens.some(t => t.startsWith(rkSearchTerm));
      });
    }

    // Sort
    const sorted = [...filtered].sort((a, b) => {
      const va = a[rkSortBy] ?? 0;
      const vb = b[rkSortBy] ?? 0;
      const cmp = va < vb ? -1 : va > vb ? 1 : 0;
      return rkSortDir === 'desc' ? -cmp : cmp;
    });

    tbody.innerHTML = sorted.map((p, idx) => {
      // FPS color: green if >= 25 (avg), red if < 25
      const fpsVal = p.avg_fps ?? 0;
      const fpsClass = fpsVal >= 25 ? 'fps-good' : 'fps-bad';
      const profileUrl = p.player_id ? `/player/${p.player_id}` : '#';

      return `
      <tr>
        <td class="rk-rank num">${idx + 1}</td>
        <td class="player-name"><a href="${profileUrl}" class="player-link">${p.name}</a></td>
        <td class="team-abbr">${p.team}</td>
        <td class="rk-gms num">${p.games}</td>
        <td class="num">${p.avg_min ?? 0}</td>
        <td class="num">${p.pts}</td>
        <td class="num">${p.reb}</td>
        <td class="num">${p.ast}</td>
        <td class="num">${p.stl}</td>
        <td class="num">${p.blk}</td>
        <td class="num">${p.tov}</td>
        <td class="num usage">${p.avg_usage}%</td>
        <td class="num fps ${fpsClass}">${fmt(fpsVal)}</td>
        <td class="rk-total num">${fmt(p.total_fps, 1)}</td>
      </tr>
    `;
    }).join('');

    // Update the meta line to reflect search results
    const meta = $('#rk-meta');
    if (meta) {
      // Remove any stale "Showing" suffix first
      meta.textContent = meta.textContent.replace(/\s*\|\s*Showing \d+ of \d+ players$/, '');
      if (rkSearchTerm) {
        meta.textContent += ` | Showing ${sorted.length} of ${rkPlayersData.length} players`;
      }
    }
  }

  // Rankings table sorting via column headers
  $$('.th-sort', $('#rk-table')).forEach(th => {
    th.addEventListener('click', () => {
      const key = th.dataset.sort;
      if (rkSortBy === key) {
        rkSortDir = rkSortDir === 'desc' ? 'asc' : 'desc';
      } else {
        rkSortBy = key;
        rkSortDir = 'desc';
      }
      // Update visual indicators
      $$('.th-sort', $('#rk-table')).forEach(t => t.classList.remove('sort-asc', 'sort-desc'));
      th.classList.add(rkSortDir === 'desc' ? 'sort-desc' : 'sort-asc');
      sortAndRenderRankings();
    });
  });

  // Update sort dropdown when clicking headers (keep in sync)
  const rkSortSelect = $('#rk-sort');
  $$('.th-sort', $('#rk-table')).forEach(th => {
    th.addEventListener('click', () => {
      if (rkSortSelect) rkSortSelect.value = th.dataset.sort;
    });
  });
  // Sync when dropdown changes
  rkSortSelect?.addEventListener('change', () => {
    rkSortBy = rkSortSelect.value;
    rkSortDir = 'desc';
    sortAndRenderRankings();
    $$('.th-sort', $('#rk-table')).forEach(t => t.classList.remove('sort-asc', 'sort-desc'));
    const activeTh = $(`.th-sort[data-sort="${rkSortBy}"]`, $('#rk-table'));
    if (activeTh) activeTh.classList.add('sort-desc');
  });

  // Export rankings to CSV
  $('#rk-export')?.addEventListener('click', () => {
    if (!rkPlayersData.length) return alert('Load rankings first');
    exportRankingsCSV(rkPlayersData);
  });

  function exportRankingsCSV(players) {
    const headers = ['Rank', 'Player', 'Team', 'Gms', 'MIN', 'PTS', 'REB', 'AST', 'STL', 'BLK', 'TOV', 'Usage%', 'FPS', 'Total FPS'];
    const rows = players.map((p, idx) => [
      idx + 1,
      p.name,
      p.team,
      p.games,
      p.avg_min ?? 0,
      p.pts,
      p.reb,
      p.ast,
      p.stl,
      p.blk,
      p.tov,
      p.avg_usage,
      fmt(p.avg_fps, 1),
      fmt(p.total_fps, 1)
    ]);
    const csv = [headers.join(','), ...rows.map(r => r.map(v => `"${v}"`).join(','))].join('\n');
    const fnamePeriod = $('#rk-period').value === 'custom'
      ? `${$('#rk-start-date').value}_${$('#rk-end-date').value}`
      : $('#rk-period').value;
    downloadCSV(csv, `player-rankings-${fnamePeriod}-${$('#rk-season').value}.csv`);
  }

  // ===================================================================
  // Start/Sit
  // ===================================================================
  $('#ss-compare')?.addEventListener('click', async () => {
    const a = $('#ss-player-a').value.trim();
    const b = $('#ss-player-b').value.trim();
    if (!a || !b) return alert('Enter both player names');
    const results = $('#startsit-results');
    const verdict = $('#ss-verdict');
    const cardA = $('#ss-card-a');
    const cardB = $('#ss-card-b');
    const reasoning = $('#ss-reasoning');
    show(results);
    verdict.innerHTML = '<div class="loading">Analyzing…</div>';
    cardA.innerHTML = cardB.innerHTML = '';
    reasoning.textContent = '';
    try {
      const data = await api('/api/start-sit', { method: 'POST', body: JSON.stringify({ player_a: a, player_b: b }) });
      renderStartSit(data);
    } catch (e) { verdict.innerHTML = '<div class="empty">Failed to compare</div>'; console.error(e); }
  });

  function renderStartSit(data) {
    const verdict = $('#ss-verdict');
    const cardA = $('#ss-card-a');
    const cardB = $('#ss-card-b');
    const reasoning = $('#ss-reasoning');
    const A = data.player_a, B = data.player_b;
    const winner = data.recommendation === 'A' ? A.name : B.name;

    verdict.innerHTML = `
      <span class="ss-winner">${winner}</span>
      <span class="ss-confidence">Advantage: ${fmt(data.confidence)} FPS</span>
    `;

    function renderCard(p, id) {
      const card = $(`#ss-card-${id}`);
      card.innerHTML = `
        <h4>${p.name} (${p.team})</h4>
        <div class="ss-stat-row"><span class="ss-stat-label">7-Day Avg FPS</span><span class="ss-stat-value accent">${fmt(p.avg_fps_7)}</span></div>
        <div class="ss-stat-row"><span class="ss-stat-label">7-Day Avg Usage%</span><span class="ss-stat-value warn">${fmt(p.avg_usage_7)}%</span></div>
        <div class="ss-stat-row"><span class="ss-stat-label">Next Matchup</span><span class="ss-stat-value">${p.next_matchup || 'TBD'}</span></div>
        <div class="ss-stat-row"><span class="ss-stat-label">Opp Pace</span><span class="ss-stat-value">${fmt(p.opp_pace)}</span></div>
        <div class="ss-stat-row"><span class="ss-stat-label">Opp Def Rtg</span><span class="ss-stat-value">${fmt(p.opp_def_rating)}</span></div>
        <div class="ss-stat-row"><span class="ss-stat-label">Matchup Multiplier</span><span class="ss-stat-value ${p.matchup_multiplier > 1 ? 'good' : p.matchup_multiplier < 1 ? 'bad' : ''}">${p.matchup_multiplier}x</span></div>
        <div class="ss-stat-row"><span class="ss-stat-label">Projected FPS</span><span class="ss-stat-value accent">${fmt(p.projected_fps)}</span></div>
      `;
    }
    renderCard(A, 'a');
    renderCard(B, 'b');
    reasoning.textContent = data.reasoning;
  }

  // ===================================================================
  // Trade Analyzer
  // ===================================================================
  $('#trade-add-a')?.addEventListener('click', () => addRosterInput('trade-roster-a'));
  $('#trade-add-b')?.addEventListener('click', () => addRosterInput('trade-roster-b'));
  $$('#trade-roster-a .btn-remove, #trade-roster-b .btn-remove').forEach(btn => {
    btn.addEventListener('click', () => btn.closest('.player-row').remove());
  });

  $('#trade-analyze')?.addEventListener('click', async () => {
    const rosterA = $$('#trade-roster-a input').map(i => i.value.trim()).filter(Boolean);
    const rosterB = $$('#trade-roster-b input').map(i => i.value.trim()).filter(Boolean);
    if (!rosterA.length || !rosterB.length) return alert('Add at least one player to each roster');
    const results = $('#trade-results');
    const verdict = $('#trade-verdict');
    const sideA = $('#trade-side-a');
    const sideB = $('#trade-side-b');
    show(results);
    verdict.innerHTML = '<div class="loading">Analyzing trade…</div>';
    sideA.innerHTML = sideB.innerHTML = '';
    try {
      const data = await api('/api/trade-analyzer', { method: 'POST', body: JSON.stringify({ roster_a: rosterA, roster_b: rosterB }) });
      renderTrade(data);
    } catch (e) { verdict.innerHTML = '<div class="empty">Failed</div>'; console.error(e); }
  });

  function renderTrade(data) {
    const verdict = $('#trade-verdict');
    const sideA = $('#trade-side-a');
    const sideB = $('#trade-side-b');
    const winnerLabel = data.winner === 'A' ? 'Roster A (You Receive)' : 'Roster B (You Send)';

    verdict.innerHTML = `
      <span class="trade-winner">${winnerLabel} Wins</span>
      <span class="trade-diff">${data.difference > 0 ? '+' : ''}${fmt(data.difference)} FPS/game</span>
      <span class="trade-summary">${data.summary}</span>
    `;

    function renderSide(sideData, el, label) {
      const playersHtml = sideData.players.map(p => `
        <div class="trade-player">
          <span class="trade-player-name">${p.name}${p.error ? ' (not found)' : ''}</span>
          <span class="trade-player-fps">${p.error ? '—' : fmt(p.avg_fps)}</span>
        </div>
      `).join('');
      el.innerHTML = `
        <h4>${label}</h4>
        ${playersHtml}
        <div class="trade-total"><span>Total</span><span class="accent">${fmt(sideData.total_fps)}</span></div>
      `;
    }
    renderSide(data.roster_a, sideA, 'Roster A (Receive)');
    renderSide(data.roster_b, sideB, 'Roster B (Send)');
  }

  // ===================================================================
  // Win Projector (Full Page)
  // ===================================================================
  const EXAMPLES = { myCur: 842.5, myProj: 1312, myGP: 19, myTot: 50, oppCur: 801, oppProj: 1278, oppGP: 22, oppTot: 50, sd: 9.5, mode: "rate", W: 5, L: 3, T: 0, left: 6, avgP: 55, target: 9 };
  const IDS = Object.keys(EXAMPLES);
  const KEY = "nba-fantasy-win-projector-v1";

  const $wp = (id) => document.getElementById(id);
  function wpNum(id) { const v = parseFloat($wp(id).value); return isFinite(v) ? v : 0; }
  function wpRemaining(k) { return Math.max(0, wpNum(k + "Tot") - wpNum(k + "GP")); }

  function erf(x) {
    const s = x < 0 ? -1 : 1; x = Math.abs(x);
    const t = 1 / (1 + 0.3275911 * x);
    const y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-x * x);
    return s * y;
  }
  function normCdf(z) { return 0.5 * (1 + erf(z / Math.SQRT2)); }

  function matchup(i) {
    let myRem, oppRem;
    if (i.mode === "rate") {
      myRem = i.myTot > 0 ? i.myProj / i.myTot * Math.max(0, i.myG) : 0;
      oppRem = i.oppTot > 0 ? i.oppProj / i.oppTot * Math.max(0, i.oppG) : 0;
    } else {
      myRem = Math.max(0, i.myProj - i.myCur);
      oppRem = Math.max(0, i.oppProj - i.oppCur);
    }
    const myFinal = i.myCur + myRem, oppFinal = i.oppCur + oppRem;
    const mean = myFinal - oppFinal;
    const games = Math.max(0, i.myG) + Math.max(0, i.oppG);
    const sd = Math.sqrt(games) * i.sd;
    let p;
    if (sd === 0) p = mean > 0 ? 1 : mean < 0 ? 0 : 0.5;
    else p = normCdf(mean / sd);
    return {
      p, mean, sd, myFinal, oppFinal,
      margin: i.myCur - i.oppCur,
      need: Math.max(0, oppFinal - i.myCur),
      needPerGame: i.myG > 0 ? Math.max(0, oppFinal - i.myCur) / i.myG : null,
      projPerGame: i.myG > 0 ? myRem / i.myG : null
    };
  }

  function winDist(probs) {
    let d = [1];
    for (const p of probs) {
      const n = new Array(d.length + 1).fill(0);
      for (let k = 0; k < d.length; k++) { n[k] += d[k] * (1 - p); n[k + 1] += d[k] * p; }
      d = n;
    }
    return d;
  }

  function season(s, pThis) {
    const left = Math.max(1, Math.round(s.left));
    const probs = [pThis];
    for (let k = 1; k < left; k++) probs.push(s.avgP);
    const d = winDist(probs);
    const expWins = s.W + 0.5 * s.T;
    let sumP = 0;
    for (const prob of probs) sumP += prob;
    const total = s.W + s.L + s.T + left;
    const winsNeeded = s.target - s.W;
    let pTarget = 0;
    for (let m = 0; m < d.length; m++) if (m >= winsNeeded) pTarget += d[m];
    return {
      dist: d, left,
      expWins: s.W + sumP, expLosses: s.L + (left - sumP), ties: s.T,
      winPct: (expWins + sumP) / total, pTarget
    };
  }

  function render() {
    const m = matchup({ myCur: wpNum("myCur"), myProj: wpNum("myProj"), myG: wpRemaining("my"),
      oppCur: wpNum("oppCur"), oppProj: wpNum("oppProj"), oppG: wpRemaining("opp"), sd: wpNum("sd"),
      mode: $wp("mode").value, myTot: wpNum("myTot"), oppTot: wpNum("oppTot") });
    const pct = m.p * 100;
    $wp("myRem").textContent = wpRemaining("my") + " player-games remaining";
    $wp("oppRem").textContent = wpRemaining("opp") + " player-games remaining";
    $wp("pct").textContent = (pct >= 99.95 ? "100" : pct < 0.05 ? "0" : fmt(pct, 1)) + "%";
    $wp("barfill").style.width = pct + "%";
    const chip = $wp("chip");
    chip.className = "chip " + (m.p >= 0.6 ? "good" : m.p <= 0.4 ? "bad" : "mid");
    chip.textContent = m.p >= 0.85 ? "Strong favorite" : m.p >= 0.6 ? "Favored" : m.p > 0.4 ? "Toss-up" : m.p > 0.15 ? "Underdog" : "Long shot";

    $wp("sMargin").textContent = signed(m.margin, 1);
    $wp("sProjMargin").textContent = signed(m.mean, 1);
    $wp("sRange").innerHTML = signed(m.mean - m.sd, 0) + " to " + signed(m.mean + m.sd, 0) + " <small>final margin</small>";
    $wp("sNeed").innerHTML = fmt(m.need, 1) + (m.needPerGame !== null ? " <small>(" + fmt(m.needPerGame, 1) + " per game)</small>" : "");
    if (m.needPerGame !== null && m.projPerGame !== null) {
      const diff = m.needPerGame - m.projPerGame;
      $wp("needNote").textContent = "To pass the opponent's projected finish you need " + fmt(m.needPerGame, 1) +
        " points per remaining player-game. Your projection has you at " + fmt(m.projPerGame, 1) +
        (diff > 0 ? ", so you need to beat it by " + fmt(diff, 1) + " per game." : ", which clears the bar by " + fmt(-diff, 1) + " per game.");
    } else {
      $wp("needNote").textContent = "";
    }

    const s = season({ W: wpNum("W"), L: wpNum("L"), T: wpNum("T"), left: wpNum("left"), avgP: Math.min(1, Math.max(0, wpNum("avgP") / 100)), target: wpNum("target") }, m.p);
    const rec = fmt(s.expWins, 1) + "-" + fmt(s.expLosses, 1) + (s.ties ? "-" + s.ties : "");
    $wp("oRecord").textContent = rec;
    $wp("oPct").textContent = fmt(s.winPct * 100, 1) + "%";
    $wp("oTarget").textContent = fmt(s.pTarget * 100, 0) + "%";

    const W = wpNum("W"), need = wpNum("target") - W, max = Math.max(...s.dist);
    let h = "";
    s.dist.forEach((v, k) => {
      const hit = k >= need;
      h += '<div class="col' + (hit ? " hit" : "") + '"><em>' + (v >= 0.005 ? fmt(v * 100, 0) + "%" : "") + '</em><i style="height:' + Math.max(1, (v / max) * 100 * 0.8) + '%"></i><b>' + (W + k) + "</b></div>";
    });
    $wp("hist").innerHTML = h;
    save();
  }

  function fillInputs(vals) { IDS.forEach(id => $wp(id).value = vals[id]); }
  function save() {
    try { const o = {}; IDS.forEach(id => o[id] = $wp(id).value); localStorage.setItem(KEY, JSON.stringify(o)); } catch (e) {}
  }
  function load() {
    try { const raw = localStorage.getItem(KEY); if (raw) { const o = JSON.parse(raw); IDS.forEach(id => { if (o[id] !== undefined) $wp(id).value = o[id]; }); return true; } } catch (e) {}
    return false;
  }

  fillInputs(EXAMPLES); load();
  IDS.forEach(id => $wp(id).addEventListener("input", render));
  $wp("reset")?.addEventListener("click", () => { fillInputs(EXAMPLES); render(); });
  render();

  // ===================================================================
  // Minutes Slider - Per-36 Projection
  // ===================================================================
  let msData = null;
  let msPlayerId = null;

  setupAutocomplete('ms-player', 'ms-ac');

  async function loadMinutesProjection(pid) {
    const panel = $('#minutes-results');
    panel.hidden = true;
    try {
      const data = await api(`/api/player/${pid}/minutes-projection`);
      msData = data;
      msPlayerId = pid;
      $('#ms-header').innerHTML = `
        <div class="ms-header-row">
          <span class="ms-name">${data.player_id ? '' : ''}</span>
          <span class="ms-meta">${data.games} games · avg ${data.avg_min} MPG</span>
        </div>
        <div class="ms-baseline">Baseline FPS at ${data.avg_min} MPG: <strong>${data.baseline_fps}</strong></div>
      `;
      show(panel);
      renderMinutes();
    } catch (e) {
      console.error(e);
    }
  }

  function renderMinutes() {
    if (!msData) return;
    const mins = parseFloat($('#ms-minutes').value);
    const p36 = msData.per36;
    const s = msData.scoring;

    // Per-36 linear scaling: stat * (M / 36)
    const scale = mins / 36;
    const proj = {
      pts: p36.pts * scale,
      reb: p36.reb * scale,
      ast: p36.ast * scale,
      stl: p36.stl * scale,
      blk: p36.blk * scale,
      tov: p36.tov * scale,
    };
    const fps = s.PTS * proj.pts + s.REB * proj.reb + s.AST * proj.ast +
      s.STL * proj.stl + s.BLK * proj.blk + s.TOV * proj.tov;

    const diff = fps - msData.baseline_fps;
    const pct = msData.avg_min > 0 ? (mins / msData.avg_min * 100) : 0;

    $('#ms-fps-val').textContent = fmt(fps, 1);
    $('#ms-minutes-val').textContent = fmt(mins, 1);

    const breakdown = [
      ['PTS', proj.pts, s.PTS],
      ['REB', proj.reb, s.REB],
      ['AST', proj.ast, s.AST],
      ['STL', proj.stl, s.STL],
      ['BLK', proj.blk, s.BLK],
      ['TOV', proj.tov, s.TOV],
    ];
    $('#ms-breakdown').innerHTML = breakdown.map(([k, v, w]) => {
      const contrib = v * w;
      const cls = contrib >= 0 ? 'ms-pos' : 'ms-neg';
      return `<div class="ms-line"><span class="ms-stat">${k}</span>
        <span class="ms-val">${fmt(v, 1)}</span>
        <span class="ms-contrib ${cls}">${signed(contrib, 1)}</span></div>`;
    }).join('');

    $('#ms-stats').innerHTML = `
      <div class="ms-meta-row">
        <div class="ms-stat-tile"><div class="ms-stat-val">${fmt(mins, 1)}</div><div class="ms-stat-label">Minutes</div></div>
        <div class="ms-stat-tile"><div class="ms-stat-val">${fmt(pct, 0)}%</div><div class="ms-stat-label">vs Avg (${msData.avg_min})</div></div>
        <div class="ms-stat-tile"><div class="ms-stat-val ${diff >= 0 ? 'good' : 'bad'}">${signed(diff, 1)}</div><div class="ms-stat-label">Δ vs Baseline</div></div>
      </div>
    `;
  }

  function parseMinutesInput(val) {
    // Accept "38:51" (mm:ss) or "38.5" (decimal) or "3851" -> treat as mmss without colon
    const s = val.trim();
    if (!s) return null;
    if (s.includes(':')) {
      const [m, sec] = s.split(':').map(Number);
      if (isNaN(m) || isNaN(sec)) return null;
      return m + sec / 60;
    }
    // If 4+ digits like "3851", interpret as mmss
    if (/^\d{4,}$/.test(s)) {
      const m = parseInt(s.slice(0, -2), 10);
      const sec = parseInt(s.slice(-2), 10);
      return m + sec / 60;
    }
    const n = Number(s);
    return isNaN(n) ? null : n;
  }

  function formatMinutesForDisplay(mins) {
    // Return mm:ss for display
    const m = Math.floor(mins);
    const sec = Math.round((mins - m) * 60);
    return `${m}:${String(sec).padStart(2, '0')}`;
  }

  function syncMinutesInputs(mins) {
    const clamped = Math.max(0, Math.min(48, mins));
    const slider = $('#ms-minutes');
    const textInput = $('#ms-minutes-input');
    const output = $('#ms-minutes-val');
    slider.value = clamped;
    textInput.value = formatMinutesForDisplay(clamped);
    output.textContent = formatMinutesForDisplay(clamped);
    renderMinutes();
  }

  $('#ms-minutes').addEventListener('input', e => {
    // Only update visuals if event is from user interaction
    const mins = parseFloat(e.target.value);
    const textInput = $('#ms-minutes-input');
    const output = $('#ms-minutes-val');
    textInput.value = formatMinutesForDisplay(mins);
    output.textContent = formatMinutesForDisplay(mins);
    renderMinutes();
  });

  $('#ms-minutes-input').addEventListener('change', e => {
    const parsed = parseMinutesInput(e.target.value);
    if (parsed !== null) {
      syncMinutesInputs(parsed);
      renderMinutes();
    } else {
      // Invalid input, reset to current slider value
      syncMinutesInputs(parseFloat($('#ms-minutes').value));
    }
  });

  $('#ms-load').addEventListener('click', () => {
    const input = $('#ms-player');
    const pid = input.dataset.playerId;
    if (pid) loadMinutesProjection(parseInt(pid, 10));
  });

  // ===================================================================
  // Init
  // ===================================================================
  console.log('NBA Fantasy Command Center loaded');
})();