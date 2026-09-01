/**
 * JavaScript для интерфейса чата с LLM
 */

// Глобальные переменные
let currentChatId = null;
let currentProjectId = null;
let isModelLoading = false;
let modelCheckInterval = null;
let progressEventSource = null;

// Инициализация при загрузке страницы
document.addEventListener('DOMContentLoaded', () => {
    // Получаем chat_id из URL
    const pathParts = window.location.pathname.split('/');
    const chatIndex = pathParts.indexOf('chat');
    if (chatIndex > 0 && pathParts[chatIndex + 1]) {
        currentChatId = parseInt(pathParts[chatIndex + 1]);
    }

    if (!currentChatId) {
        showNotification('Ошибка: ID чата не указан', 'error');
        return;
    }

    // Загружаем данные чата
    loadChatInfo();
    loadMessages();
});

// Загрузка информации о чате
async function loadChatInfo() {
    try {
        const response = await fetch(`/api/llm/session/${currentChatId}/details`);
        if (!response.ok) throw new Error('Чат не найден');

        const chat = await response.json();
        currentProjectId = chat.project_id;

        document.getElementById('chat-title').textContent = chat.title;
        document.getElementById('breadcrumb-chat-title').textContent = chat.title;
        document.getElementById('chat-description').textContent = chat.description || '';

        // Обновляем ссылки в хлебных крошках и кнопке назад
        const projectNameLink = document.getElementById('breadcrumb-project-name');
        const chatsListLink = document.getElementById('breadcrumb-chats-list');
        const backToChatsBtn = document.getElementById('back-to-chats-btn');

        if (projectNameLink) {
            projectNameLink.href = `/project/${currentProjectId}`;
            projectNameLink.textContent = `Проект #${currentProjectId}`;
        }

        if (chatsListLink) {
            chatsListLink.href = `/projects/${currentProjectId}/chats`;
            chatsListLink.textContent = 'Чаты';
        }

        if (backToChatsBtn) {
            backToChatsBtn.onclick = () => {
                goBackToChats();
            };
        }

        document.title = `Чат: ${chat.title}`;
    } catch (error) {
        console.error('Ошибка загрузки чата:', error);
        showNotification('Ошибка загрузки информации о чате', 'error');
    }
}

// Функция возврата к списку чатов
function goBackToChats() {
    if (currentProjectId) {
        window.location.href = `/projects/${currentProjectId}/chats`;
    } else {
        window.history.back();
    }
}

// Загрузка сообщений чата
async function loadMessages() {
    try {
        const response = await fetch(`/api/llm/sessions/${currentChatId}/messages?limit=100`);
        if (!response.ok) throw new Error('Ошибка загрузки сообщений');

        const messages = await response.json();
        const messagesContainer = document.getElementById('chat-messages');

        if (messages.length === 0) {
            messagesContainer.innerHTML = `
                <div class="empty-state">
                    <p>Это начало вашего диалога</p>
                    <p>Задайте первый вопрос!</p>
                </div>
            `;
            return;
        }

        messagesContainer.innerHTML = messages.map(msg => `
            <div class="message ${msg.role}">
                <div class="message-header">
                    <span class="message-role">${msg.role === 'user' ? '👤 Вы' : '🤖 Ассистент'}</span>
                    <span class="message-time">${formatDateTime(msg.created_at)}</span>
                </div>
                <div class="message-content">${escapeHtml(msg.content)}</div>
                ${msg.applied_filters ? `
                    <div class="message-filters">
                        <small>Примененные фильтры: ${JSON.stringify(msg.applied_filters)}</small>
                    </div>
                ` : ''}
                ${msg.sources_count > 0 ? `
                    <div class="message-sources">
                        <small>Источников: ${msg.sources_count}</small>
                    </div>
                ` : ''}
            </div>
        `).join('');

        scrollToBottom();
    } catch (error) {
        console.error('Ошибка загрузки сообщений:', error);
        document.getElementById('chat-messages').innerHTML = `
            <div class="error-message">Ошибка загрузки истории сообщений</div>
        `;
    }
}

// Проверка статуса модели
async function checkModelStatus() {
    try {
        const response = await fetch('/api/llm/model/status');
        if (!response.ok) return { model_loaded: false };

        const status = await response.json();
        updateModelStatusIndicator(status);
        return status;
    } catch (error) {
        console.error('Ошибка проверки статуса модели:', error);
        return { model_loaded: false };
    }
}

// Загрузка модели (если не загружена)
async function ensureModelLoaded() {
    let modelStatus = await checkModelStatus();
    if (modelStatus.model_loaded) return true;

    showModelLoadingIndicator();
    updateLoadingStatus('Инициализация загрузки модели...');

    try {
        await fetch('/api/llm/model/reload', { method: 'POST' });
    } catch (e) {
        console.warn('Ошибка вызова reload:', e);
    }

    let attempts = 0;
    const maxAttempts = 60;
    while (attempts < maxAttempts) {
        await new Promise(resolve => setTimeout(resolve, 2000));
        modelStatus = await checkModelStatus();
        if (modelStatus.model_loaded) {
            updateLoadingStatus('Модель загружена! Обработка запроса...');
            return true;
        }
        attempts++;
        updateLoadingStatus(`Загрузка модели... (${attempts}/${maxAttempts})`);
    }
    throw new Error('Превышено время ожидания загрузки модели.');
}

// Отправка обычного сообщения
async function sendMessage() {
    const input = document.getElementById('message-input');
    const message = input.value.trim();

    if (!message) {
        showNotification('Введите сообщение', 'warning');
        return;
    }

    const manualFilters = collectManualFilters();

    try {
        await ensureModelLoaded();

        addMessageToInterface('user', message);
        input.value = '';
        showTypingIndicator();

        // Формируем запрос с ОБЯЗАТЕЛЬНЫМ project_id
        const requestData = {
            query: message,
            project_id: currentProjectId,
            session_id: currentChatId
        };

        if (manualFilters && Object.keys(manualFilters).length > 0) {
            requestData.manual_filters = manualFilters;
        }

        // РАБОЧАЯ АДРЕСАЦИЯ
        const response = await fetch(`/api/llm/sessions/${currentChatId}/message`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(requestData)
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Ошибка отправки сообщения');
        }

        const result = await response.json();
        removeTypingIndicator();
        addMessageToInterface('assistant', result.answer || result.content, result.applied_filters, result.sources_count);
        scrollToBottom();

    } catch (error) {
        console.error('Ошибка отправки сообщения:', error);
        showNotification(error.message || 'Ошибка отправки сообщения', 'error');
        removeTypingIndicator();

        if (error.message.includes('модель') || error.message.includes('Model')) {
            showModelLoadingIndicator();
        }
    }
}

// Отправка сообщения с расширенной пакетной обработкой
// Отправка сообщения с расширенной пакетной обработкой
async function sendEnhancedMessage() {
    const input = document.getElementById('message-input');
    const message = input.value.trim();

    if (!message) {
        showNotification('Введите сообщение', 'warning');
        return;
    }

    const manualFilters = collectManualFilters();
    const enhancedBtn = document.getElementById('enhanced-send-btn');

    if (enhancedBtn) {
        enhancedBtn.disabled = true;
        enhancedBtn.textContent = 'Обработка...';
    }

    try {
        await ensureModelLoaded();

        addMessageToInterface('user', message);
        input.value = '';

        // Показываем расширенный индикатор прогресса
        showEnhancedProgressIndicator(currentChatId);

        // Формируем запрос с ОБЯЗАТЕЛЬНЫМ project_id
        const requestData = {
            query: message,
            project_id: currentProjectId,
            session_id: currentChatId
        };

        if (manualFilters && Object.keys(manualFilters).length > 0) {
            requestData.manual_filters = manualFilters;
        }

        // РАБОЧАЯ АДРЕСАЦИЯ
        const response = await fetch(`/api/llm/sessions/${currentChatId}/message/enhanced`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(requestData)
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Ошибка отправки сообщения');
        }

        const result = await response.json();

        removeEnhancedProgressIndicator();
        addMessageToInterface('assistant', result.answer || result.content, result.applied_filters, result.sources_count);

        if (result.batches_processed > 0) {
            showNotification(` Обработано ${result.batches_processed} пакетов, ${result.sources_count} записей`, 'success');
        }
        scrollToBottom();

    } catch (error) {
        console.error('Ошибка отправки сообщения (enhanced):', error);
        showNotification(error.message || 'Ошибка отправки сообщения', 'error');
        removeEnhancedProgressIndicator();

        if (error.message.includes('модель') || error.message.includes('Model')) {
            showModelLoadingIndicator();
        }
    } finally {
        if (enhancedBtn) {
            enhancedBtn.disabled = false;
            enhancedBtn.textContent = ' Отправить';
        }
    }
}

// Сбор ручных фильтров (улучшенная проверка на пустые значения)
function collectManualFilters() {
    const filters = {};

    const sentiment = document.getElementById('filter-sentiment').value;
    if (sentiment && sentiment !== 'any') filters.sentiment = sentiment;

    const keywords = document.getElementById('filter-keywords').value.trim();
    if (keywords) filters.keywords = keywords;

    const minLikes = document.getElementById('filter-min-likes').value;
    if (minLikes !== '') filters.min_likes = parseInt(minLikes, 10);

    const sortBy = document.getElementById('filter-sort').value;
    if (sortBy && sortBy !== 'default') filters.sort_by = sortBy;

    const limit = document.getElementById('filter-limit').value;
    if (limit !== '') filters.limit = parseInt(limit, 10);

    return Object.keys(filters).length > 0 ? filters : null;
}

// Добавление сообщения в интерфейс
function addMessageToInterface(role, content, appliedFilters = null, sourcesCount = 0) {
    const messagesContainer = document.getElementById('chat-messages');

    const emptyState = messagesContainer.querySelector('.empty-state');
    if (emptyState) emptyState.remove();

    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${role}`;
    messageDiv.innerHTML = `
        <div class="message-header">
            <span class="message-role">${role === 'user' ? '👤 Вы' : '🤖 Ассистент'}</span>
            <span class="message-time">${new Date().toLocaleString('ru-RU')}</span>
        </div>
        <div class="message-content">${escapeHtml(content)}</div>
        ${appliedFilters ? `
            <div class="message-filters">
                <small>Примененные фильтры: ${JSON.stringify(appliedFilters)}</small>
            </div>
        ` : ''}
        ${sourcesCount > 0 ? `
            <div class="message-sources">
                <small>Источников: ${sourcesCount}</small>
            </div>
        ` : ''}
    `;

    messagesContainer.appendChild(messageDiv);
    scrollToBottom();
}

// Показ индикатора "печатает..."
function showTypingIndicator(customText = null) {
    const messagesContainer = document.getElementById('chat-messages');
    const indicator = document.createElement('div');
    indicator.id = 'typing-indicator';
    indicator.className = 'message assistant typing';

    const textDisplay = customText || `
        <div class="typing-dots">
            <span></span><span></span><span></span>
        </div>
    `;

    indicator.innerHTML = `
        <div class="message-header">
            <span class="message-role">🤖 Ассистент</span>
        </div>
        <div class="typing-content">${textDisplay}</div>
    `;
    messagesContainer.appendChild(indicator);
    scrollToBottom();
}

// Удаление индикатора "печатает..."
function removeTypingIndicator() {
    const indicator = document.getElementById('typing-indicator');
    if (indicator) indicator.remove();
}

// Обновление индикатора статуса модели
function updateModelStatusIndicator(status) {
    const indicator = document.getElementById('model-status-indicator');
    if (!indicator) return;

    const statusDot = indicator.querySelector('.status-dot');
    const statusText = indicator.querySelector('.status-text');

    indicator.style.display = 'block';

    if (status.model_loaded) {
        if (statusDot) statusDot.className = 'status-dot ready';
        if (statusText) statusText.textContent = 'Модель готова к работе';
        hideModelLoadingIndicator();
    } else {
        if (statusDot) statusDot.className = 'status-dot loading';
        if (statusText) statusText.textContent = status.status || 'Модель загружается...';
    }
}

// Показ индикатора загрузки модели
function showModelLoadingIndicator() {
    const indicator = document.getElementById('model-loading-indicator');
    if (indicator) {
        indicator.style.display = 'block';
        const enhancedBtn = document.getElementById('enhanced-send-btn');
        if (enhancedBtn) enhancedBtn.disabled = true;
    }
}

// Скрытие индикатора загрузки модели
function hideModelLoadingIndicator() {
    const indicator = document.getElementById('model-loading-indicator');
    if (indicator) {
        indicator.style.display = 'none';
        const enhancedBtn = document.getElementById('enhanced-send-btn');
        if (enhancedBtn) enhancedBtn.disabled = false;
    }
}

// Переключение панели фильтров
function toggleManualFilters() {
    const panel = document.getElementById('manual-filters-panel');
    panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
}

// Обработка нажатия Enter
function handleKeyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        sendEnhancedMessage(); // Вызываем расширенную отправку
    }
}

// Обновление статуса загрузки модели
function updateLoadingStatus(statusText) {
    const statusElement = document.getElementById('loading-status');
    if (statusElement) {
        statusElement.textContent = statusText;
    }
}

// ============================================================
// ПРОГРЕСС-БАР (SSE)
// ============================================================
function showEnhancedProgressIndicator(sessionId) {
    const messagesContainer = document.getElementById('chat-messages');
    const progressDiv = document.createElement('div');

    // ИСПРАВЛЕНО: убраны пробелы в ID
    progressDiv.id = 'enhanced-progress-indicator';
    progressDiv.className = 'message assistant typing';

    // ИСПРАВЛЕНО: корректная, чистая шаблонная строка
    progressDiv.innerHTML = `
        <div class="message-header">
            <span class="message-role"> Ассистент</span>
        </div>
        <div class="progress-content">
            <div class="progress-info">
                <div class="progress-bar-container">
                    <div class="progress-bar" id="progress-bar" style="width: 0%"></div>
                </div>
                <div class="progress-text" id="progress-text">Инициализация пакетной обработки...</div>
            </div>
            <div class="progress-stats">
                <div class="stat-item">
                    <span class="stat-label">📊 Записей:</span>
                    <span class="stat-value" id="stat-posts">-</span>
                </div>
                <div class="stat-item">
                    <span class="stat-label">💬 Комментариев:</span>
                    <span class="stat-value" id="stat-comments">-</span>
                </div>
                <div class="stat-item">
                    <span class="stat-label">⏱️ Ожидание:</span>
                    <span class="stat-value" id="stat-time">-</span>
                </div>
            </div>
        </div>
    `;

    messagesContainer.appendChild(progressDiv);
    scrollToBottom();

    // Подключаемся к SSE
    connectToProgressStream(sessionId);
    }

function connectToProgressStream() {
    // 1. Формируем абсолютный URL с портом 8001
    const targetUrl = `http://localhost:8001/api/llm/sessions/${currentChatId}/enhanced-progress`;
    console.log("🔍 РЕАЛЬНЫЙ ЗАПРОС SSE К URL:", targetUrl, "| ID чата:", currentChatId);

    // 2. КРИТИЧЕСКИ ВАЖНО: передаем в EventSource именно targetUrl, а не относительный путь!
    const eventSource = new EventSource(targetUrl);
    progressEventSource = eventSource;

    eventSource.onopen = function() {
        console.log("✅ SSE: Соединение установлено");
    };

    eventSource.onmessage = function(event) {
        const data = JSON.parse(event.data);
        console.log("📡 ПОЛУЧЕНЫ ДАННЫЕ ПРОГРЕССА:", data); // Вы увидите это в консоли, если все работает

        // Обновляем значения в менюшке
        const progressBar = document.getElementById('progress-bar');
        const progressText = document.getElementById('progress-text');
        const statPosts = document.getElementById('stat-posts');
        const statComments = document.getElementById('stat-comments');
        const statTime = document.getElementById('stat-time');

        if (progressBar && progressText) {
            const percentage = data.total_batches > 0 ? (data.current_batch / data.total_batches) * 100 : 0;
            progressBar.style.width = `${percentage}%`;
            progressText.textContent = `Обработка пакета ${data.current_batch} из ${data.total_batches}`;

            if (statPosts) statPosts.textContent = data.total_posts || '0';
            if (statComments) statComments.textContent = data.total_comments || '0';

            if (statTime && data.estimated_seconds !== undefined) {
                const minutes = Math.floor(data.estimated_seconds / 60);
                const seconds = data.estimated_seconds % 60;
                statTime.textContent = minutes > 0 ? `${minutes} мин ${seconds} сек` : `${seconds} сек`;
            }
        }

        if (data.status === 'completed') {
            eventSource.close();
            progressEventSource = null;
        }
    };

    eventSource.onerror = function(error) {
        console.error("❌ SSE Ошибка (проверьте, что бэкенд перезагружен):", error);
        eventSource.close();
        progressEventSource = null;
    };
}

function updateProgressIndicator(data) {
    const progressBar = document.getElementById('progress-bar');
    const progressText = document.getElementById('progress-text');
    const statPosts = document.getElementById('stat-posts');
    const statComments = document.getElementById('stat-comments');
    const statTime = document.getElementById('stat-time');

    if (!progressBar || !progressText) return;

    // Обновляем прогресс-бар
    const percentage = data.total_batches > 0
        ? (data.current_batch / data.total_batches) * 100
        : 0;
    progressBar.style.width = `${percentage}%`;

    // Обновляем текст
    progressText.textContent = `Обработка пакета ${data.current_batch} из ${data.total_batches}`;

    // Обновляем статистику
    if (statPosts) statPosts.textContent = data.total_posts || '-';
    if (statComments) statComments.textContent = data.total_comments || '-';

    if (statTime && data.estimated_seconds !== undefined) {
        const minutes = Math.floor(data.estimated_seconds / 60);
        const seconds = data.estimated_seconds % 60;
        statTime.textContent = minutes > 0
            ? `${minutes} мин ${seconds} сек`
            : `${seconds} сек`;
    }
}

function removeEnhancedProgressIndicator() {
    const indicator = document.getElementById('enhanced-progress-indicator');
    if (indicator) indicator.remove();

    if (progressEventSource) {
        progressEventSource.close();
        progressEventSource = null;
    }
}

// ============================================================
// ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
// ============================================================
function scrollToBottom() {
    const messagesContainer = document.getElementById('chat-messages');
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
}

function formatDateTime(dateString) {
    if (!dateString) return '';
    const date = new Date(dateString);
    return date.toLocaleString('ru-RU', {
        hour: '2-digit',
        minute: '2-digit',
        day: 'numeric',
        month: 'short'
    });
}

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}