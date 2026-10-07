// Local Storage User ID
function getUserId() {
    let userId = localStorage.getItem('vaani_user_id');
    if (!userId) {
        userId = 'user_' + Math.random().toString(36).substr(2, 9);
        localStorage.setItem('vaani_user_id', userId);
    }
    return userId;
}

// Security: Escape HTML helper to prevent XSS
function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

const USER_ID = getUserId();
const API_BASE = window.location.origin;

// DOM Elements
const dropZone = document.getElementById('dropZone');
const fileInput = document.getElementById('fileInput');
const recordBtn = document.getElementById('recordBtn');
const cancelRecordBtn = document.getElementById('cancelRecordBtn');
const uploadTitle = document.getElementById('uploadTitle');
const uploadSubtitle = document.getElementById('uploadSubtitle');
const fileInputWrapper = document.getElementById('fileInputWrapper');
const loadingState = document.getElementById('loadingState');
const resultsZone = document.getElementById('resultsZone');
const newUploadBtn = document.getElementById('newUploadBtn');

const transcriptText = document.getElementById('transcriptText');
const summaryText = document.getElementById('summaryText');
const actionItemsList = document.getElementById('actionItemsList');
const sentimentBadge = document.getElementById('sentimentBadge');
const historyList = document.getElementById('historyList');

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    loadHistory();
    setupDragAndDrop();
    setupFileInput();
    setupRecording();
    setupFeedback();
});

// History Logic
async function loadHistory() {
    try {
        const response = await fetch(`${API_BASE}/history/${USER_ID}`);
        if (response.ok) {
            const data = await response.json();
            renderHistory(data.history);
        }
    } catch (e) {
        console.error("Failed to load history", e);
    }
}

function renderHistory(items) {
    historyList.innerHTML = '';
    items.forEach(item => {
        const li = document.createElement('li');
        li.className = 'history-item';
        
        const date = new Date(item.timestamp).toLocaleDateString();
        const shortText = item.summary || (item.transcript ? item.transcript.substring(0, 30) + '...' : 'Voice Note');
        
        li.innerHTML = `
            <div class="history-title">${escapeHtml(shortText)}</div>
            <div class="history-meta">
                <span>${escapeHtml(date)}</span>
                <span>${escapeHtml(item.sentiment || 'Neutral')}</span>
            </div>
        `;
        
        li.onclick = () => showResult(item);
        historyList.appendChild(li);
    });
}

// Drag & Drop
function setupDragAndDrop() {
    dropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropZone.classList.add('dragover');
    });

    ['dragleave', 'dragend'].forEach(type => {
        dropZone.addEventListener(type, () => dropZone.classList.remove('dragover'));
    });

    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.classList.remove('dragover');
        
        if (e.dataTransfer.files.length > 0) {
            handleFile(e.dataTransfer.files[0]);
        }
    });
}

function setupFileInput() {
    fileInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
            handleFile(e.target.files[0]);
        }
    });
    
    newUploadBtn.addEventListener('click', () => {
        resultsZone.classList.add('hidden');
        dropZone.classList.remove('hidden');
        fileInput.value = '';
    });
}

// Recording Logic
let mediaRecorder;
let audioChunks = [];
let isRecording = false;

let audioContext;
let analyser;
let microphone;
let animationFrameId;

async function setupRecording() {
    recordBtn.addEventListener('click', async () => {
        if (isRecording) {
            mediaRecorder.stop();
            isRecording = false;
            resetUI();
            return;
        }

        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            
            // UI state for recording
            cancelRecordBtn.classList.remove('hidden');
            uploadTitle.textContent = "Listening...";
            uploadSubtitle.textContent = "Click mic again to stop and summarize";
            fileInputWrapper.classList.add('hidden');
            recordBtn.classList.add('recording');
            
            // Setup Web Audio API for volume visualization
            audioContext = new (window.AudioContext || window.webkitAudioContext)();
            analyser = audioContext.createAnalyser();
            analyser.fftSize = 256;
            analyser.smoothingTimeConstant = 0.5; // more responsive
            
            microphone = audioContext.createMediaStreamSource(stream);
            microphone.connect(analyser);
            
            const dataArray = new Uint8Array(analyser.frequencyBinCount);
            
            function animateVolume() {
                analyser.getByteFrequencyData(dataArray);
                
                // Use max volume rather than average for much better responsiveness
                let maxVolume = 0;
                for(let i = 0; i < dataArray.length; i++) {
                    if (dataArray[i] > maxVolume) maxVolume = dataArray[i];
                }
                
                // Map volume (0-255) to scale (1 to 1.6) and shadow intensity
                const scale = 1 + (maxVolume / 255) * 0.6;
                const shadowSize = 30 + (maxVolume / 255) * 60;
                
                recordBtn.style.transform = `scale(${scale})`;
                recordBtn.style.boxShadow = `0 0 ${shadowSize}px rgba(239, 68, 68, 0.8)`;
                
                animationFrameId = requestAnimationFrame(animateVolume);
            }
            animateVolume();

            mediaRecorder = new MediaRecorder(stream);
            
            mediaRecorder.ondataavailable = (e) => {
                if (e.data.size > 0) audioChunks.push(e.data);
            };
            
            let isCancelled = false;
            cancelRecordBtn.onclick = () => {
                isCancelled = true;
                mediaRecorder.stop();
                resetUI();
                stream.getTracks().forEach(track => track.stop());
            };
            
            mediaRecorder.onstop = () => {
                if (!isCancelled) {
                    const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
                    const file = new File([audioBlob], "recording.webm", { type: 'audio/webm' });
                    handleFile(file);
                }
                
                audioChunks = [];
                // Stop all tracks
                stream.getTracks().forEach(track => track.stop());
            };

            audioChunks = [];
            mediaRecorder.start();
            isRecording = true;
            
        } catch (err) {
            console.error("Microphone access denied", err);
            alert("Could not access microphone.");
        }
    });
}

function resetUI() {
    isRecording = false;
    recordBtn.classList.remove('recording');
    cancelRecordBtn.classList.add('hidden');
    uploadTitle.textContent = "Upload or Record Audio";
    uploadSubtitle.textContent = "Drop your Hindi/Hinglish audio file here";
    fileInputWrapper.classList.remove('hidden');
    
    if (animationFrameId) cancelAnimationFrame(animationFrameId);
    if (microphone) microphone.disconnect();
    if (analyser) analyser.disconnect();
    if (audioContext && audioContext.state !== 'closed') audioContext.close();
    
    recordBtn.style.transform = 'scale(1)';
    recordBtn.style.boxShadow = '0 0 20px var(--primary-glow)';
}

// API Communication
async function handleFile(file) {
    if (!file.type.startsWith('audio/') && !file.type.startsWith('video/')) {
        alert('Please upload an audio file.');
        return;
    }

    // UI State: Loading
    dropZone.classList.add('hidden');
    resultsZone.classList.add('hidden');
    loadingState.classList.remove('hidden');

    const formData = new FormData();
    formData.append('audio', file);
    formData.append('user_id', USER_ID);

    try {
        const response = await fetch(`${API_BASE}/process-audio`, {
            method: 'POST',
            body: formData
        });

        if (!response.ok) {
            throw new Error(`Server error: ${response.status}`);
        }

        const data = await response.json();
        showResult(data);
        loadHistory(); // Refresh history
        
    } catch (e) {
        alert("Processing failed: " + e.message);
        loadingState.classList.add('hidden');
        dropZone.classList.remove('hidden');
    }
}

let currentInteractionId = null;

function setupFeedback() {
    const thumbsUpBtn = document.getElementById('thumbsUpBtn');
    const thumbsDownBtn = document.getElementById('thumbsDownBtn');
    const feedbackNotice = document.getElementById('feedbackNotice');

    async function submitRating(rating) {
        if (!currentInteractionId) return;

        try {
            const formData = new FormData();
            formData.append('interaction_id', currentInteractionId);
            formData.append('rating', rating);

            const res = await fetch(`${API_BASE}/feedback`, {
                method: 'POST',
                body: formData
            });

            if (res.ok) {
                thumbsUpBtn.classList.toggle('active-up', rating === 'thumbs_up');
                thumbsDownBtn.classList.toggle('active-down', rating === 'thumbs_down');
                thumbsUpBtn.disabled = true;
                thumbsDownBtn.disabled = true;
                if (feedbackNotice) {
                    feedbackNotice.textContent = rating === 'thumbs_up' ? '✓ Rated accurate!' : '✓ Feedback recorded';
                    feedbackNotice.classList.remove('hidden');
                }
            }
        } catch (e) {
            console.error("Failed to submit feedback", e);
        }
    }

    if (thumbsUpBtn && thumbsDownBtn) {
        thumbsUpBtn.addEventListener('click', () => submitRating('thumbs_up'));
        thumbsDownBtn.addEventListener('click', () => submitRating('thumbs_down'));
    }
}

function showResult(data) {
    loadingState.classList.add('hidden');
    dropZone.classList.add('hidden');
    resultsZone.classList.remove('hidden');

    // Track interaction ID for feedback
    currentInteractionId = data.interaction_id || data.id || null;
    const thumbsUpBtn = document.getElementById('thumbsUpBtn');
    const thumbsDownBtn = document.getElementById('thumbsDownBtn');
    const feedbackNotice = document.getElementById('feedbackNotice');

    if (thumbsUpBtn && thumbsDownBtn) {
        thumbsUpBtn.disabled = false;
        thumbsDownBtn.disabled = false;
        thumbsUpBtn.classList.remove('active-up');
        thumbsDownBtn.classList.remove('active-down');

        if (data.accuracy_rating === 'thumbs_up') {
            thumbsUpBtn.classList.add('active-up');
            thumbsUpBtn.disabled = true;
            thumbsDownBtn.disabled = true;
        } else if (data.accuracy_rating === 'thumbs_down') {
            thumbsDownBtn.classList.add('active-down');
            thumbsUpBtn.disabled = true;
            thumbsDownBtn.disabled = true;
        }

        if (feedbackNotice) {
            feedbackNotice.classList.add('hidden');
            feedbackNotice.textContent = '';
        }
    }

    // Ensure transcript accordion is collapsed by default for clean overview
    const transcriptAccordion = document.getElementById('transcriptAccordion');
    if (transcriptAccordion) {
        transcriptAccordion.open = false;
    }

    // Populate Data
    transcriptText.textContent = data.transcript || "No transcript generated.";
    summaryText.textContent = data.summary || "No summary generated.";
    
    // Action Items
    actionItemsList.innerHTML = '';
    if (data.action_items && data.action_items.length > 0) {
        data.action_items.forEach(item => {
            const li = document.createElement('li');
            const icon = document.createElement('i');
            icon.className = 'fa-solid fa-angle-right';
            const span = document.createElement('span');
            span.textContent = item;
            li.appendChild(icon);
            li.appendChild(document.createTextNode(' '));
            li.appendChild(span);
            actionItemsList.appendChild(li);
        });
    } else {
        const li = document.createElement('li');
        li.textContent = 'No action items identified.';
        actionItemsList.appendChild(li);
    }

    // Sentiment Badge
    const sentiment = data.sentiment || "Neutral";
    sentimentBadge.textContent = sentiment;
    sentimentBadge.className = `sentiment-badge ${sentiment.toLowerCase()}`;
}
