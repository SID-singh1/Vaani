'use strict';

const $ = (id) => document.getElementById(id);
const KEY = 'vaani_admin_key';
let days = 30;
const charts = {};

function getKey() { try { return sessionStorage.getItem(KEY) || ''; } catch (_) { return ''; } }
function setKey(value) { try { value ? sessionStorage.setItem(KEY, value) : sessionStorage.removeItem(KEY); } catch (_) { /* ignore */ } }

function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
        if (k === 'class') node.className = v; else if (k === 'text') node.textContent = v; else node.setAttribute(k, v);
    }
    for (const child of children) if (child !== null && child !== undefined) node.append(child);
    return node;
}

const fmt = {
    num: (v) => (v === null || v === undefined ? '–' : Number(v).toLocaleString()),
    pct: (v) => (v === null || v === undefined ? '–' : `${v}%`),
    ms: (v) => (v === null || v === undefined ? '–' : v >= 1000 ? `${(v / 1000).toFixed(1)}s` : `${v}ms`),
    when: (iso) => (iso ? new Date(iso).toLocaleString([], { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : ''),
};

async function load() {
    const key = getKey();
    if (!key) { $('authDialog').showModal(); return; }
    const res = await fetch(`/api/v1/admin/analytics?days=${days}`, { headers: { 'X-Admin-Key': key } });
    const data = await res.json().catch(() => ({}));
    if (res.status === 401) {
        setKey('');
        $('authError').textContent = data?.error?.message || 'Invalid key.';
        $('authError').classList.remove('hidden');
        $('authDialog').showModal();
        return;
    }
    if (!res.ok) { alert(data?.error?.message || `Failed to load (${res.status})`); return; }
    render(data);
}

function stat(label, value, sub) {
    return el('div', { class: 'panel stat' },
        el('div', { class: 'stat-label', text: label }),
        el('div', { class: 'stat-value', text: value }),
        sub ? el('div', { class: 'stat-sub', text: sub }) : null);
}

function render(d) {
    $('generatedAt').textContent = `Last ${d.window_days} days · updated ${fmt.when(d.generated_at)} · test accounts excluded`;
    const ratingSub = d.ratings.accuracy_pct === null ? 'no ratings yet' : `${d.ratings.thumbs_up} 👍 · ${d.ratings.thumbs_down} 👎`;
    $('stats').replaceChildren(
        stat('Users (all time)', fmt.num(d.users.total), `${fmt.num(d.users.new_in_window)} new in window`),
        stat('Weekly active', fmt.num(d.users.wau), `DAU ${fmt.num(d.users.dau)} · MAU ${fmt.num(d.users.mau)}`),
        stat('Returning users', fmt.pct(d.users.returning_rate), `${fmt.num(d.users.returning)} used it on 2+ days`),
        stat('Notes', fmt.num(d.notes.in_window), `${fmt.num(d.notes.total_done)} all time`),
        stat('Listening time saved', `${fmt.num(d.time_saved.listening_minutes)} min`,
            `notes ready in ${fmt.num(d.time_saved.waiting_minutes)} min total`),
        stat('Faster than listening', d.time_saved.median_speedup ? `${d.time_saved.median_speedup}×` : '–',
            'median, audio length ÷ processing time'),
        stat('Latency p50 / p95', `${fmt.ms(d.latency_ms.p50)}`, `p95 ${fmt.ms(d.latency_ms.p95)} · ${fmt.num(d.latency_ms.samples)} notes`),
        stat('Failure rate', fmt.pct(d.notes.failure_rate), `${fmt.num(d.notes.failed_in_window)} failed`),
        stat('Rated accurate', fmt.pct(d.ratings.accuracy_pct), ratingSub),
    );

    if (window.Chart) {
        Chart.defaults.color = 'rgba(255,255,255,0.6)';
        Chart.defaults.borderColor = 'rgba(255,255,255,0.06)';
        Chart.defaults.font.family = 'Outfit, sans-serif';
        charts.timeline?.destroy();
        charts.timeline = new Chart($('timelineChart'), {
            data: {
                labels: d.timeline.map((t) => t.date.slice(5)),
                datasets: [
                    { type: 'bar', label: 'Notes', data: d.timeline.map((t) => t.notes), backgroundColor: 'rgba(99,102,241,0.6)', borderRadius: 4 },
                    { type: 'line', label: 'Active users', data: d.timeline.map((t) => t.active_users), borderColor: '#f59e0b', pointRadius: 0, tension: 0.3 },
                ],
            },
            options: { responsive: true, maintainAspectRatio: false, scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } },
        });
        const channels = Object.entries(d.channels);
        charts.channels?.destroy();
        charts.channels = new Chart($('channelChart'), {
            type: 'doughnut',
            data: {
                labels: channels.length ? channels.map(([k]) => k) : ['no data'],
                datasets: [{ data: channels.length ? channels.map(([, v]) => v) : [1],
                    backgroundColor: ['#6366f1', '#22d3ee', '#10b981', '#f59e0b'], borderWidth: 0 }],
            },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } } },
        });
    }

    $('recentBody').replaceChildren(...(d.recent.length ? d.recent.map((n) => el('tr', {},
        el('td', { text: fmt.when(n.created_at) }),
        el('td', {}, el('code', { text: n.user })),
        el('td', { text: n.channel || '' }),
        el('td', { text: n.title || '–' }),
        el('td', {}, el('span', { class: `pill ${n.status}`, text: n.status })),
        el('td', { text: fmt.ms(n.total_ms) }),
    )) : [el('tr', {}, el('td', { colspan: '6', text: 'No notes yet.' }))]));

    $('feedbackList').replaceChildren(...(d.feedback.length ? d.feedback.map((f) => el('div', { class: 'feedback-item' },
        el('small', { text: `${fmt.when(f.created_at)} · ${f.user}` }), f.message))
        : [el('p', { class: 'stat-sub', text: 'No feedback yet.' })]));
}

document.addEventListener('DOMContentLoaded', () => {
    $('authSubmit').addEventListener('click', () => {
        const value = $('adminKey').value.trim();
        if (!value) return;
        setKey(value);
        $('authError').classList.add('hidden');
        $('authDialog').close();
        load();
    });
    $('adminKey').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('authSubmit').click(); });
    $('refreshBtn').addEventListener('click', load);
    $('lockBtn').addEventListener('click', () => { setKey(''); $('adminKey').value = ''; $('authDialog').showModal(); });
    document.querySelectorAll('#rangeSwitch button').forEach((button) => button.addEventListener('click', () => {
        document.querySelectorAll('#rangeSwitch button').forEach((b) => b.classList.toggle('active', b === button));
        days = Number(button.dataset.days);
        load();
    }));
    load();
});
