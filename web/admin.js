document.addEventListener('DOMContentLoaded', async () => {
    try {
        let adminKey = localStorage.getItem('vaani_admin_key') || '';
        let headers = adminKey ? { 'x-admin-key': adminKey } : {};
        let response = await fetch('/admin/analytics', { headers });

        if (response.status === 401) {
            const enteredKey = prompt("🔐 Admin Dashboard is protected. Please enter the Admin Secret Key:");
            if (enteredKey) {
                localStorage.setItem('vaani_admin_key', enteredKey);
                response = await fetch('/admin/analytics', { headers: { 'x-admin-key': enteredKey } });
            }
        }
        if (!response.ok) {
            throw new Error(`Access denied or server error (HTTP ${response.status})`);
        }
        const data = await response.json();

        // Update basic stats
        document.getElementById('totalUsers').textContent = data.total_users;
        document.getElementById('totalInteractions').textContent = data.total_interactions;

        // Chart defaults for dark theme
        Chart.defaults.color = 'rgba(255, 255, 255, 0.7)';
        Chart.defaults.borderColor = 'rgba(255, 255, 255, 0.1)';

        // Timeline Chart (Bar)
        const ctxTimeline = document.getElementById('timelineChart').getContext('2d');
        const labels = Object.keys(data.timeline);
        const values = Object.values(data.timeline);
        
        new Chart(ctxTimeline, {
            type: 'bar',
            data: {
                labels: labels.length > 0 ? labels : ['No Data'],
                datasets: [{
                    label: 'Voice Notes Processed',
                    data: values.length > 0 ? values : [0],
                    backgroundColor: 'rgba(168, 192, 255, 0.6)',
                    borderColor: 'rgba(168, 192, 255, 1)',
                    borderWidth: 1,
                    borderRadius: 4
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    title: { display: true, text: 'Last 7 Days Activity', color: '#fff' }
                }
            }
        });

        // Sentiment Chart (Doughnut)
        const ctxSentiment = document.getElementById('sentimentChart').getContext('2d');
        const sentimentLabels = Object.keys(data.sentiment_breakdown);
        const sentimentData = Object.values(data.sentiment_breakdown);

        new Chart(ctxSentiment, {
            type: 'doughnut',
            data: {
                labels: sentimentLabels,
                datasets: [{
                    data: sentimentData,
                    backgroundColor: [
                        'rgba(76, 175, 80, 0.7)', // Positive - Green
                        'rgba(158, 158, 158, 0.7)', // Neutral - Grey
                        'rgba(244, 67, 54, 0.7)', // Negative - Red
                        'rgba(33, 150, 243, 0.7)'  // Unknown - Blue
                    ],
                    borderColor: 'rgba(255,255,255,0.1)',
                    borderWidth: 1
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    title: { display: true, text: 'Sentiment Analysis', color: '#fff' },
                    legend: { position: 'bottom' }
                }
            }
        });

    } catch (error) {
        console.error("Error fetching analytics:", error);
        document.getElementById('totalUsers').textContent = "Error";
    }
});
