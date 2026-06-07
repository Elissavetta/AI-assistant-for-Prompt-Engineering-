const API = '/api';

const MODULE_META = [
    { id: 1, name: 'Структура промпта', icon: '🏗️' },
    { id: 2, name: 'Улучшение промптов', icon: '🔧' },
    { id: 3, name: 'Few-shot', icon: '🎯' },
    { id: 4, name: 'Chain-of-thought', icon: '🧠' },
    { id: 5, name: 'Добавление контекста', icon: '📎' },
    { id: 6, name: 'Комплексный промпт', icon: '🏆' },
];

let currentMode = 'lesson';

function getToken() {
    return localStorage.getItem('token');
}

function headers() {
    return {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${getToken()}`
    };
}

function handleAuthError(res) {
    if (res.status === 401) {
        localStorage.removeItem('token');
        window.location.href = '/login';
        return true;
    }
    return false;
}

async function fetchProfile() {
    try {
        const res = await fetch(`${API}/profile/me`, { headers: headers() });
        if (handleAuthError(res)) return null;
        if (!res.ok) return null;
        return await res.json();
    } catch {
        return null;
    }
}

async function fetchProgress() {
    try {
        const res = await fetch(`${API}/profile/progress`, { headers: headers() });
        if (handleAuthError(res)) return [];
        if (!res.ok) return [];
        return await res.json();
    } catch {
        return [];
    }
}

function updateUI(profile, progress) {
    if (!profile) return;

    document.getElementById('user-score').textContent = profile.total_score;
    document.getElementById('sidebar-score').textContent = profile.total_score;
    document.getElementById('user-name').textContent = profile.username;

    const levelBadge = document.getElementById('level-badge');
    const levels = {
        newbie: { label: '🌱 Новичок', cls: 'level-newbie' },
        intermediate: { label: '⚡ Средний', cls: 'level-intermediate' },
        advanced: { label: '🏆 Продвинутый', cls: 'level-advanced' },
    };
    const lvl = levels[profile.level] || levels.newbie;
    levelBadge.className = `level-badge ${lvl.cls}`;
    levelBadge.textContent = lvl.label;

    const badgesList = document.getElementById('badges-list');
    if (profile.badges && profile.badges.length > 0) {
        badgesList.innerHTML = profile.badges.map(b => `
            <span class="badge-item">${b}</span>
        `).join('');
    }

    const modulesList = document.getElementById('modules-list');
    const completedModules = (progress || []).filter(p => p.completed).map(p => p.module_id);
    modulesList.innerHTML = MODULE_META.map(m => `
        <div class="module-item ${completedModules.includes(m.id) ? 'completed' : ''}" data-module="${m.id}">
            <div class="module-icon">${completedModules.includes(m.id) ? '✅' : m.icon}</div>
            <span>${escapeHtml(m.name)}</span>
        </div>
    `).join('');

    modulesList.querySelectorAll('.module-item').forEach(el => {
        el.addEventListener('click', () => {
            const mid = parseInt(el.dataset.module, 10);
            const meta = MODULE_META.find(m => m.id === mid);
            if (meta) selectModule(mid, meta.name);
        });
    });

    const isReturning = profile.level && profile.level !== '';
    const welcomeTitle = document.getElementById('welcome-title');
    const welcomeSubtitle = document.getElementById('welcome-subtitle');
    const startBtn = document.getElementById('start-btn');

    if (isReturning) {
        welcomeTitle.textContent = 'С возвращением!';
        const currentModule = (progress || []).find(p => !p.completed);
        if (currentModule) {
            const meta = MODULE_META.find(m => m.id === currentModule.module_id);
            welcomeSubtitle.textContent = 'Ты на модуле ' + (meta ? meta.name : currentModule.module_id) + ' — продолжим?';
        } else {
            welcomeSubtitle.textContent = 'Все модули пройдены! Можно повторить или попробовать режим Prompt Up.';
        }
        startBtn.textContent = 'Продолжить обучение';
    } else {
        welcomeTitle.textContent = 'Привет! Я Prompt Up — твой наставник по работе с ИИ';
        welcomeSubtitle.textContent = 'Помогу освоить промпт-инжиниринг — от основ до продвинутых техник. Напиши что-нибудь, и мы начнём!';
        startBtn.textContent = 'Начать обучение';
    }
}

function selectModule(moduleId, moduleName) {
    sendMessage('Хочу пройти модуль ' + moduleId + ': ' + moduleName);
}

function selectPromptUp() {
    sendMessage('Хочу перейти в режим Prompt Up');
}

let isLoading = false;

function createTypewriter(el) {
    const bubble = el.querySelector('.message-bubble');
    let rawBuffer = '';
    let displayedLen = 0;
    let rafId = null;
    let lastRender = 0;
    const RENDER_INTERVAL = 80;
    const CHARS_PER_FRAME = 3;
    let finished = false;

    function render() {
        const typing = el.querySelector('.typing-indicator');
        if (typing) typing.remove();
        bubble.innerHTML = renderMarkdown(rawBuffer.slice(0, displayedLen));
        scrollToBottom();
    }

    function tick(now) {
        if (finished) return;

        if (displayedLen < rawBuffer.length) {
            displayedLen = Math.min(displayedLen + CHARS_PER_FRAME, rawBuffer.length);
        }

        if (now - lastRender >= RENDER_INTERVAL) {
            render();
            lastRender = now;
        }

        if (displayedLen < rawBuffer.length) {
            rafId = requestAnimationFrame(tick);
        } else {
            render();
            rafId = null;
        }
    }

    function ensureLoop() {
        if (rafId === null && !finished) {
            rafId = requestAnimationFrame(tick);
        }
    }

    return {
        push(text) {
            rawBuffer += text;
            ensureLoop();
        },
        stop() {
            finished = true;
            if (rafId !== null) {
                cancelAnimationFrame(rafId);
                rafId = null;
            }
            displayedLen = rawBuffer.length;
            render();
        },
        getFullText() {
            return rawBuffer;
        }
    };
}

async function sendMessage(text) {
    if (!text.trim() || isLoading) return;
    isLoading = true;

    const sendBtn = document.getElementById('send-btn');
    const chatInput = document.getElementById('chat-input');
    sendBtn.disabled = true;
    chatInput.value = '';

    addMessageToUI('user', text);

    const streamingEl = addStreamingMessage();
    const typewriter = createTypewriter(streamingEl);

    try {
        const res = await fetch(`${API}/chat/message/stream`, {
            method: 'POST',
            headers: headers(),
            body: JSON.stringify({
                message: text,
                mode: currentMode,
            }),
        });

        if (handleAuthError(res)) return;

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let agent = '';
        let score = null;
        let currentEl = streamingEl;
        let currentTypewriter = typewriter;
        let isFinalized = false;
        let sseBuffer = '';

        function finalizeCurrent() {
            if (isFinalized) return;
            currentTypewriter.stop();
            finalizeStreamingMessage(currentEl, currentTypewriter.getFullText(), agent);
            isFinalized = true;
        }

        function startNewBubble(newAgent) {
            if (!isFinalized && currentTypewriter.getFullText() === '') {
                agent = newAgent;
                updateStreamingAgent(currentEl, agent);
                return;
            }
            currentEl = addStreamingMessage();
            currentTypewriter = createTypewriter(currentEl);
            agent = newAgent;
            updateStreamingAgent(currentEl, agent);
            isFinalized = false;
        }

        function handleSseData(data) {
            if (data.agent) {
                if (isFinalized || agent === '') {
                    startNewBubble(data.agent);
                } else if (data.agent !== agent) {
                    finalizeCurrent();
                    startNewBubble(data.agent);
                } else {
                    updateStreamingAgent(currentEl, agent);
                }
            }

            if (data.token) {
                currentTypewriter.push(data.token);
            }

            if (data.done) {
                score = data.score || null;
                if (data.points && data.points > 0) {
                    showScoreNotification(data.points, data.total_score);
                }
                if (data.agent_done) {
                    finalizeCurrent();
                }
            }
        }

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            sseBuffer += decoder.decode(value, { stream: true });

            let lineEnd;
            while ((lineEnd = sseBuffer.indexOf('\n')) !== -1) {
                const line = sseBuffer.slice(0, lineEnd).trim();
                sseBuffer = sseBuffer.slice(lineEnd + 1);

                if (!line.startsWith('data: ')) continue;
                try {
                    const data = JSON.parse(line.slice(6));
                    handleSseData(data);
                } catch (e) {
                    console.warn('SSE parse error:', e, line);
                }
            }
        }

        if (!isFinalized) {
            currentTypewriter.stop();
            finalizeStreamingMessage(currentEl, currentTypewriter.getFullText(), agent);
        }
        await refreshProfile();
    } catch (err) {
        typewriter.stop();
        streamingEl.remove();
        addMessageToUI('assistant', 'Ошибка подключения к серверу.', 'SYSTEM');
    }

    isLoading = false;
    sendBtn.disabled = false;
    chatInput.focus();
}

function addMessageToUI(role, content, agent = '') {
    const messages = document.getElementById('chat-messages');
    const welcome = document.getElementById('welcome-screen');
    if (welcome) welcome.style.display = 'none';

    const msgDiv = document.createElement('div');
    msgDiv.className = `message message-${role}`;

    if (role === 'user') {
        msgDiv.innerHTML = `<div class="message-bubble">${escapeHtml(content)}</div>`;
    } else {
        const avatarClass = agent === 'PROFILER' ? 'avatar-profiler' :
                            agent === 'EVALUATOR' ? 'avatar-evaluator' : 'avatar-tutor';
        const avatarEmoji = agent === 'PROFILER' ? '🔍' :
                           agent === 'EVALUATOR' ? '⭐' : '📚';
        const labelClass = agent === 'PROFILER' ? 'agent-label-profiler' :
                          agent === 'EVALUATOR' ? 'agent-label-evaluator' : 'agent-label-tutor';
        const agentName = agent === 'PROFILER' ? 'Профайлер' :
                         agent === 'EVALUATOR' ? 'Оценщик' :
                         agent === 'TUTOR' ? 'Тьютор' : 'Система';

        msgDiv.innerHTML = `
            <div class="agent-avatar ${avatarClass}">${avatarEmoji}</div>
            <div>
                <div class="agent-label ${labelClass}">${agentName}</div>
                <div class="message-bubble">${renderMarkdown(content)}</div>
            </div>
        `;
    }

    messages.appendChild(msgDiv);
    scrollToBottom();
}

function addStreamingMessage() {
    const messages = document.getElementById('chat-messages');
    const welcome = document.getElementById('welcome-screen');
    if (welcome) welcome.style.display = 'none';

    const msgDiv = document.createElement('div');
    msgDiv.className = 'message message-assistant';
    msgDiv.innerHTML = `
        <div class="agent-avatar avatar-tutor">📚</div>
        <div>
            <div class="agent-label agent-label-tutor">Тьютор</div>
            <div class="message-bubble">
                <div class="typing-indicator"><span></span><span></span><span></span></div>
            </div>
        </div>
    `;
    messages.appendChild(msgDiv);
    scrollToBottom();
    return msgDiv;
}

function updateStreamingAgent(el, agent) {
    const avatar = el.querySelector('.agent-avatar');
    const label = el.querySelector('.agent-label');
    const configs = {
        PROFILER: { cls: 'avatar-profiler', emoji: '🔍', label: 'Профайлер', labelCls: 'agent-label-profiler' },
        EVALUATOR: { cls: 'avatar-evaluator', emoji: '⭐', label: 'Оценщик', labelCls: 'agent-label-evaluator' },
        TUTOR: { cls: 'avatar-tutor', emoji: '📚', label: 'Тьютор', labelCls: 'agent-label-tutor' },
    };
    const cfg = configs[agent] || configs.TUTOR;
    avatar.className = `agent-avatar ${cfg.cls}`;
    avatar.textContent = cfg.emoji;
    label.className = `agent-label ${cfg.labelCls}`;
    label.textContent = cfg.label;
}

function finalizeStreamingMessage(el, text, agent) {
    updateStreamingAgent(el, agent);
    const bubble = el.querySelector('.message-bubble');
    if (bubble && text) {
        bubble.innerHTML = renderMarkdown(text);
    }
    scrollToBottom();
}

function scrollToBottom() {
    const container = document.getElementById('chat-container');
    container.scrollTop = container.scrollHeight;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function renderMarkdown(text) {
    let html = escapeHtml(text);

    html = html.replace(/\[ОЖИДАЕТСЯ ОТВЕТ\]/g, '');
    html = html.replace(/\[ОЖИДАЕТСЯ ВЫБОР\]/g, '');
    html = html.replace(/\[ОЖИДАЕТСЯ УТОЧНЕНИЕ\]/g, '');

    html = html.replace(/```(\w*)\n?([\s\S]*?)```/g, '<pre><code>$2</code></pre>');
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
    html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
    html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
    html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>');
    html = html.replace(/^- (.+)$/gm, '<li>$1</li>');
    html = html.replace(/^\d+\. (.+)$/gm, '<li>$1</li>');
    html = html.replace(/\n\n/g, '</p><p>');
    html = html.replace(/\n/g, '<br>');

    return html;
}

let _refreshTimer = null;

async function refreshProfile() {
    if (_refreshTimer) clearTimeout(_refreshTimer);
    _refreshTimer = setTimeout(async () => {
        try {
            const profile = await fetchProfile();
            if (!profile) return;
            const progress = await fetchProgress();
            window._lastProfile = profile;
            window._lastProgress = progress;
            updateUI(profile, progress);
        } catch {
            // non-critical — UI already rendered
        }
    }, 300);
}

function showScoreNotification(points, totalScore) {
    const existing = document.querySelector('.score-notification');
    if (existing) existing.remove();

    const notif = document.createElement('div');
    notif.className = 'score-notification';
    notif.innerHTML = `⭐ +${points} баллов<div class="score-notification-total">Всего: ${totalScore}</div>`;
    document.body.appendChild(notif);

    setTimeout(() => { notif.classList.add('score-notification-hide'); }, 2500);
    setTimeout(() => { notif.remove(); }, 3000);
}

function formatDuration(days) {
    if (days === 0) return 'Сегодня';
    if (days === 1) return '1 день';
    if (days >= 2 && days <= 4) return `${days} дня`;
    if (days <= 30) return `${days} дней`;
    const months = Math.round(days / 30);
    if (months === 1) return '~1 месяц';
    if (months >= 2 && months <= 4) return `~${months} месяца`;
    return `~${months} месяцев`;
}

function openStatsModal() {
    const profile = window._lastProfile;
    const progress = window._lastProgress;
    if (!profile) return;

    document.getElementById('stats-username').textContent = profile.username;
    document.getElementById('stat-tasks').textContent = profile.tasks_count;
    document.getElementById('stat-modules').textContent = `${profile.modules_completed}/${profile.modules_total}`;
    document.getElementById('stats-total-score').textContent = profile.total_score;

    if (profile.created_at) {
        const days = Math.floor((Date.now() - new Date(profile.created_at)) / 86400000);
        document.getElementById('stat-days').textContent = formatDuration(days);
    } else {
        document.getElementById('stat-days').textContent = '—';
    }

    const progressMap = {};
    (progress || []).forEach(p => { progressMap[p.module_id] = p; });

    const listEl = document.getElementById('stats-progress-list');
    listEl.innerHTML = MODULE_META.map(m => {
        const p = progressMap[m.id];
        const score = p ? p.score : 0;
        const maxScore = p ? p.max_score : 50;
        const pct = maxScore > 0 ? Math.round(score / maxScore * 100) : 0;
        const completed = p && p.completed;
        return `
            <div class="progress-bar-container">
                <div class="progress-label">
                    <span>${completed ? '✅' : m.icon} ${m.name}</span>
                    <span>${score}/${maxScore}</span>
                </div>
                <div class="progress-bar">
                    <div class="progress-bar-fill" style="width: ${pct}%"></div>
                </div>
            </div>
        `;
    }).join('');

    document.getElementById('stats-modal').classList.add('modal-open');
}

function closeStatsModal() {
    document.getElementById('stats-modal').classList.remove('modal-open');
}

async function init() {
    const token = getToken();
    if (!token) {
        window.location.href = '/login';
        return;
    }

    const chatInput = document.getElementById('chat-input');
    const sendBtn = document.getElementById('send-btn');

    chatInput.addEventListener('input', () => {
        chatInput.style.height = 'auto';
        chatInput.style.height = chatInput.scrollHeight + 'px';
        sendBtn.disabled = !chatInput.value.trim();
    });

    chatInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage(chatInput.value);
        }
    });

    sendBtn.addEventListener('click', () => {
        sendMessage(chatInput.value);
    });

    document.getElementById('start-btn').addEventListener('click', () => {
        const isReturning = window._lastProfile && window._lastProfile.level && window._lastProfile.level !== '';
        sendMessage(isReturning ? 'Хочу продолжить обучение' : 'Привет! Я хочу научиться писать хорошие промпты для AI.');
    });

    document.getElementById('logout-btn').addEventListener('click', (e) => {
        e.preventDefault();
        localStorage.removeItem('token');
        window.location.href = '/login';
    });

    document.getElementById('nav-lessons').addEventListener('click', (e) => {
        e.preventDefault();
        sendMessage('Хочу вернуться к урокам');
    });

    document.getElementById('nav-prompt-up').addEventListener('click', (e) => {
        e.preventDefault();
        sendMessage('Хочу перейти в режим Prompt Up');
    });

    document.getElementById('nav-stats').addEventListener('click', (e) => {
        e.preventDefault();
        openStatsModal();
    });

    document.getElementById('stats-modal-close').addEventListener('click', () => {
        closeStatsModal();
    });

    document.getElementById('stats-modal').addEventListener('click', (e) => {
        if (e.target === document.getElementById('stats-modal')) {
            closeStatsModal();
        }
    });

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeStatsModal();
    });

    chatInput.focus();

    await refreshProfile();
}

document.addEventListener('DOMContentLoaded', init);
