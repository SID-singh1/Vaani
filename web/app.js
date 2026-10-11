'use strict';

// ---------- Small helpers ----------

const $ = (id) => document.getElementById(id);
const TOKEN_KEY = 'vaani_token';
const LEGACY_ID_KEY = 'vaani_user_id';
const STEPS = ['upload', 'queued', 'transcribing', 'analyzing'];
const BUSY_MESSAGE = 'Vaani is getting a lot of traffic right now. Please try again in a minute.';
const OFFLINE_MESSAGE = "Can't reach Vaani right now. It may be briefly down; please try again in a minute.";

const state = {
    me: null,
    notes: [],
    current: null,       // note shown in the result view
    tracking: null,      // id of the note being followed
    recorder: null,
    recordStart: 0,
    recordTimer: null,
    maxRecordSeconds: 30 * 60,
};

function storage(key, value) {
    try {
        if (value === undefined) return localStorage.getItem(key);
        if (value === null) localStorage.removeItem(key);
        else localStorage.setItem(key, value);
    } catch (_) { /* private mode: keep going without persistence */ }
    return null;
}

function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
        if (key === 'class') node.className = value;
        else if (key === 'text') node.textContent = value;
        else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
        else node.setAttribute(key, value);
    }
    for (const child of children) if (child) node.append(child);
    return node;
}

let toastTimer;
function toast(message, isError = false) {
    const box = $('toast');
    box.textContent = message;
    box.classList.toggle('error', isError);
    box.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => box.classList.remove('show'), isError ? 6000 : 3000);
}

function formatDuration(seconds) {
    if (!seconds && seconds !== 0) return '';
    const s = Math.round(seconds);
    return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`;
}

// Model output says "Me" for tasks the speaker took on; the reader is that speaker.
function displayOwner(owner) {
    return ['me', 'i', 'myself', 'speaker', 'the speaker', 'self'].includes(owner.trim().toLowerCase()) ? 'You' : owner;
}

// "2m 54s voice note → ready in 9s": the time Vaani saves.
function turnaround(note) {
    const ms = note.timings?.total_ms;
    if (!ms) return note.source === 'text' ? 'from text' : '';
    const ready = `ready in ${Math.max(1, Math.round(ms / 1000))}s`;
    return note.audio_duration_sec ? `${formatDuration(note.audio_duration_sec)} voice note → ${ready}` : `text → ${ready}`;
}

// Themed replacement for window.confirm(); resolves true when the user confirms.
function confirmDialog(title, message, confirmLabel) {
    const dialog = $('confirmDialog');
    $('confirmTitle').textContent = title;
    $('confirmText').textContent = message;
    $('confirmOk').textContent = confirmLabel;
    dialog.showModal();
    return new Promise((resolve) => {
        const finish = (value) => {
            $('confirmOk').removeEventListener('click', ok);
            $('confirmCancel').removeEventListener('click', cancel);
            dialog.removeEventListener('cancel', cancel);
            if (dialog.open) dialog.close();
            resolve(value);
        };
        const ok = () => finish(true);
        const cancel = () => finish(false);
        $('confirmOk').addEventListener('click', ok);
        $('confirmCancel').addEventListener('click', cancel);
        dialog.addEventListener('cancel', cancel);
    });
}

function formatDate(iso) {
    if (!iso) return '';
    const date = new Date(iso);
    const today = new Date();
    const sameDay = date.toDateString() === today.toDateString();
    return sameDay
        ? date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
        : date.toLocaleDateString([], { day: 'numeric', month: 'short' });
}

// ---------- API ----------

class ApiError extends Error {
    constructor(message, status) { super(message); this.status = status; }
}

async function createSession() {
    const legacy = storage(LEGACY_ID_KEY);
    const res = await fetch('/api/v1/session', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(legacy ? { legacy_user_id: legacy } : {}),
    });
    if (!res.ok) throw new ApiError('Could not start a session. Please refresh.', res.status);
    const data = await res.json();
    storage(TOKEN_KEY, data.token);
    if (legacy) storage(LEGACY_ID_KEY, null);  // migrated (or not claimable): don't retry
    return data.token;
}

async function api(path, { method = 'GET', json, form, retry = true } = {}) {
    let token = storage(TOKEN_KEY) || await createSession();
    const headers = { Authorization: `Bearer ${token}` };
    let body;
    if (json !== undefined) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(json); }
    if (form) body = form;
    const res = await fetch(path, { method, headers, body });
    if (res.status === 401 && retry) {
        storage(TOKEN_KEY, null);
        return api(path, { method, json, form, retry: false });
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new ApiError(data?.error?.message || BUSY_MESSAGE, res.status);
    return data;
}

// Upload with progress events (fetch can't report upload progress).
function uploadNote(form, onProgress) {
    return new Promise(async (resolve, reject) => {
        const token = storage(TOKEN_KEY) || await createSession().catch(reject);
        if (!token) return;
        const xhr = new XMLHttpRequest();
        xhr.open('POST', '/api/v1/notes');
        xhr.setRequestHeader('Authorization', `Bearer ${token}`);
        xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
        xhr.onload = () => {
            let data = {};
            try { data = JSON.parse(xhr.responseText); } catch (_) { /* non-JSON */ }
            if (xhr.status === 401) { storage(TOKEN_KEY, null); reject(new ApiError('Session expired. Please try again.', 401)); }
            else if (xhr.status >= 200 && xhr.status < 300) resolve(data);
            else reject(new ApiError(data?.error?.message || BUSY_MESSAGE, xhr.status));
        };
        xhr.onerror = () => reject(new ApiError(OFFLINE_MESSAGE, 0));
        xhr.send(form);
    });
}

// ---------- Views ----------

function show(view) {
    for (const id of ['composer', 'textComposer', 'progress', 'errorPanel', 'result']) {
        $(id).classList.toggle('hidden', id !== view);
    }
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

function resetComposer() {
    state.current = null;
    state.tracking = null;
    $('fileInput').value = '';
    $('textInput').value = '';
    updateTextCounter();
    renderHistory();
    show('composer');
}

function setStep(step, details = {}) {
    const index = STEPS.indexOf(step);
    document.querySelectorAll('.step').forEach((li) => {
        const i = STEPS.indexOf(li.dataset.step);
        li.classList.toggle('done', i < index);
        li.classList.toggle('active', i === index);
    });
    if (details.queue !== undefined) $('queueDetail').textContent = details.queue;
    $('uploadBar').classList.toggle('hidden', step !== 'upload');
}

function showError(message) {
    $('errorText').textContent = message;
    show('errorPanel');
}

// ---------- Me / engines / usage ----------

async function loadMe() {
    try {
        state.me = await api('/api/v1/me');
    } catch (err) {
        toast(err.message, true);
        return;
    }
    renderModes();
    renderUsage();
}

function renderModes() {
    const { engines = [], engine } = state.me || {};
    const current = engines.find((e) => e.name === engine) || engines[0];
    const switcher = $('modeSwitch');
    switcher.replaceChildren();
    switcher.classList.toggle('hidden', engines.length < 2);
    const icons = { cloud: '⚡', private: '🔒' };
    for (const e of engines) {
        switcher.append(el('button', {
            class: e.name === current?.name ? 'active' : '',
            role: 'radio',
            'aria-checked': String(e.name === current?.name),
            text: `${icons[e.name] || ''} ${e.label}`,
            onclick: () => setMode(e.name),
        }));
    }
    if (current) {
        state.maxRecordSeconds = current.max_audio_minutes * 60;
        const note = $('modeNote');
        note.replaceChildren(
            engines.length < 2 ? `${icons[current.name] || ''} ${current.label} mode · ` : '',
            current.description + ' ',
            el('a', { href: '/privacy', text: 'Privacy' }),
        );
    }
}

async function setMode(name) {
    try {
        state.me = await api('/api/v1/me', { method: 'PATCH', json: { engine: name } });
        renderModes();
        toast(`${state.me.engines.find((e) => e.name === name)?.label} mode on`);
    } catch (err) { toast(err.message, true); }
}

function renderUsage() {
    const usage = state.me?.usage;
    if (!usage || !usage.daily_limit) { $('usage').classList.add('hidden'); return; }
    $('usage').classList.remove('hidden');
    $('usageText').textContent = `${usage.used_today} of ${usage.daily_limit} free notes today`;
    $('usageBar').style.width = `${Math.min(100, (100 * usage.used_today) / usage.daily_limit)}%`;
}

// ---------- History ----------

async function loadHistory() {
    try {
        state.notes = (await api('/api/v1/notes?limit=50')).notes;
    } catch (err) {
        return toast(err.message, true);
    }
    renderHistory();
}

function renderHistory() {
    const list = $('historyList');
    list.replaceChildren();
    $('historyEmpty').classList.toggle('hidden', state.notes.length > 0);
    $('deleteAllBtn').classList.toggle('hidden', state.notes.length === 0);
    for (const note of state.notes) {
        const active = note.status === 'queued' || note.status === 'processing';
        const title = note.title || (note.status === 'failed' ? 'Failed note' : active ? 'Processing…' : 'Note');
        const meta = [formatDate(note.created_at), note.audio_duration_sec ? formatDuration(note.audio_duration_sec) : note.source === 'text' ? 'text' : '']
            .filter(Boolean).join(' · ');
        const item = el('li', {
            class: `history-item${state.current?.id === note.id ? ' active' : ''}`,
            tabindex: '0',
            onclick: () => openNote(note),
            onkeydown: (e) => { if (e.key === 'Enter') openNote(note); },
        },
            el('span', { class: `status-dot ${note.status === 'failed' ? 'failed' : active ? 'active' : ''}` }),
            el('div', { class: 'history-text' },
                el('div', { class: 'history-title', text: title }),
                el('div', { class: 'history-meta', text: meta })),
            el('button', {
                class: 'icon-btn small', 'aria-label': 'Delete note', title: 'Delete',
                onclick: (e) => { e.stopPropagation(); deleteNote(note.id); },
            }, el('i', { class: 'fa-regular fa-trash-can' })),
        );
        list.append(item);
    }
}

function openNote(note) {
    closeSidebar();
    if (note.status === 'done') showResult(note);
    else if (note.status === 'failed') showError(note.error || 'This note could not be processed.');
    else track(note.id, note);
}

async function deleteNote(id) {
    if (!await confirmDialog('Delete this note?', 'It will be removed permanently, including its transcript.', 'Delete')) return;
    try {
        await api(`/api/v1/notes/${encodeURIComponent(id)}`, { method: 'DELETE' });
        state.notes = state.notes.filter((n) => n.id !== id);
        if (state.current?.id === id) resetComposer();
        renderHistory();
        toast('Note deleted');
    } catch (err) { toast(err.message, true); }
}

async function deleteAll() {
    if (!await confirmDialog('Delete all your notes?', 'Every note and transcript will be removed permanently. This cannot be undone.', 'Delete all')) return;
    try {
        const { deleted } = await api('/api/v1/notes', { method: 'DELETE' });
        state.notes = [];
        resetComposer();
        toast(`Deleted ${deleted} note${deleted === 1 ? '' : 's'}`);
    } catch (err) { toast(err.message, true); }
}

// ---------- Submitting ----------

async function submit(form, label) {
    show('progress');
    $('progressTitle').textContent = label;
    $('progressSub').textContent = 'This usually takes 10–30 seconds.';
    $('uploadDetail').textContent = '';
    $('queueDetail').textContent = '';
    setStep('upload');
    const bar = $('uploadBar').firstElementChild;
    bar.style.width = '0%';
    try {
        const note = await uploadNote(form, (fraction) => {
            bar.style.width = `${Math.round(fraction * 100)}%`;
            $('uploadDetail').textContent = `${Math.round(fraction * 100)}%`;
        });
        state.notes.unshift(note);
        renderHistory();
        await track(note.id, note);
    } catch (err) {
        showError(err.message);
    }
    loadMe();
}

function submitFile(file) {
    if (!file) return;
    if (!/^(audio|video)\//.test(file.type) && !/\.(mp3|m4a|ogg|oga|opus|wav|flac|aac|amr|mp4|webm|mov|3gp)$/i.test(file.name)) {
        return toast('Please choose an audio or video file.', true);
    }
    const form = new FormData();
    form.append('audio', file, file.name || 'recording');
    submit(form, 'Working on your note…');
}

function submitText() {
    const text = $('textInput').value.trim();
    if (text.split(/\s+/).length < 3) return toast('Please paste a bit more text.', true);
    const form = new FormData();
    form.append('text', text);
    submit(form, 'Summarizing your text…');
}

// ---------- Tracking ----------

async function track(id, initial) {
    state.tracking = id;
    show('progress');
    let note = initial;
    while (state.tracking === id) {
        if (note) {
            if (note.status === 'done') return finish(note);
            if (note.status === 'failed') { refreshNote(note); return showError(note.error || 'Processing failed.'); }
            const stage = note.stage === 'queued' || note.status === 'queued' ? 'queued' : note.stage;
            const queue = note.queue_position > 1
                ? `#${note.queue_position} · ~${note.eta_seconds}s`
                : (stage === 'queued' ? 'next up' : '');
            setStep(STEPS.includes(stage) ? stage : 'transcribing', { queue });
            if (note.eta_seconds && stage === 'queued') {
                $('progressSub').textContent = `About ${note.eta_seconds} seconds to go.`;
            }
        }
        try {
            note = await api(`/api/v1/notes/${encodeURIComponent(id)}?wait=20`);
        } catch (err) {
            if (err.status === 404) return showError('That note was deleted.');
            await new Promise((r) => setTimeout(r, 3000));  // transient network issue: keep following
            note = null;
        }
    }
}

function refreshNote(note) {
    const index = state.notes.findIndex((n) => n.id === note.id);
    if (index >= 0) state.notes[index] = note; else state.notes.unshift(note);
    renderHistory();
}

function finish(note) {
    refreshNote(note);
    showResult(note);
    if (document.hidden && 'Notification' in window && Notification.permission === 'granted') {
        new Notification('Your note is ready', { body: note.title || 'Vaani' });
    }
}

// ---------- Result ----------

function checkedKey(id) { return `vaani_checked_${id}`; }

function showResult(note) {
    state.current = note;
    state.tracking = null;
    $('resultTitle').textContent = note.title || 'Your note';
    const sentiment = note.sentiment || 'Neutral';
    $('sentimentBadge').textContent = sentiment;
    $('sentimentBadge').className = `sentiment-badge ${sentiment.toLowerCase()}`;

    const meta = [
        formatDate(note.created_at),
        turnaround(note),
        note.engine ? `${note.engine === 'private' ? '🔒 Private' : '⚡ Fast'} mode` : '',
    ].filter(Boolean).join(' · ');
    $('resultMeta').textContent = meta;
    $('summaryText').textContent = note.summary || '';
    $('transcriptText').textContent = note.transcript || '';
    $('transcriptBox').open = false;

    const list = $('actionsList');
    list.replaceChildren();
    let checked = [];
    try { checked = JSON.parse(storage(checkedKey(note.id)) || '[]'); } catch (_) { checked = []; }
    if (!note.action_items?.length) {
        list.append(el('li', { class: 'empty-actions', text: 'Nothing to do here. 🎉' }));
    }
    (note.action_items || []).forEach((item, index) => {
        const isChecked = checked.includes(index);
        const box = el('input', { type: 'checkbox', 'aria-label': 'Mark done' });
        box.checked = isChecked;
        const row = el('li', { class: `action-item${isChecked ? ' checked' : ''}` }, box);
        const chips = el('div', { class: 'chips' });
        if (item.owner) chips.append(el('span', { class: 'chip', text: `👤 ${displayOwner(item.owner)}` }));
        if (item.due) chips.append(el('span', { class: 'chip due', text: `⏰ ${item.due}` }));
        row.append(el('div', {}, el('div', { class: 'task', text: item.task }), chips.childElementCount ? chips : null));
        box.addEventListener('change', () => {
            row.classList.toggle('checked', box.checked);
            const next = new Set(checked);
            if (box.checked) next.add(index); else next.delete(index);
            checked = [...next];
            storage(checkedKey(note.id), JSON.stringify(checked));
        });
        list.append(row);
    });

    renderRating(note.rating);
    renderHistory();
    show('result');
}

function renderRating(rating) {
    $('rateUp').classList.toggle('selected', rating === 'up');
    $('rateDown').classList.toggle('selected', rating === 'down');
}

async function rate(value) {
    const note = state.current;
    if (!note) return;
    try {
        await api(`/api/v1/notes/${encodeURIComponent(note.id)}/rating`, { method: 'POST', json: { rating: value } });
        note.rating = value;
        renderRating(value);
        toast('Thanks for the feedback!');
    } catch (err) { toast(err.message, true); }
}

function noteAsMarkdown(note) {
    const lines = [`# ${note.title || 'Voice note'}`, '', `_${new Date(note.created_at).toLocaleString()}_`, '', '## Summary', '', note.summary || '', '', '## Action items', ''];
    if (note.action_items?.length) {
        for (const item of note.action_items) {
            const extras = [item.owner && `owner: ${displayOwner(item.owner)}`, item.due && `due: ${item.due}`].filter(Boolean).join(', ');
            lines.push(`- [ ] ${item.task}${extras ? ` (${extras})` : ''}`);
        }
    } else lines.push('_None_');
    lines.push('', '## Transcript', '', note.transcript || '');
    return lines.join('\n');
}

async function copyNote() {
    if (!state.current) return;
    try {
        await navigator.clipboard.writeText(noteAsMarkdown(state.current));
        toast('Copied to clipboard');
    } catch (_) { toast('Copy failed. Select the text manually.', true); }
}

function downloadNote() {
    const note = state.current;
    if (!note) return;
    const blob = new Blob([noteAsMarkdown(note)], { type: 'text/markdown' });
    const slug = (note.title || 'note').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'note';
    const link = el('a', { href: URL.createObjectURL(blob), download: `${slug}.md` });
    document.body.append(link);
    link.click();
    setTimeout(() => { URL.revokeObjectURL(link.href); link.remove(); }, 1000);
}

// ---------- Recording ----------

function pickMimeType() {
    const candidates = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus'];
    return candidates.find((type) => window.MediaRecorder?.isTypeSupported?.(type)) || '';
}

async function startRecording() {
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
        return toast("Your browser can't record audio here. Upload a file instead.", true);
    }
    let stream;
    try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    } catch (_) {
        return toast('Microphone access was blocked. Allow it in your browser settings, or upload a file.', true);
    }
    const mimeType = pickMimeType();
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    const chunks = [];
    let cancelled = false;
    recorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
    recorder.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        clearInterval(state.recordTimer);
        setRecordingUI(false);
        if (cancelled || !chunks.length) return;
        const type = recorder.mimeType || mimeType || 'audio/webm';
        const ext = type.includes('mp4') ? 'm4a' : type.includes('ogg') ? 'ogg' : 'webm';
        submitFile(new File(chunks, `recording.${ext}`, { type: type.split(';')[0] }));
    };
    recorder.cancel = () => { cancelled = true; recorder.stop(); };
    state.recorder = recorder;
    recorder.start(1000);
    state.recordStart = Date.now();
    setRecordingUI(true);
    state.recordTimer = setInterval(() => {
        const elapsed = (Date.now() - state.recordStart) / 1000;
        $('composerSubtitle').replaceChildren(el('span', { class: 'record-timer', text: formatDuration(elapsed) }), ' · tap Done when finished');
        if (elapsed >= state.maxRecordSeconds) recorder.stop();
    }, 250);
    if ('Notification' in window && Notification.permission === 'default') Notification.requestPermission().catch(() => {});
}

function setRecordingUI(recording) {
    $('recordBtn').classList.toggle('recording', recording);
    $('recordBtn').setAttribute('aria-label', recording ? 'Stop recording' : 'Start recording');
    $('composerTitle').textContent = recording ? 'Listening…' : 'Tap to record';
    if (!recording) $('composerSubtitle').textContent = 'or drop an audio file here';
    $('composerActions').classList.toggle('hidden', recording);
    $('recordingActions').classList.toggle('hidden', !recording);
    $('composerHint').classList.toggle('hidden', recording);
}

// ---------- Sidebar & misc ----------

function openSidebar() { $('sidebar').classList.add('open'); $('backdrop').classList.add('show'); }
function closeSidebar() { $('sidebar').classList.remove('open'); $('backdrop').classList.remove('show'); }

function updateTextCounter() {
    const length = $('textInput').value.length;
    $('textCounter').textContent = `${length.toLocaleString()} / 20,000`;
    $('submitTextBtn').disabled = $('textInput').value.trim().length < 10;
}

function wireEvents() {
    $('recordBtn').addEventListener('click', () => (state.recorder?.state === 'recording' ? state.recorder.stop() : startRecording()));
    $('stopBtn').addEventListener('click', () => state.recorder?.stop());
    $('cancelRecordBtn').addEventListener('click', () => state.recorder?.cancel());
    $('fileInput').addEventListener('change', (e) => submitFile(e.target.files[0]));
    $('showTextBtn').addEventListener('click', () => { show('textComposer'); $('textInput').focus(); });
    $('cancelTextBtn').addEventListener('click', resetComposer);
    $('textInput').addEventListener('input', updateTextCounter);
    $('submitTextBtn').addEventListener('click', submitText);
    $('newNoteBtn').addEventListener('click', () => { closeSidebar(); resetComposer(); });
    $('anotherBtn').addEventListener('click', resetComposer);
    $('errorRetryBtn').addEventListener('click', resetComposer);
    $('rateUp').addEventListener('click', () => rate('up'));
    $('rateDown').addEventListener('click', () => rate('down'));
    $('copyBtn').addEventListener('click', copyNote);
    $('downloadBtn').addEventListener('click', downloadNote);
    $('deleteBtn').addEventListener('click', () => state.current && deleteNote(state.current.id));
    $('deleteAllBtn').addEventListener('click', deleteAll);
    $('openSidebar').addEventListener('click', openSidebar);
    $('closeSidebar').addEventListener('click', closeSidebar);
    $('backdrop').addEventListener('click', closeSidebar);
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeSidebar(); });

    const composer = $('composer');
    composer.addEventListener('dragover', (e) => { e.preventDefault(); composer.classList.add('dragover'); });
    ['dragleave', 'dragend'].forEach((t) => composer.addEventListener(t, () => composer.classList.remove('dragover')));
    composer.addEventListener('drop', (e) => {
        e.preventDefault();
        composer.classList.remove('dragover');
        submitFile(e.dataTransfer.files[0]);
    });

    const dialog = $('feedbackDialog');
    $('feedbackBtn').addEventListener('click', () => { closeSidebar(); dialog.showModal(); });
    $('feedbackCancel').addEventListener('click', () => dialog.close());
    $('feedbackSend').addEventListener('click', async () => {
        const message = $('feedbackText').value.trim();
        if (message.length < 2) return;
        try {
            await api('/api/v1/feedback', { method: 'POST', json: { message } });
            $('feedbackText').value = '';
            dialog.close();
            toast('Thank you! 🙏');
        } catch (err) { toast(err.message, true); }
    });
}

document.addEventListener('DOMContentLoaded', async () => {
    wireEvents();
    updateTextCounter();
    await loadMe();
    await loadHistory();
});
