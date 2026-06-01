const API = '/api';

function getToken() {
    return localStorage.getItem('token');
}

function headers() {
    return {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${getToken()}`
    };
}

async function fetchProfile() {
    const res = await fetch(`${API}/profile/me`, { headers: headers() });
    if (res.status === 401) {
        window.location.href = '/login';
        return null;
    }
    return await res.json();
}

async function fetchProgress() {
    const res = await fetch(`${API}/profile/progress`, { headers: headers() });
    return await res.json();
}

async function fetchAssignments() {
    const res = await fetch(`${API}/assignments/`, { headers: headers() });
    return await res.json();
}

function updateUI(profile, progress) {
    if (!profile) return;

    document.getElementById('user-score').textContent = profile.total_score;
    document.getElementById('sidebar-score').textContent = profile.total_score;

    const levelBadge = document.getElementById('level-badge');
    const levels = {
        newbie: { label: '🌱 Новичок', cls: 'level-newbie' },
        intermediate: { label: '⚡ Средний', cls: 'level-intermediate' },
        advanced: { label: '🏆 Продвинутый', cls: 'level-advanced' },
    };
    const lvl = levels[profile.level] || levels.newbie;
    levelBadge.className = `level-badge ${lvl.cls}`;
    levelBadge.textContent = lvl.label;

    const progressList = document.getElementById('progress-list');
    if (progress && progress.length > 0) {
        progressList.innerHTML = progress.map(p => `
            <div class="progress-bar-container">
                <div class="progress-label">
                    <span>${p.module_name}</span>
                    <span>${p.score}/${p.max_score}</span>
                </div>
                <div class="progress-bar">
                    <div class="progress-bar-fill" style="width: ${p.max_score > 0 ? (p.score / p.max_score * 100) : 0}%"></div>
                </div>
            </div>
        `).join('');
    }

    const badgesList = document.getElementById('badges-list');
    if (profile.badges && profile.badges.length > 0) {
        badgesList.innerHTML = profile.badges.map(b => `
            <span class="badge-item">${b}</span>
        `).join('');
    }

    const modulesList = document.getElementById('modules-list');
    const moduleNames = [
        { id: 1, name: 'Структура промпта', icon: '🏗️', diff: 'newbie' },
        { id: 2, name: 'Улучшение промптов', icon: '🔧', diff: 'newbie' },
        { id: 3, name: 'Few-shot', icon: '🎯', diff: 'intermediate' },
        { id: 4, name: 'Chain-of-thought', icon: '🧠', diff: 'intermediate' },
        { id: 5, name: 'Форматирование', icon: '🎨', diff: 'advanced' },
        { id: 6, name: 'Комплексный промпт', icon: '🏆', diff: 'advanced' },
    ];
    const completedModules = (progress || []).filter(p => p.completed).map(p => p.module_id);
    modulesList.innerHTML = moduleNames.map(m => `
        <div class="module-item ${completedModules.includes(m.id) ? 'completed' : ''}" data-module="${m.id}">
            <div class="module-icon">${completedModules.includes(m.id) ? '✅' : m.icon}</div>
            <span>${m.name}</span>
        </div>
    `).join('');
}

let currentConversationId = null;
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
                conversation_id: currentConversationId,
                message: text,
            }),
        });

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let agent = 'TUTOR';
        let score = null;

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            const chunk = decoder.decode(value, { stream: true });
            const lines = chunk.split('\n');

            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                try {
                    const data = JSON.parse(line.slice(6));

                    if (data.agent) {
                        agent = data.agent;
                        updateStreamingAgent(streamingEl, agent);
                    }

                    if (data.token) {
                        typewriter.push(data.token);
                    }

                    if (data.done) {
                        score = data.score || null;
                    }
                } catch (e) {}
            }
        }

        typewriter.stop();
        finalizeStreamingMessage(streamingEl, typewriter.getFullText(), agent);
        currentConversationId = currentConversationId;
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

function updateStreamingText(el, text) {
    const bubble = el.querySelector('.message-bubble');
    const typing = el.querySelector('.typing-indicator');
    if (typing) typing.remove();
    bubble.innerHTML = renderMarkdown(text);
    scrollToBottom();
}

function finalizeStreamingMessage(el, text, agent) {
    updateStreamingAgent(el, agent);
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

async function refreshProfile() {
    const profile = await fetchProfile();
    const progress = await fetchProgress();
    updateUI(profile, progress);
}

async function init() {
    const token = getToken();
    if (!token) {
        window.location.href = '/login';
        return;
    }

    await refreshProfile();

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
        sendMessage('Привет! Я хочу научиться писать хорошие промпты для AI.');
    });

    document.getElementById('logout-btn').addEventListener('click', (e) => {
        e.preventDefault();
        localStorage.removeItem('token');
        window.location.href = '/login';
    });

    document.getElementById('nav-assignments').addEventListener('click', async (e) => {
        e.preventDefault();
        const assignments = await fetchAssignments();
        if (assignments && assignments.length > 0) {
            sendMessage(`Покажи мне список заданий по модулю ${assignments[0].module_id}`);
        }
    });

    chatInput.focus();
}

document.addEventListener('DOMContentLoaded', init);
