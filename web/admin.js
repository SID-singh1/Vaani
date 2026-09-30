let currentDays = 7;
let timelineChartInstance = null;
let sentimentChartInstance = null;

document.addEventListener('DOMContentLoaded', () => {
    initAuth();
    setupEventListeners();
    loadDashboardData(currentDays);
});

function getAdminKey() {
    return sessionStorage.getItem('vaani_admin_key') || localStorage.getItem('vaani_admin_key') || '';
}

function setAdminKey(key) {
    sessionStorage.setItem('vaani_admin_key', key);
    localStorage.setItem('vaani_admin_key', key);
}

function clearAdminKey() {
    sessionStorage.removeItem('vaani_admin_key');
    localStorage.removeItem('vaani_admin_key');
}

function initAuth() {
    const authModal = document.getElementById('authModal');
    const authSubmitBtn = document.getElementById('authSubmitBtn');
    const adminKeyInput = document.getElementById('adminKeyInput');
    const authError = document.getElementById('authError');

    authSubmitBtn.addEventListener('click', async () => {
        const key = adminKeyInput.value.trim();
        if (!key) return;

        setAdminKey(key);
        authError.style.display = 'none';
        
        const success = await loadDashboardData(currentDays);
        if (success) {
            authModal.classList.add('hidden');
        } else {
            authError.style.display = 'block';
            clearAdminKey();
        }
    });

    adminKeyInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            authSubmitBtn.click();
        }
    });
}

function setupEventListeners() {
    // Range filter buttons
    document.querySelectorAll('.filter-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
            e.target.classList.add('active');
            currentDays = parseInt(e.target.getAttribute('data-days'), 10);
            loadDashboardData(currentDays);
        });
    });

    // Refresh button
    const refreshBtn = document.getElementById('refreshBtn');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', () => {
            loadDashboardData(currentDays);
        });
    }

    // Lock button
    const lockBtn = document.getElementById('lockBtn');
    if (lockBtn) {
        lockBtn.addEventListener('click', () => {
            clearAdminKey();
            document.getElementById('authModal').classList.remove('hidden');
            document.getElementById('adminKeyInput').value = '';
            document.getElementById('authError').style.display = 'none';
        });
    }
}

async function loadDashboardData(days = 7) {
    const authModal = document.getElementById('authModal');
    const key = getAdminKey();
    const headers = key ? { 'x-admin-key': key } : {};

    try {
        const url = `/admin/analytics?days=${days}${key ? `&key=${encodeURIComponent(key)}` : ''}`;
        const response = await fetch(url, { headers });

        if (response.status === 401) {
            authModal.classList.remove('hidden');
            return false;
        }

        if (!response.ok) {
            throw new Error(`Server returned HTTP ${response.status}`);
        }

        const data = await response.json();
        authModal.classList.add('hidden');

        // Render Stat Cards
        document.getElementById('statTotalUsers').textContent = (data.total_users ?? 0).toLocaleString();
        document.getElementById('statTotalNotes').textContent = (data.total_interactions ?? 0).toLocaleString();
        document.getElementById('statToday').textContent = (data.today_interactions ?? 0).toLocaleString();

        const feedback = data.feedback || {};
        const accElement = document.getElementById('statAccuracy');
        const accSub = document.getElementById('statAccuracySub');
        if (feedback.accuracy_percentage !== null && feedback.accuracy_percentage !== undefined) {
            accElement.textContent = `${feedback.accuracy_percentage}%`;
            accSub.innerHTML = `👍 ${feedback.thumbs_up} Accurate &bull; 👎 ${feedback.thumbs_down} Inaccurate`;
        } else {
            accElement.textContent = '100%';
            accSub.textContent = 'Awaiting more user ratings';
        }

        // Render Charts
        renderTimelineChart(data.timeline || {});
        renderSentimentChart(data.sentiment_breakdown || {});

        // Render Recent Table
        renderRecentTable(data.recent_interactions || []);

        return true;
    } catch (err) {
        console.error('Failed to load dashboard metrics:', err);
        return false;
    }
}

function renderTimelineChart(timelineData) {
    const ctx = document.getElementById('timelineChart').getContext('2d');
    const labels = Object.keys(timelineData);
    const values = Object.values(timelineData);

    if (timelineChartInstance) {
        timelineChartInstance.destroy();
    }

    Chart.defaults.color = 'rgba(255, 255, 255, 0.6)';
    Chart.defaults.borderColor = 'rgba(255, 255, 255, 0.06)';

    timelineChartInstance = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels.length > 0 ? labels : ['No Data'],
            datasets: [{
                label: 'Voice Notes',
                data: values.length > 0 ? values : [0],
                backgroundColor: 'rgba(99, 102, 241, 0.6)',
                borderColor: 'rgba(99, 102, 241, 1)',
                borderWidth: 1.5,
                borderRadius: 6
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    ticks: { precision: 0 }
                }
            }
        }
    });
}

function renderSentimentChart(sentimentData) {
    const ctx = document.getElementById('sentimentChart').getContext('2d');

    if (sentimentChartInstance) {
        sentimentChartInstance.destroy();
    }

    const labels = Object.keys(sentimentData);
    const values = Object.values(sentimentData);

    const bgColors = {
        'Positive': 'rgba(34, 197, 94, 0.7)',
        'Neutral': 'rgba(148, 163, 184, 0.7)',
        'Negative': 'rgba(239, 68, 68, 0.7)',
        'Unknown': 'rgba(99, 102, 241, 0.7)'
    };

    const colors = labels.map(l => bgColors[l] || 'rgba(148, 163, 184, 0.7)');

    sentimentChartInstance = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: labels,
            datasets: [{
                data: values.length > 0 ? values : [1],
                backgroundColor: colors,
                borderWidth: 0
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: { boxWidth: 12, padding: 15 }
                }
            }
        }
    });
}

function renderRecentTable(interactions) {
    const tbody = document.getElementById('recentTableBody');
    if (!tbody) return;

    if (!interactions || interactions.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: rgba(255,255,255,0.4); padding: 20px;">No interaction records found yet.</td></tr>';
        return;
    }

    let rowsHtml = '';
    interactions.forEach(item => {
        const sentimentClass = item.sentiment === 'Positive' ? 'badge-positive' :
                               item.sentiment === 'Negative' ? 'badge-negative' : 'badge-neutral';

        let feedbackBadge = '<span class="badge badge-unrated">⏳ Unrated</span>';
        if (item.accuracy_rating === 'thumbs_up') {
            feedbackBadge = '<span class="badge badge-up">👍 Accurate</span>';
        } else if (item.accuracy_rating === 'thumbs_down') {
            feedbackBadge = '<span class="badge badge-down">👎 Inaccurate</span>';
        }

        const summaryText = item.summary && item.summary.length > 80 ? 
                            item.summary.substring(0, 80) + '...' : (item.summary || 'None');

        rowsHtml += `
            <tr>
                <td style="white-space: nowrap; font-size: 0.85rem; color: rgba(255,255,255,0.6);">${item.timestamp}</td>
                <td><code style="background: rgba(255,255,255,0.06); padding: 3px 6px; border-radius: 4px; font-size: 0.8rem;">${item.user_id}</code></td>
                <td>${summaryText}</td>
                <td><span class="badge ${sentimentClass}">${item.sentiment}</span></td>
                <td>${feedbackBadge}</td>
            </tr>
        `;
    });

    tbody.innerHTML = rowsHtml;
}
