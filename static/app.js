// ============================================================================
// LV Arbitrage frontend
// Vanilla JS, žádný build step, načítá vše z /api/*
// ============================================================================

const API = '/api';
const PAGE_SIZE = 50;

const state = {
    category: 'all',          // all | opportunities | favorites | dismissed
    sort: 'arbitrage_desc',
    source: '',
    model: '',
    minArbitrage: 0,
    maxRisk: 0.55,
    offset: 0,
    items: [],
    total: 0,
    meta: null,
};

// ============================================================================
// API
// ============================================================================
async function apiGet(path) {
    const res = await fetch(API + path);
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    return res.json();
}

async function apiPost(path) {
    const res = await fetch(API + path, { method: 'POST' });
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    return res.json();
}

function buildListingsQuery() {
    const params = new URLSearchParams();
    params.set('limit', PAGE_SIZE);
    params.set('offset', state.offset);
    params.set('sort', state.sort);

    if (state.source) params.set('source', state.source);
    if (state.model) params.set('model', state.model);
    if (state.minArbitrage > 0) params.set('min_arbitrage_pct', state.minArbitrage);
    if (state.maxRisk < 1.0) params.set('max_counterfeit_risk', state.maxRisk);

    if (state.category === 'opportunities') {
        params.set('min_arbitrage_pct', Math.max(state.minArbitrage, 0.20));
        params.set('exclude_dismissed', 'true');
    } else if (state.category === 'favorites') {
        params.set('favorites_only', 'true');
        params.set('exclude_dismissed', 'false');
    } else if (state.category === 'dismissed') {
        params.set('exclude_dismissed', 'false');
    }

    return params.toString();
}

// ============================================================================
// Rendering
// ============================================================================
function fmtPrice(czk) {
    if (!czk) return '—';
    return new Intl.NumberFormat('cs-CZ').format(czk) + ' Kč';
}

function fmtPercent(pct) {
    if (pct === null || pct === undefined) return '—';
    return Math.round(pct * 100) + ' %';
}

function modelDisplayName(modelId) {
    if (!state.meta || !modelId) return modelId || '';
    const m = state.meta.models.find(x => x.id === modelId);
    return m ? m.display_name : modelId;
}

function renderCard(item) {
    const photo = item.photo_urls && item.photo_urls.length
        ? item.photo_urls[0]
        : 'data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="280" height="200"><rect fill="%23f0f0f0" width="280" height="200"/><text x="50%" y="50%" font-family="sans-serif" font-size="14" fill="%23999" text-anchor="middle" dy=".3em">bez fotky</text></svg>';

    const badges = [];
    if (item.arbitrage_pct >= 0.30) {
        badges.push(`<span class="badge badge-deal-strong">−${Math.round(item.arbitrage_pct * 100)}%</span>`);
    } else if (item.arbitrage_pct >= 0.15) {
        badges.push(`<span class="badge badge-deal">−${Math.round(item.arbitrage_pct * 100)}%</span>`);
    }
    if (item.counterfeit_risk >= 0.6) {
        badges.push(`<span class="badge badge-risk">⚠ riziko</span>`);
    } else if (item.counterfeit_risk >= 0.35) {
        badges.push(`<span class="badge badge-risk-medium">? ověřit</span>`);
    }

    const favClass = item.is_favorite ? 'icon-btn active' : 'icon-btn';

    return `
        <div class="card" data-id="${item.id}">
            <div class="card-photo" style="background-image: url('${photo}')">
                <div class="card-badges">${badges.join('')}</div>
                <div class="card-actions">
                    <button class="${favClass}" data-action="favorite" title="Oblíbit">♡</button>
                    <button class="icon-btn" data-action="dismiss" title="Vyřadit">✕</button>
                </div>
            </div>
            <div class="card-body">
                <div class="card-model">${modelDisplayName(item.model_normalized)} · ${item.source}</div>
                <div class="card-title">${escapeHtml(item.title || '(bez názvu)')}</div>
                <div class="card-prices">
                    <span class="card-price">${fmtPrice(item.price_czk)}</span>
                    ${item.fair_value_czk ? `<span class="card-fair">${fmtPrice(item.fair_value_czk)}</span>` : ''}
                </div>
                <div class="card-meta">
                    <span>${item.condition_raw || '—'}</span>
                    <span>${item.location || ''}</span>
                </div>
            </div>
        </div>
    `;
}

function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function renderResults(append = false) {
    const main = document.getElementById('results');
    if (!append) main.innerHTML = '';

    if (state.items.length === 0) {
        main.innerHTML = '<div class="empty-state">Žádné inzeráty pro aktuální filtry.<br><br>Zkus změnit kategorii nebo počkej na příští denní scrape.</div>';
        document.getElementById('load-more').style.display = 'none';
        return;
    }

    const html = state.items.map(renderCard).join('');
    if (append) {
        main.insertAdjacentHTML('beforeend', html);
    } else {
        main.innerHTML = html;
    }

    document.getElementById('load-more').style.display =
        state.items.length < state.total ? 'block' : 'none';
}

function renderStats(stats) {
    const bar = document.getElementById('stats-bar');
    const lastRun = stats.recent_runs[0];
    const lastRunStr = lastRun
        ? `Poslední scrape: ${new Date(lastRun.finished_at).toLocaleString('cs-CZ')}`
        : 'Zatím žádný scrape';
    bar.innerHTML = `${stats.total_active} aktivních inzerátů · ${stats.opportunities_count} příležitostí · ${lastRunStr}`;
}

function renderModelFilter(models) {
    const container = document.getElementById('model-filter');
    container.innerHTML =
        '<label><input type="radio" name="model" value="" checked> Všechny</label>' +
        models.map(m =>
            `<label><input type="radio" name="model" value="${m.id}"> ${m.display_name}</label>`
        ).join('');
}

function renderSourceFilter(sources) {
    const select = document.getElementById('source-filter');
    select.innerHTML = '<option value="">Vše</option>' +
        sources.map(s =>
            `<option value="${s.id}">${s.id}${s.type === 'professional' ? ' (profi)' : ''}</option>`
        ).join('');
}

// ============================================================================
// Modal detail
// ============================================================================
async function openDetail(listingId) {
    const data = await apiGet(`/listings/${listingId}`);
    const l = data.listing;

    const photosHtml = l.photo_urls && l.photo_urls.length
        ? `<div class="modal-photos">${l.photo_urls.map(u => `<img src="${u}">`).join('')}</div>`
        : '';

    const historyHtml = data.price_history.length > 1
        ? `<h3 style="margin-top:16px">Historie cen</h3><ul>${
            data.price_history.map(h =>
                `<li>${new Date(h.observed_at).toLocaleDateString('cs-CZ')}: ${fmtPrice(h.price_czk)}</li>`
            ).join('')
        }</ul>`
        : '';

    document.getElementById('modal-body').innerHTML = `
        <h2>${escapeHtml(l.title || '')}</h2>
        <div class="muted">${modelDisplayName(l.model_normalized)} · ${l.source}</div>
        ${photosHtml}
        <div class="modal-row"><span class="modal-label">Cena</span><span class="modal-value">${fmtPrice(l.price_czk)}</span></div>
        <div class="modal-row"><span class="modal-label">Fair value</span><span class="modal-value">${fmtPrice(l.fair_value_czk)}</span></div>
        <div class="modal-row"><span class="modal-label">Arbitrážní potenciál</span><span class="modal-value">${fmtPercent(l.arbitrage_pct)}</span></div>
        <div class="modal-row"><span class="modal-label">Riziko padělku</span><span class="modal-value">${fmtPercent(l.counterfeit_risk)}</span></div>
        <div class="modal-row"><span class="modal-label">Stav</span><span class="modal-value">${escapeHtml(l.condition_raw || '—')}</span></div>
        <div class="modal-row"><span class="modal-label">Lokalita</span><span class="modal-value">${escapeHtml(l.location || '—')}</span></div>
        <h3 style="margin-top:16px">Popis</h3>
        <p style="font-size:13px;color:#555;white-space:pre-wrap">${escapeHtml(l.description || '')}</p>
        ${historyHtml}
        <a href="${l.url}" target="_blank" rel="noopener" class="modal-link">Otevřít inzerát ↗</a>
    `;
    document.getElementById('detail-modal').style.display = 'flex';
}

// ============================================================================
// Event handlers
// ============================================================================
async function loadResults(append = false) {
    if (!append) {
        state.offset = 0;
        document.getElementById('results').innerHTML = '<div class="loading">Načítám…</div>';
    }
    try {
        const data = await apiGet('/listings?' + buildListingsQuery());
        if (append) {
            state.items = state.items.concat(data.items);
        } else {
            state.items = data.items;
        }
        state.total = data.total;
        renderResults(append);
    } catch (e) {
        document.getElementById('results').innerHTML =
            `<div class="empty-state">Chyba načítání: ${e.message}</div>`;
    }
}

async function loadStats() {
    try {
        const stats = await apiGet('/stats');
        renderStats(stats);
    } catch (e) {
        console.error('Stats failed:', e);
    }
}

async function loadMeta() {
    state.meta = await apiGet('/meta');
    renderModelFilter(state.meta.models);
    renderSourceFilter(state.meta.sources);
}

function bindEvents() {
    // Category tabs
    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            state.category = tab.dataset.category;
            loadResults();
        });
    });

    // Sort buttons
    document.querySelectorAll('.sort-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.sort-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            state.sort = btn.dataset.sort;
            loadResults();
        });
    });

    // Source filter
    document.getElementById('source-filter').addEventListener('change', e => {
        state.source = e.target.value;
        loadResults();
    });

    // Filters panel toggle
    document.getElementById('filters-toggle').addEventListener('click', () => {
        document.getElementById('filters-panel').classList.toggle('open');
    });

    // Apply filters
    document.getElementById('apply-filters').addEventListener('click', () => {
        const modelInput = document.querySelector('input[name="model"]:checked');
        const arbInput = document.querySelector('input[name="min_arbitrage"]:checked');
        const riskInput = document.querySelector('input[name="max_risk"]:checked');
        state.model = modelInput ? modelInput.value : '';
        state.minArbitrage = arbInput ? parseFloat(arbInput.value) : 0;
        state.maxRisk = riskInput ? parseFloat(riskInput.value) : 1.0;
        loadResults();
    });

    document.getElementById('reset-filters').addEventListener('click', () => {
        document.querySelector('input[name="model"][value=""]').checked = true;
        document.querySelector('input[name="min_arbitrage"][value="0"]').checked = true;
        document.querySelector('input[name="max_risk"][value="0.55"]').checked = true;
        state.model = '';
        state.minArbitrage = 0;
        state.maxRisk = 0.55;
        loadResults();
    });

    // Card clicks (event delegation)
    document.getElementById('results').addEventListener('click', async (e) => {
        const card = e.target.closest('.card');
        if (!card) return;
        const id = parseInt(card.dataset.id);

        const action = e.target.dataset.action;
        if (action === 'favorite') {
            e.stopPropagation();
            const result = await apiPost(`/listings/${id}/favorite`);
            const item = state.items.find(x => x.id === id);
            if (item) item.is_favorite = result.is_favorite ? 1 : 0;
            renderResults();
            return;
        }
        if (action === 'dismiss') {
            e.stopPropagation();
            await apiPost(`/listings/${id}/dismiss`);
            state.items = state.items.filter(x => x.id !== id);
            renderResults();
            return;
        }

        openDetail(id);
    });

    // Modal close
    document.getElementById('modal-close').addEventListener('click', () => {
        document.getElementById('detail-modal').style.display = 'none';
    });
    document.getElementById('detail-modal').addEventListener('click', (e) => {
        if (e.target.id === 'detail-modal') {
            document.getElementById('detail-modal').style.display = 'none';
        }
    });

    // Load more
    document.getElementById('load-more').addEventListener('click', () => {
        state.offset += PAGE_SIZE;
        loadResults(true);
    });
}

// ============================================================================
// Init
// ============================================================================
async function init() {
    bindEvents();
    await loadMeta();
    await Promise.all([loadStats(), loadResults()]);
}

init();
