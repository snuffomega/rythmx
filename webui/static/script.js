/**
 * rythmx — Vanilla JS SPA
 * No frameworks. fetch() only. No build tools.
 */

const API_BASE = '/api';

// ── State ──────────────────────────────────────────────────
const state = {
    currentPage: 'discovery',
    candidates: [],
    playlist: [],
    ccStatus: {},
    filters: { newReleases: false, unownedOnly: false, highScore: false },
};

// ── Utilities ──────────────────────────────────────────────

async function api(endpoint, options = {}) {
    const resp = await fetch(`${API_BASE}${endpoint}`, {
        headers: { 'Content-Type': 'application/json', ...options.headers },
        ...options,
    });
    const data = await resp.json();
    if (data.status === 'error') throw new Error(data.message || 'API error');
    return data;
}

function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    const colors = {
        success: 'bg-accent-success',
        error:   'bg-accent-danger',
        warning: 'bg-accent-warning',
        info:    'bg-accent-primary',
    };
    const icons = { success: 'check-circle', error: 'x-circle', warning: 'alert-circle', info: 'info' };
    toast.className = `toast ${colors[type]} text-white px-5 py-3 rounded-xl shadow-lg flex items-center gap-2 text-sm`;
    toast.innerHTML = `<i data-lucide="${icons[type]}" class="w-4 h-4 shrink-0"></i><span>${message}</span>`;
    container.appendChild(toast);
    lucide.createIcons({ icons: lucide.icons, nameAttr: 'data-lucide' });
    setTimeout(() => {
        toast.classList.add('hiding');
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

function showConfirm(title, message) {
    return new Promise(resolve => {
        const modal = document.getElementById('confirm-modal');
        document.getElementById('confirm-title').textContent = title;
        document.getElementById('confirm-message').textContent = message;
        modal.classList.remove('hidden');

        function cleanup() {
            document.getElementById('confirm-ok').removeEventListener('click', onOk);
            document.getElementById('confirm-cancel').removeEventListener('click', onCancel);
            modal.classList.add('hidden');
        }
        function onOk()     { cleanup(); resolve(true);  }
        function onCancel() { cleanup(); resolve(false); }

        document.getElementById('confirm-ok').addEventListener('click', onOk);
        document.getElementById('confirm-cancel').addEventListener('click', onCancel);
    });
}

function formatDate(dateStr) {
    if (!dateStr) return 'Never';
    const d = new Date(dateStr);
    return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

// ── Navigation ──────────────────────────────────────────────

function navigateTo(page) {
    state.currentPage = page;
    document.querySelectorAll('.nav-link').forEach(l =>
        l.classList.toggle('active', l.dataset.page === page));
    document.querySelectorAll('.page').forEach(p =>
        p.classList.toggle('hidden', p.id !== `page-${page}`));
    switch (page) {
        case 'discovery':     loadDiscovery();    break;
        case 'cruise-control': loadCruiseControl(); break;
        case 'playlists':     loadPlaylists();    break;
        case 'stats':         loadStats();        break;
        case 'settings':      loadSettings();     break;
    }
    window.location.hash = page;
}

// ── Discovery ──────────────────────────────────────────────

async function loadDiscovery() {
    const grid    = document.getElementById('discovery-grid');
    const loading = document.getElementById('discovery-loading');
    const empty   = document.getElementById('discovery-empty');

    grid.classList.add('hidden');
    loading.classList.remove('hidden');
    empty.classList.add('hidden');

    try {
        const params = new URLSearchParams();
        if (state.filters.newReleases) params.set('new_releases', 'true');
        if (state.filters.unownedOnly) params.set('unowned', 'true');
        if (state.filters.highScore)   params.set('min_score', '60');

        const data = await api(`/discovery/candidates?${params}`);
        state.candidates = data.candidates || [];

        loading.classList.add('hidden');

        if (!state.candidates.length) {
            empty.classList.remove('hidden');
        } else {
            renderCandidates();
        }
    } catch (err) {
        loading.classList.add('hidden');
        empty.classList.remove('hidden');
        showToast('Failed to load discoveries', 'error');
    }

    loadPlaylist();
}

function renderCandidates() {
    const grid = document.getElementById('discovery-grid');
    grid.innerHTML = '';
    state.candidates.forEach(track => grid.appendChild(createTrackCard(track)));
    grid.classList.remove('hidden');
    lucide.createIcons({ icons: lucide.icons, nameAttr: 'data-lucide' });
}

function createTrackCard(track) {
    const card = document.createElement('div');
    card.className = 'track-card group relative bg-surface rounded-xl overflow-hidden border border-border-default cursor-pointer';

    const score     = typeof track.score === 'number' ? track.score.toFixed(1) : '—';
    const highScore = (track.score || 0) >= 60;
    const ownedCls  = track.is_owned ? 'pill-owned' : 'pill-unowned';
    const ownedTxt  = track.is_owned ? 'Owned' : 'Missing';
    const artSrc    = track.album_cover_url || '';

    card.innerHTML = `
        <div class="aspect-square relative bg-surface-highlight">
            ${artSrc
                ? `<img src="${artSrc}" alt="${escHtml(track.album_name || '')}" class="w-full h-full object-cover"
                        onerror="this.style.display='none'">`
                : `<div class="w-full h-full flex items-center justify-center text-4xl text-text-muted">♪</div>`
            }
            <!-- Hover overlay -->
            <div class="track-overlay absolute inset-0 bg-black/60 flex items-center justify-center gap-3">
                ${!track.is_owned ? `
                <button class="btn-dl p-3 rounded-full bg-accent-primary text-white hover:scale-110 transition-transform" title="Download">
                    <i data-lucide="download" class="w-5 h-5"></i>
                </button>` : ''}
                <button class="btn-add p-3 rounded-full bg-accent-secondary text-black hover:scale-110 transition-transform" title="Add to playlist">
                    <i data-lucide="plus" class="w-5 h-5"></i>
                </button>
            </div>
            <!-- Badges top-left -->
            <div class="absolute top-2 left-2 flex gap-1 flex-wrap">
                <span class="score-badge ${highScore ? 'high-score' : ''} px-2 py-0.5 rounded text-xs font-bold">${score}</span>
                ${track.is_new_release ? '<span class="pill-new px-2 py-0.5 rounded text-xs font-bold">NEW</span>' : ''}
            </div>
            <!-- Owned pill top-right -->
            <div class="absolute top-2 right-2">
                <span class="${ownedCls} px-2 py-0.5 rounded text-xs font-bold">${ownedTxt}</span>
            </div>
        </div>
        <div class="p-4">
            <h4 class="font-medium text-text-primary truncate text-sm" title="${escHtml(track.track_name || '')}">${escHtml(track.track_name || 'Unknown')}</h4>
            <p class="text-xs text-text-secondary truncate mt-0.5" title="${escHtml(track.artist_name || '')}">${escHtml(track.artist_name || '')}</p>
            <p class="text-xs text-text-muted truncate mt-0.5" title="${escHtml(track.album_name || '')}">${escHtml(track.album_name || '')}</p>
        </div>
    `;

    card.querySelector('.btn-dl')?.addEventListener('click', e => { e.stopPropagation(); downloadTrack(track); });
    card.querySelector('.btn-add').addEventListener('click',  e => { e.stopPropagation(); addToPlaylist(track);  });

    return card;
}

function escHtml(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

async function downloadTrack(track) {
    try {
        await api('/discovery/download', {
            method: 'POST',
            body: JSON.stringify({
                track_name:       track.track_name,
                artist_name:      track.artist_name,
                album_name:       track.album_name,
                spotify_track_id: track.spotify_track_id,
            }),
        });
        showToast(`Queued: ${track.track_name}`, 'success');
    } catch (err) {
        showToast(`Download failed: ${err.message}`, 'error');
    }
}

async function addToPlaylist(track) {
    try {
        await api('/discovery/playlist', {
            method: 'POST',
            body: JSON.stringify({
                track_id:         track.plex_rating_key || track.spotify_track_id,
                spotify_track_id: track.spotify_track_id,
                track_name:       track.track_name,
                artist_name:      track.artist_name,
                album_name:       track.album_name,
                album_cover_url:  track.album_cover_url,
                score:            track.score,
            }),
        });
        showToast(`Added: ${track.track_name}`, 'success');
        loadPlaylist();
    } catch (err) {
        showToast(`Failed to add to playlist`, 'error');
    }
}

async function loadPlaylist() {
    try {
        const data = await api('/discovery/playlist');
        state.playlist = data.playlist || [];
        renderPlaylist();
    } catch (_) {}
}

function renderPlaylist() {
    const container = document.getElementById('playlist-tracks');
    const empty     = document.getElementById('playlist-empty');
    const count     = document.getElementById('playlist-count');

    count.textContent = `${state.playlist.length} track${state.playlist.length === 1 ? '' : 's'}`;

    if (!state.playlist.length) {
        container.innerHTML = '';
        empty.classList.remove('hidden');
        return;
    }
    empty.classList.add('hidden');

    container.innerHTML = state.playlist.map((t, i) => `
        <div class="playlist-item flex items-center gap-3 px-2 py-1.5 rounded-lg group">
            <span class="text-xs text-text-muted w-5 shrink-0">${i + 1}</span>
            <div class="flex-1 min-w-0">
                <p class="text-sm text-text-primary truncate">${escHtml(t.track_name || '')}</p>
                <p class="text-xs text-text-secondary truncate">${escHtml(t.artist_name || '')}</p>
            </div>
            <button class="btn-rm opacity-0 group-hover:opacity-100 p-1 text-text-muted hover:text-accent-danger transition-all shrink-0"
                    data-id="${escHtml(t.track_id || '')}">
                <i data-lucide="x" class="w-4 h-4"></i>
            </button>
        </div>
    `).join('');

    container.querySelectorAll('.btn-rm').forEach(btn => {
        btn.addEventListener('click', async () => {
            const id = btn.dataset.id;
            try {
                await api(`/discovery/playlist/${encodeURIComponent(id)}`, { method: 'DELETE' });
                loadPlaylist();
            } catch (err) {
                showToast('Failed to remove', 'error');
            }
        });
    });

    lucide.createIcons({ icons: lucide.icons, nameAttr: 'data-lucide' });
}

function setupDiscoveryFilters() {
    document.querySelectorAll('.filter-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const f = btn.dataset.filter;
            if (f === 'all') {
                state.filters.newReleases = false;
                state.filters.unownedOnly = false;
                state.filters.highScore   = false;
            } else {
                if (f === 'new')        state.filters.newReleases = !state.filters.newReleases;
                if (f === 'unowned')    state.filters.unownedOnly = !state.filters.unownedOnly;
                if (f === 'high-score') state.filters.highScore   = !state.filters.highScore;
            }
            document.querySelectorAll('.filter-btn').forEach(b => {
                const bf = b.dataset.filter;
                let active = false;
                if (bf === 'all')        active = !state.filters.newReleases && !state.filters.unownedOnly && !state.filters.highScore;
                if (bf === 'new')        active = state.filters.newReleases;
                if (bf === 'unowned')    active = state.filters.unownedOnly;
                if (bf === 'high-score') active = state.filters.highScore;
                b.classList.toggle('active', active);
            });
            loadDiscovery();
        });
    });
}

function setupPlaylistButtons() {
    document.getElementById('btn-publish-plex').addEventListener('click', async () => {
        if (!state.playlist.length) { showToast('Playlist is empty', 'warning'); return; }
        try {
            await api('/discovery/publish', { method: 'POST' });
            showToast('Playlist published to Plex!', 'success');
        } catch (err) {
            showToast(`Publish failed: ${err.message}`, 'error');
        }
    });

    document.getElementById('btn-export-m3u').addEventListener('click', async () => {
        if (!state.playlist.length) { showToast('Playlist is empty', 'warning'); return; }
        try {
            const data = await api('/discovery/export', { method: 'POST' });
            const blob = new Blob([data.content], { type: 'audio/x-mpegurl' });
            const url  = URL.createObjectURL(blob);
            const a    = document.createElement('a');
            a.href = url; a.download = data.filename || 'for-you.m3u'; a.click();
            URL.revokeObjectURL(url);
            showToast('Playlist exported!', 'success');
        } catch (err) {
            showToast(`Export failed: ${err.message}`, 'error');
        }
    });
}

// ── Cruise Control ──────────────────────────────────────────

async function loadCruiseControl() {
    try {
        const [cfgData, statusData, histData] = await Promise.all([
            api('/cruise-control/config'),
            api('/cruise-control/status'),
            api('/cruise-control/history?limit=30'),
        ]);
        populateCCForm(cfgData.config || {});
        state.ccStatus = statusData;
        updateCCStatus();
        renderCCHistory(histData.history || []);
    } catch (err) {
        showToast('Failed to load cruise control data', 'error');
    }
}

function setRunMode(mode) {
    document.getElementById('cc-run-mode').value = mode;
    const descs = {
        dry:      'Scan only — no downloads or playlist saved',
        playlist: 'Scan + build playlist from owned new releases, no downloads',
        cruise:   'Scan + playlist + queue downloads for unowned releases',
    };
    const descEl = document.getElementById('cc-run-mode-desc');
    if (descEl) descEl.textContent = descs[mode] || '';
    document.querySelectorAll('.cc-mode-btn').forEach(b =>
        b.classList.toggle('active', b.dataset.mode === mode));
    const prefixRow = document.getElementById('cc-playlist-prefix-row');
    if (prefixRow) prefixRow.classList.toggle('hidden', mode === 'dry');
}

function populateCCForm(cfg) {
    const set = (id, key, fallback) => {
        const el = document.getElementById(id);
        if (!el) return;
        if (el.type === 'checkbox') el.checked = cfg[key] === 'true';
        else el.value = cfg[key] ?? fallback;
    };
    set('cc-enabled',    'cc_enabled',           false);
    set('cc-min-listens','cc_min_listens',        '10');
    set('cc-period',     'cc_period',             '6month');
    set('cc-lookback',   'cc_lookback_days',      '90');
    set('cc-max',        'cc_max_per_cycle',      '10');
    set('cc-auto-push',  'cc_auto_push_playlist', false);
    // Run mode toggle
    setRunMode(cfg['cc_run_mode'] || 'playlist');
    // Playlist prefix
    const prefixEl = document.getElementById('cc-playlist-prefix');
    if (prefixEl) prefixEl.value = cfg['cc_playlist_prefix'] || 'New Music';
}

function updateCCStatus() {
    const s = state.ccStatus;
    const r = s.last_result || {};

    document.getElementById('cc-last-run').textContent = formatDate(s.last_run);

    const dash = v => (v !== undefined && v !== null) ? String(v) : '—';
    document.getElementById('cc-considered').textContent = dash(r.releases_found);
    document.getElementById('cc-owned').textContent      = dash(r.releases_owned);
    document.getElementById('cc-queued').textContent     = dash(r.queued);
    document.getElementById('cc-failed').textContent     = dash(r.failed);
    document.getElementById('cc-provider').textContent   = r.provider || '—';

    // Stage icons
    document.querySelectorAll('#pipeline-stages .stage').forEach(stage => {
        stage.classList.remove('completed', 'active', 'failed');
        const icon = stage.querySelector('.stage-icon');
        icon.setAttribute('data-lucide', 'circle');
        if (s.is_running && s.current_stage) {
            const n = parseInt(stage.dataset.stage);
            if (n < s.current_stage) {
                stage.classList.add('completed');
                icon.setAttribute('data-lucide', 'check-circle');
            } else if (n === s.current_stage) {
                stage.classList.add('active');
                icon.setAttribute('data-lucide', 'loader-2');
            }
        }
    });

    // Run Now button state
    const runBtn = document.getElementById('btn-run-now');
    if (s.is_running) {
        runBtn.disabled = true;
        runBtn.innerHTML = `<i data-lucide="loader-2" class="w-4 h-4 animate-spin"></i> Running...`;
    } else {
        runBtn.disabled = false;
        runBtn.innerHTML = `<i data-lucide="play" class="w-4 h-4"></i> Run Now`;
    }

    lucide.createIcons({ icons: lucide.icons, nameAttr: 'data-lucide' });
}

function renderCCHistory(history) {
    const tbody = document.getElementById('cc-history-table');
    const empty = document.getElementById('cc-history-empty');

    if (!history.length) {
        tbody.innerHTML = '';
        empty.classList.remove('hidden');
        return;
    }
    empty.classList.add('hidden');

    const statusColor = {
        queued:  'text-accent-primary',
        success: 'text-accent-success',
        failed:  'text-accent-danger',
        skipped: 'text-text-muted',
        dry_run: 'text-accent-warning',
    };

    tbody.innerHTML = history.slice(0, 30).map(e => `
        <tr class="text-sm">
            <td class="py-3 pr-4 text-text-primary">${escHtml(e.artist_name || '—')}</td>
            <td class="py-3 pr-4 text-text-secondary">${escHtml(e.album_name || e.track_name || '—')}</td>
            <td class="py-3 pr-4 text-text-muted font-mono text-xs">${escHtml(e.source || '—')}</td>
            <td class="py-3 pr-4 ${statusColor[e.acquisition_status] || ''}">${escHtml(e.acquisition_status || '—')}</td>
            <td class="py-3 text-text-muted">${formatDate(e.cycle_date)}</td>
        </tr>
    `).join('');
}

function setupCCForm() {
    // Run mode toggle buttons
    document.querySelectorAll('.cc-mode-btn').forEach(btn => {
        btn.addEventListener('click', () => setRunMode(btn.dataset.mode));
    });

    document.getElementById('cc-config-form').addEventListener('submit', async e => {
        e.preventDefault();
        const form    = e.target;
        const payload = {};
        new FormData(form).forEach((v, k) => { payload[k] = v; });
        // Checkboxes missing from FormData when unchecked
        ['cc_enabled', 'cc_auto_push_playlist'].forEach(k => {
            payload[k] = form.querySelector(`[name="${k}"]`)?.checked ? 'true' : 'false';
        });
        try {
            await api('/cruise-control/config', { method: 'POST', body: JSON.stringify(payload) });
            showToast('Configuration saved!', 'success');
        } catch (err) {
            showToast(`Save failed: ${err.message}`, 'error');
        }
    });

    document.getElementById('btn-run-now').addEventListener('click', async () => {
        const runMode = document.getElementById('cc-run-mode')?.value || 'playlist';
        try {
            await api('/cruise-control/run-now', {
                method: 'POST',
                body: JSON.stringify({ run_mode: runMode }),
            });
            showToast('Cycle started!', 'success');

            // Poll status every 2s while running
            const poll = setInterval(async () => {
                try {
                    state.ccStatus = await api('/cruise-control/status');
                    updateCCStatus();
                    if (!state.ccStatus.is_running) {
                        clearInterval(poll);
                        await loadCruiseControl();
                        const result = state.ccStatus.last_result || {};
                        if (result.playlist_name) {
                            showToast(
                                `Playlist '${result.playlist_name}' ready — ${result.playlist_tracks || 0} tracks`,
                                'success'
                            );
                            navigateTo('playlists');
                        } else {
                            showToast('Cycle complete!', 'success');
                        }
                    }
                } catch (_) { clearInterval(poll); }
            }, 2000);
        } catch (err) {
            showToast(`Failed to start: ${err.message}`, 'error');
        }
    });
}

// ── Playlists ───────────────────────────────────────────────

async function loadPlaylists() {
    const loading = document.getElementById('playlists-loading');
    const empty   = document.getElementById('playlists-empty');
    const list    = document.getElementById('playlists-list');

    list.classList.add('hidden');
    empty.classList.add('hidden');
    loading.classList.remove('hidden');

    try {
        const data = await api('/playlists');
        const playlists = data.playlists || [];
        loading.classList.add('hidden');
        if (!playlists.length) {
            empty.classList.remove('hidden');
        } else {
            list.innerHTML = '';
            playlists.forEach(pl => list.appendChild(buildPlaylistCard(pl)));
            list.classList.remove('hidden');
            lucide.createIcons({ icons: lucide.icons, nameAttr: 'data-lucide' });
        }
    } catch (err) {
        loading.classList.add('hidden');
        showToast('Failed to load playlists', 'error');
    }
}

function buildPlaylistCard(pl) {
    const card = document.createElement('div');
    card.className = 'bg-surface rounded-2xl border border-border-default p-6';

    const coverage = pl.track_count > 0
        ? Math.round((pl.owned_count / pl.track_count) * 100)
        : 0;

    const sourceColors = {
        taste: 'text-accent-primary',
        spotify: 'text-accent-success',
        lastfm: 'text-orange-400',
        deezer: 'text-purple-400',
        cc: 'text-text-muted',
        manual: 'text-text-muted',
    };
    const sourceColor  = sourceColors[pl.source] || 'text-text-muted';

    const lastSynced = pl.last_synced_ts
        ? formatDate(new Date(pl.last_synced_ts * 1000).toISOString())
        : null;

    const syncedLabel = lastSynced
        ? `<span class="text-text-muted text-xs">Last synced: ${lastSynced}</span>`
        : (pl.created_at ? `<span class="text-text-muted text-xs">Created: ${formatDate(pl.created_at)}</span>` : '');

    const autoSyncBadge = pl.source !== 'manual'
        ? `<button class="btn-toggle-sync px-2 py-0.5 rounded-full text-xs border transition-all ${pl.auto_sync ? 'border-accent-success text-accent-success' : 'border-border-default text-text-muted hover:border-accent-success'}"
                   title="Toggle auto-sync" data-name="${escHtml(pl.name)}" data-sync="${pl.auto_sync ? '1' : '0'}">
                auto-sync ${pl.auto_sync ? '●' : '○'}
           </button>`
        : '';

    // Primary action buttons based on source
    const primaryActions = pl.source === 'taste'
        ? `<button class="btn-rebuild px-4 py-2 rounded-xl bg-surface-highlight border border-border-default text-text-primary text-sm hover:border-accent-primary transition-all flex items-center gap-2"
                   data-name="${escHtml(pl.name)}">
               <i data-lucide="refresh-cw" class="w-4 h-4"></i>Rebuild
           </button>`
        : ['spotify', 'lastfm', 'deezer'].includes(pl.source)
        ? `<button class="btn-sync px-4 py-2 rounded-xl bg-surface-highlight border border-border-default text-text-primary text-sm hover:border-accent-primary transition-all flex items-center gap-2"
                   data-name="${escHtml(pl.name)}">
               <i data-lucide="refresh-cw" class="w-4 h-4"></i>Sync Now
           </button>`
        : '';

    card.innerHTML = `
        <div class="flex items-start justify-between mb-4">
            <div>
                <h3 class="font-display text-xl font-semibold text-text-primary">${escHtml(pl.name)}</h3>
                <div class="flex items-center gap-3 mt-1">
                    <span class="text-xs font-medium ${sourceColor}">[${escHtml(pl.source)}]</span>
                    ${autoSyncBadge}
                    ${syncedLabel}
                </div>
            </div>
            <button class="btn-delete-pl text-text-muted hover:text-accent-danger transition-colors p-1"
                    data-name="${escHtml(pl.name)}" title="Delete playlist">
                <i data-lucide="trash-2" class="w-4 h-4"></i>
            </button>
        </div>

        <div class="flex items-center gap-6 mb-4 text-sm">
            <span class="text-text-secondary">${pl.track_count} track${pl.track_count === 1 ? '' : 's'}</span>
            <span class="text-text-secondary">${pl.owned_count} owned
                <span class="text-text-muted">(${coverage}%)</span>
            </span>
            <div class="flex-1 h-1.5 bg-surface-highlight rounded-full overflow-hidden">
                <div class="h-full bg-accent-success rounded-full" style="width:${coverage}%"></div>
            </div>
        </div>

        <!-- Expandable track list (all playlists with tracks) -->
        ${pl.track_count > 0
            ? `<details class="mb-4">
                   <summary class="text-sm text-text-secondary cursor-pointer hover:text-text-primary transition-colors">
                       Track list (${pl.track_count})
                   </summary>
                   <div class="mt-3 max-h-64 overflow-y-auto space-y-1 playlist-tracks-container">
                       <div class="text-text-muted text-xs py-2">Loading tracks...</div>
                   </div>
               </details>`
            : ''
        }

        <div class="flex flex-wrap gap-2 pt-4 border-t border-border-default">
            ${primaryActions}
            <button class="btn-publish px-4 py-2 rounded-xl bg-accent-primary text-white text-sm hover:bg-opacity-90 transition-all flex items-center gap-2"
                    data-name="${escHtml(pl.name)}">
                <i data-lucide="upload-cloud" class="w-4 h-4"></i>Push to Plex
            </button>
            <button class="btn-export px-4 py-2 rounded-xl bg-surface-highlight border border-border-default text-text-primary text-sm hover:border-accent-primary transition-all flex items-center gap-2"
                    data-name="${escHtml(pl.name)}">
                <i data-lucide="download" class="w-4 h-4"></i>Export M3U
            </button>
        </div>
    `;

    // Wire actions
    card.querySelector('.btn-delete-pl').addEventListener('click', async () => {
        const ok = await showConfirm('Delete Playlist', `Delete "${pl.name}" and all its tracks?`);
        if (!ok) return;
        try {
            await api(`/playlists/${encodeURIComponent(pl.name)}`, { method: 'DELETE' });
            showToast('Playlist deleted', 'success');
            loadPlaylists();
        } catch (err) {
            showToast(`Delete failed: ${err.message}`, 'error');
        }
    });

    card.querySelector('.btn-publish').addEventListener('click', async () => {
        try {
            await api(`/playlists/${encodeURIComponent(pl.name)}/publish`, { method: 'POST' });
            showToast(`"${pl.name}" pushed to Plex!`, 'success');
        } catch (err) {
            showToast(`Publish failed: ${err.message}`, 'error');
        }
    });

    card.querySelector('.btn-export').addEventListener('click', async () => {
        try {
            const data = await api(`/playlists/${encodeURIComponent(pl.name)}/export`, { method: 'POST' });
            const blob = new Blob([data.content], { type: 'audio/x-mpegurl' });
            const url  = URL.createObjectURL(blob);
            const a    = document.createElement('a');
            a.href = url; a.download = data.filename; a.click();
            URL.revokeObjectURL(url);
            showToast('Exported!', 'success');
        } catch (err) {
            showToast(`Export failed: ${err.message}`, 'error');
        }
    });

    card.querySelector('.btn-rebuild')?.addEventListener('click', async () => {
        const btn = card.querySelector('.btn-rebuild');
        btn.disabled = true;
        btn.innerHTML = `<i data-lucide="loader-2" class="w-4 h-4 animate-spin"></i> Building...`;
        lucide.createIcons({ icons: lucide.icons, nameAttr: 'data-lucide' });
        try {
            const data = await api(`/playlists/${encodeURIComponent(pl.name)}/build`, { method: 'POST' });
            showToast(`Built ${data.track_count} tracks (${data.owned_count} owned)`, 'success');
            loadPlaylists();
        } catch (err) {
            showToast(`Build failed: ${err.message}`, 'error');
            btn.disabled = false;
            btn.innerHTML = `<i data-lucide="refresh-cw" class="w-4 h-4"></i> Rebuild`;
            lucide.createIcons({ icons: lucide.icons, nameAttr: 'data-lucide' });
        }
    });

    card.querySelector('.btn-sync')?.addEventListener('click', async () => {
        const btn = card.querySelector('.btn-sync');
        btn.disabled = true;
        btn.innerHTML = `<i data-lucide="loader-2" class="w-4 h-4 animate-spin"></i> Syncing...`;
        lucide.createIcons({ icons: lucide.icons, nameAttr: 'data-lucide' });
        try {
            const data = await api(`/playlists/${encodeURIComponent(pl.name)}/import`, { method: 'POST' });
            showToast(`Synced ${data.track_count} tracks (${data.owned_count} owned)`, 'success');
            loadPlaylists();
        } catch (err) {
            showToast(`Sync failed: ${err.message}`, 'error');
            btn.disabled = false;
            btn.innerHTML = `<i data-lucide="refresh-cw" class="w-4 h-4"></i> Sync Now`;
            lucide.createIcons({ icons: lucide.icons, nameAttr: 'data-lucide' });
        }
    });

    card.querySelector('.btn-toggle-sync')?.addEventListener('click', async () => {
        const btn = card.querySelector('.btn-toggle-sync');
        const newSync = btn.dataset.sync === '0';
        try {
            await api(`/playlists/${encodeURIComponent(pl.name)}/settings`, {
                method: 'POST',
                body: JSON.stringify({ auto_sync: newSync }),
            });
            loadPlaylists();
        } catch (err) {
            showToast(`Failed: ${err.message}`, 'error');
        }
    });

    // Lazy-load track list when details is opened
    const details = card.querySelector('details');
    if (details) {
        details.addEventListener('toggle', async () => {
            if (!details.open) return;
            const container = card.querySelector('.playlist-tracks-container');
            if (!container || container.dataset.loaded) return;
            try {
                const data = await api(`/playlists/${encodeURIComponent(pl.name)}/tracks`);
                const tracks = data.tracks || [];
                container.innerHTML = tracks.map(t => `
                    <div class="flex items-center gap-2 py-1 text-xs">
                        <span class="${t.track_id ? 'text-accent-success' : 'text-text-muted'}">
                            ${t.track_id ? '●' : '○'}
                        </span>
                        <span class="text-text-primary truncate">${escHtml(t.artist_name || '')} – ${escHtml(t.track_name || '')}</span>
                    </div>
                `).join('') || '<p class="text-text-muted text-xs py-2">No tracks</p>';
                container.dataset.loaded = '1';
            } catch (_) {
                container.innerHTML = '<p class="text-accent-danger text-xs py-2">Failed to load tracks</p>';
            }
        });
    }

    return card;
}

function setupPlaylistsPage() {
    const modal    = document.getElementById('new-playlist-modal');
    const backdrop = document.getElementById('new-playlist-backdrop');
    const closeBtn = document.getElementById('close-playlist-modal');
    const form     = document.getElementById('new-playlist-form');
    const errEl    = document.getElementById('pl-create-error');

    // Open modal
    document.getElementById('btn-new-playlist').addEventListener('click', () => {
        form.reset();
        document.getElementById('pl-import-fields').classList.add('hidden');
        document.getElementById('pl-taste-fields').classList.remove('hidden');
        errEl.classList.add('hidden');
        const createBtn = document.getElementById('btn-create-playlist');
        createBtn.disabled = false;
        createBtn.innerHTML = `<i data-lucide="plus" class="w-4 h-4"></i> Create Playlist`;
        modal.classList.remove('hidden');
        lucide.createIcons({ icons: lucide.icons, nameAttr: 'data-lucide' });
    });

    // Close modal
    function closeModal() { modal.classList.add('hidden'); }
    closeBtn.addEventListener('click', closeModal);
    backdrop.addEventListener('click', closeModal);

    // Show/hide import URL fields and max-tracks field based on source selection
    const urlPlaceholders = {
        spotify: 'https://open.spotify.com/playlist/...',
        lastfm:  'https://www.last.fm/user/username/playlists/12345678',
        deezer:  'https://www.deezer.com/playlist/...',
    };
    const importSources = new Set(['spotify', 'lastfm', 'deezer']);
    form.querySelectorAll('input[name="pl-source"]').forEach(radio => {
        radio.addEventListener('change', () => {
            const isImport = importSources.has(radio.value);
            const isTaste  = radio.value === 'taste';
            document.getElementById('pl-import-fields').classList.toggle('hidden', !isImport);
            document.getElementById('pl-taste-fields').classList.toggle('hidden', !isTaste);
            if (isImport && urlPlaceholders[radio.value]) {
                document.getElementById('pl-source-url').placeholder = urlPlaceholders[radio.value];
            }
        });
    });

    // Submit form
    form.addEventListener('submit', async e => {
        e.preventDefault();
        errEl.classList.add('hidden');

        const name      = document.getElementById('pl-name').value.trim();
        const source    = form.querySelector('input[name="pl-source"]:checked')?.value || 'taste';
        const sourceUrl = document.getElementById('pl-source-url').value.trim();
        const autoSync  = document.getElementById('pl-auto-sync').checked;

        if (!name) { errEl.textContent = 'Name is required'; errEl.classList.remove('hidden'); return; }
        if (['spotify', 'lastfm', 'deezer'].includes(source) && !sourceUrl) {
            errEl.textContent = 'Playlist URL is required';
            errEl.classList.remove('hidden');
            return;
        }

        const btn = document.getElementById('btn-create-playlist');
        btn.disabled = true;
        btn.innerHTML = `<i data-lucide="loader-2" class="w-4 h-4 animate-spin"></i> Creating...`;
        lucide.createIcons({ icons: lucide.icons, nameAttr: 'data-lucide' });

        try {
            const maxTracks = parseInt(document.getElementById('pl-max-tracks').value, 10) || 50;
            const data = await api('/playlists', {
                method: 'POST',
                body: JSON.stringify({ name, source, source_url: sourceUrl || null,
                                       auto_sync: autoSync, max_tracks: maxTracks }),
            });
            btn.disabled = false;
            btn.innerHTML = `<i data-lucide="plus" class="w-4 h-4"></i> Create Playlist`;
            closeModal();
            showToast(`"${name}" created (${data.track_count || 0} tracks)`, 'success');
            loadPlaylists();
        } catch (err) {
            errEl.textContent = err.message;
            errEl.classList.remove('hidden');
            btn.disabled = false;
            btn.innerHTML = `<i data-lucide="plus" class="w-4 h-4"></i> Create Playlist`;
            lucide.createIcons({ icons: lucide.icons, nameAttr: 'data-lucide' });
        }
    });
}

// ── Stats ──────────────────────────────────────────────────

async function loadStats() {
    const period = document.getElementById('stats-period').value;

    try {
        const [artistsData, tracksData, summaryData] = await Promise.all([
            api(`/stats/top-artists?period=${period}&limit=20`),
            api(`/stats/top-tracks?period=${period}&limit=20`),
            api('/stats/summary'),
        ]);

        renderTopArtists(artistsData.artists || []);
        document.getElementById('stat-artists').textContent = (artistsData.artists || []).length;

        renderTopTracks(tracksData.tracks || []);
        document.getElementById('stat-tracks').textContent = (tracksData.tracks || []).length;

        const summary = summaryData.summary || {};
        document.getElementById('stat-downloaded').textContent = summary.queued || 0;
        renderCCSummary(summary);

        // Loved artists count (best effort)
        try {
            const lovedData = await api('/stats/loved-artists');
            document.getElementById('stat-loved').textContent = lovedData.count || 0;
        } catch (_) {}

    } catch (err) {
        showToast('Failed to load stats', 'error');
    }
}

function renderTopArtists(artists) {
    const container = document.getElementById('top-artists-list');
    const empty     = document.getElementById('top-artists-empty');

    if (!artists.length) {
        container.innerHTML = '';
        empty.classList.remove('hidden');
        return;
    }
    empty.classList.add('hidden');

    const maxPlays = artists[0]?.playcount || 1;
    container.innerHTML = artists.map((a, i) => `
        <div class="artist-row flex items-center gap-4 p-2 rounded-lg">
            <span class="text-lg font-bold text-text-muted w-6 shrink-0">${i + 1}</span>
            <div class="flex-1 min-w-0">
                <p class="text-text-primary font-medium truncate">${escHtml(a.artist || a.name || '')}</p>
                <p class="text-xs text-text-secondary">${(a.playcount || 0).toLocaleString()} plays</p>
            </div>
            <div class="w-24 h-2 bg-surface-highlight rounded-full overflow-hidden shrink-0">
                <div class="h-full bg-accent-primary rounded-full progress-bar" style="width:${Math.round((a.playcount / maxPlays) * 100)}%"></div>
            </div>
        </div>
    `).join('');
}

function renderTopTracks(tracks) {
    const container = document.getElementById('top-tracks-list');
    const empty     = document.getElementById('top-tracks-empty');

    if (!tracks.length) {
        container.innerHTML = '';
        empty.classList.remove('hidden');
        return;
    }
    empty.classList.add('hidden');

    const maxPlays = tracks[0]?.playcount || 1;
    container.innerHTML = tracks.map((t, i) => `
        <div class="track-row flex items-center gap-4 p-2 rounded-lg">
            <span class="text-lg font-bold text-text-muted w-6 shrink-0">${i + 1}</span>
            <div class="flex-1 min-w-0">
                <p class="text-text-primary font-medium truncate">${escHtml(t.name || t.track_name || '')}</p>
                <p class="text-xs text-text-secondary truncate">${escHtml(t.artist || t.artist_name || '')}</p>
            </div>
            <div class="w-24 h-2 bg-surface-highlight rounded-full overflow-hidden shrink-0">
                <div class="h-full bg-accent-secondary rounded-full progress-bar" style="width:${Math.round(((t.playcount || 0) / maxPlays) * 100)}%"></div>
            </div>
        </div>
    `).join('');
}

function renderCCSummary(summary) {
    const container = document.getElementById('cc-summary-body');
    const empty     = document.getElementById('cc-summary-empty');

    if (!summary || !Object.keys(summary).length) {
        container.innerHTML = '';
        empty?.classList.remove('hidden');
        return;
    }
    empty?.classList.add('hidden');

    const items = [
        { label: 'Total',   value: summary.total   || 0, color: 'text-text-primary' },
        { label: 'Queued',  value: summary.queued  || 0, color: 'text-accent-primary' },
        { label: 'Success', value: summary.success || 0, color: 'text-accent-success' },
        { label: 'Failed',  value: summary.failed  || 0, color: 'text-accent-danger' },
        { label: 'Skipped', value: summary.skipped || 0, color: 'text-text-muted' },
    ];
    container.innerHTML = items.map(item => `
        <div class="bg-surface-highlight rounded-xl p-4 text-center">
            <p class="text-text-muted text-xs mb-1">${item.label}</p>
            <p class="font-display text-2xl font-bold ${item.color}">${item.value}</p>
        </div>
    `).join('');
}

function setupStatsPage() {
    document.getElementById('stats-period').addEventListener('change', loadStats);
}

// ── Settings ────────────────────────────────────────────────

async function loadSettings() {
    try {
        const data = await api('/settings');
        setStatus('lastfm-status',
            data.lastfm_configured
                ? `Configured as <strong>${data.lastfm_username}</strong>`
                : 'Not configured — set LASTFM_API_KEY + LASTFM_USERNAME in .env',
            data.lastfm_configured);
        setStatus('plex-status',
            data.plex_configured
                ? `Configured (${data.plex_url})`
                : 'Not configured — set PLEX_URL + PLEX_TOKEN in .env',
            data.plex_configured);
        setStatus('soulsync-status',
            data.soulsync_db_accessible
                ? `DB accessible`
                : 'DB not accessible — check SOULSYNC_DB path',
            data.soulsync_db_accessible);
    } catch (_) {}
}

function setStatus(elId, html, ok) {
    const el = document.getElementById(elId);
    if (!el) return;
    el.innerHTML = html;
    el.className = `text-sm mb-4 min-h-5 ${ok ? 'status-connected' : 'status-disconnected'}`;
}

function setupSettingsPage() {
    // Test connections
    document.getElementById('btn-test-lastfm').addEventListener('click', async () => {
        const el = document.getElementById('lastfm-status');
        el.innerHTML = 'Testing...'; el.className = 'text-sm mb-4 min-h-5 text-text-muted';
        try {
            const r = await api('/settings/test-lastfm', { method: 'POST' });
            setStatus('lastfm-status', `Connected as <strong>${r.username || '?'}</strong>`, true);
        } catch (err) {
            setStatus('lastfm-status', `Failed: ${err.message}`, false);
        }
    });

    document.getElementById('btn-test-plex').addEventListener('click', async () => {
        const el = document.getElementById('plex-status');
        el.innerHTML = 'Testing...'; el.className = 'text-sm mb-4 min-h-5 text-text-muted';
        try {
            const r = await api('/settings/test-plex', { method: 'POST' });
            setStatus('plex-status', `Connected${r.server_name ? ' to ' + r.server_name : ''}`, true);
        } catch (err) {
            setStatus('plex-status', `Failed: ${err.message}`, false);
        }
    });

    document.getElementById('btn-test-soulsync').addEventListener('click', async () => {
        const el = document.getElementById('soulsync-status');
        el.innerHTML = 'Testing...'; el.className = 'text-sm mb-4 min-h-5 text-text-muted';
        try {
            const r = await api('/settings/test-soulsync', { method: 'POST' });
            const dbIcon  = r.db_available            ? '✓ DB' : '✗ DB';
            const apiIcon = r.api_status?.status === 'ok' ? '✓ API' : '✗ API';
            const ok = r.db_available || r.api_status?.status === 'ok';
            setStatus('soulsync-status', `${dbIcon} &nbsp;|&nbsp; ${apiIcon}`, ok);
        } catch (err) {
            setStatus('soulsync-status', `Failed: ${err.message}`, false);
        }
    });

    document.getElementById('btn-test-spotify').addEventListener('click', async () => {
        const el = document.getElementById('spotify-status');
        el.innerHTML = 'Testing...'; el.className = 'text-sm mb-4 min-h-5 text-text-muted';
        try {
            const r = await api('/settings/test-spotify', { method: 'POST' });
            setStatus('spotify-status', 'Connected', true);
        } catch (err) {
            setStatus('spotify-status', `Failed: ${err.message}`, false);
        }
    });

    // Danger zone
    document.getElementById('btn-clear-history').addEventListener('click', async () => {
        const ok = await showConfirm('Clear History',
            'This will delete all cycle history. This cannot be undone.');
        if (!ok) return;
        try {
            await api('/settings/clear-history', { method: 'POST' });
            showToast('History cleared', 'success');
        } catch (err) {
            showToast(`Failed: ${err.message}`, 'error');
        }
    });

    document.getElementById('btn-reset-db').addEventListener('click', async () => {
        const ok = await showConfirm('Reset Database',
            'This will delete ALL data — playlists, history, settings, and cache. This cannot be undone.');
        if (!ok) return;
        try {
            await api('/settings/reset-db', { method: 'POST' });
            showToast('Database reset', 'success');
        } catch (err) {
            showToast(`Failed: ${err.message}`, 'error');
        }
    });
}

// ── Init ─────────────────────────────────────────────────────

function init() {
    lucide.createIcons({ icons: lucide.icons, nameAttr: 'data-lucide' });

    document.querySelectorAll('.nav-link').forEach(link => {
        link.addEventListener('click', e => {
            e.preventDefault();
            navigateTo(link.dataset.page);
        });
    });

    setupDiscoveryFilters();
    setupPlaylistButtons();
    setupCCForm();
    setupPlaylistsPage();
    setupStatsPage();
    setupSettingsPage();

    const initial = window.location.hash.slice(1) || 'discovery';
    navigateTo(initial);

    window.addEventListener('hashchange', () => {
        const page = window.location.hash.slice(1) || 'discovery';
        if (page !== state.currentPage) navigateTo(page);
    });

    // Keep CC status indicator current when on that page
    setInterval(async () => {
        if (state.currentPage === 'cruise-control') {
            try {
                state.ccStatus = await api('/cruise-control/status');
                updateCCStatus();
            } catch (_) {}
        }
    }, 10000);
}

document.addEventListener('DOMContentLoaded', init);
