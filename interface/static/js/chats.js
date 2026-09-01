/**
 * JavaScript для управления чатами проекта
 */

// Глобальные переменные
let currentProjectId = null;
let currentChatId = null;
let searchRequests = [];
let instructionFiles = [];

// Инициализация при загрузке страницы
document.addEventListener('DOMContentLoaded', () => {
    // Получаем project_id из глобальной переменной, установленной в шаблоне
    if (typeof window.PROJECT_ID !== 'undefined' && window.PROJECT_ID) {
        currentProjectId = parseInt(window.PROJECT_ID);
    }
    
    // Если не нашли в глобальной переменной, пробуем извлечь из URL
    if (!currentProjectId) {
        const pathParts = window.location.pathname.split('/');
        const chatsIndex = pathParts.indexOf('chats');
        if (chatsIndex > 0 && pathParts[chatsIndex - 1]) {
            currentProjectId = parseInt(pathParts[chatsIndex - 1]);
        }
    }
    
    // Если не нашли в пути, пробуем query параметр
    if (!currentProjectId) {
        const urlParams = new URLSearchParams(window.location.search);
        currentProjectId = parseInt(urlParams.get('project_id'));
    }
    
    if (!currentProjectId) {
        showNotification('Ошибка: ID проекта не указан', 'error');
        return;
    }
    
    // Обновляем ссылку в хлебных крошках
    updateBreadcrumb();
    
    // Загружаем данные
    loadProjectInfo();
    loadChats();
    loadSearchRequests();
    loadInstructionFiles();
});

// Обновление хлебных крошек
function updateBreadcrumb() {
    const breadcrumbLink = document.getElementById('breadcrumb-project-name');
    if (breadcrumbLink) {
        breadcrumbLink.href = `/project/${currentProjectId}`;
        breadcrumbLink.textContent = `Проект #${currentProjectId}`;
    }
}

// Загрузка информации о проекте
async function loadProjectInfo() {
    try {
        const response = await fetch(`/api/projects/${currentProjectId}`);
        if (!response.ok) throw new Error('Проект не найден');
        
        const project = await response.json();
        const projectInfoEl = document.getElementById('project-info');
        
        projectInfoEl.innerHTML = `
            <div class="info-card">
                <h3>${escapeHtml(project.name)}</h3>
                <p>${escapeHtml(project.description || 'Нет описания')}</p>
            </div>
        `;
        
        document.title = `Чаты - ${project.name}`;
        document.getElementById('breadcrumb-project-name').textContent = project.name;
    } catch (error) {
        console.error('Ошибка загрузки проекта:', error);
        document.getElementById('project-info').innerHTML = `
            <div class="error-message">Ошибка загрузки информации о проекте</div>
        `;
    }
}

// Загрузка списка чатов
async function loadChats() {
    try {
        const response = await fetch(`/api/llm/sessions?project_id=${currentProjectId}&active_only=true`);
        if (!response.ok) throw new Error('Ошибка загрузки чатов');
        
        const data = await response.json();
        const chatsListEl = document.getElementById('chats-list');
        
        if (data.sessions.length === 0) {
            chatsListEl.innerHTML = `
                <div class="empty-state">
                    <p>У этого проекта пока нет чатов</p>
                    <button onclick="openCreateChatModal()" class="btn btn-primary">Создать первый чат</button>
                </div>
            `;
            return;
        }
        
        chatsListEl.innerHTML = data.sessions.map(session => `
            <div class="chat-card" onclick="openChatDetail(${session.session_id})">
                <div class="chat-card-header">
                    <h3>${escapeHtml(session.title)}</h3>
                    <div class="chat-card-actions">
                        <button onclick="event.stopPropagation(); openEditChatModal(${session.session_id})" 
                                class="btn-icon" title="Настройки">⚙️</button>
                        <button onclick="event.stopPropagation(); openChatDetail(${session.session_id})" 
                                class="btn-icon" title="Открыть чат">💬</button>
                    </div>
                </div>
                <p class="chat-description">${escapeHtml(session.description || 'Нет описания')}</p>
                <div class="chat-meta">
                    <span class="meta-item">📝 Сообщений: ${session.messages_count}</span>
                    <span class="meta-item">📅 Создан: ${formatDate(session.created_at)}</span>
                    <span class="meta-item">🔄 Обновлен: ${formatDate(session.updated_at)}</span>
                </div>
                ${session.max_context_length > 0 ? `<span class="chat-context-badge">Контекст: ${session.max_context_length} сообщ.</span>` : ''}
            </div>
        `).join('');
    } catch (error) {
        console.error('Ошибка загрузки чатов:', error);
        document.getElementById('chats-list').innerHTML = `
            <div class="error-message">Ошибка загрузки чатов: ${error.message}</div>
        `;
    }
}

// Загрузка поисковых запросов для фильтрации
async function loadSearchRequests() {
    try {
        const response = await fetch(`/api/database/search-requests?project_id=${currentProjectId}`);
        if (!response.ok) throw new Error('Ошибка загрузки запросов');
        
        searchRequests = await response.json();
        
        // Заполняем контейнеры с чекбоксами в модальных окнах
        renderRequestFiltersCheckboxes('chat-request-filters-container', []);
        renderRequestFiltersCheckboxes('edit-chat-request-filters-container', []);
    } catch (error) {
        console.error('Ошибка загрузки запросов:', error);
        document.getElementById('chat-request-filters-container').innerHTML = 
            '<div class="error-message">Ошибка загрузки запросов</div>';
        document.getElementById('edit-chat-request-filters-container').innerHTML = 
            '<div class="error-message">Ошибка загрузки запросов</div>';
    }
}

// Рендеринг чекбоксов для фильтрации по запросам
function renderRequestFiltersCheckboxes(containerId, selectedIds = []) {
    const container = document.getElementById(containerId);
    if (!container || searchRequests.length === 0) {
        if (container) {
            container.innerHTML = '<div class="empty-state">Нет доступных запросов</div>';
        }
        return;
    }
    
    const html = searchRequests.map(req => {
        const isSelected = selectedIds.includes(req.id) || selectedIds.includes(String(req.id));
        return `
            <label class="checkbox-item" style="display: flex; align-items: center; margin: 5px 0; padding: 5px; border-radius: 4px; cursor: pointer;">
                <input type="checkbox" name="request_filters" value="${req.id}" ${isSelected ? 'checked' : ''} 
                       style="margin-right: 8px; width: auto;">
                <span>Запрос #${req.id}: ${escapeHtml(req.query || 'Без названия')}</span>
            </label>
        `;
    }).join('');
    
    container.innerHTML = html;
}

// Загрузка списка файлов инструкций
async function loadInstructionFiles() {
    try {
        // Временная заглушка - в реальности нужно сделать endpoint для списка файлов
        const response = await fetch('/api/llm/instructions/list');
        if (!response.ok) throw new Error('Ошибка загрузки инструкций');
        
        instructionFiles = await response.json();
        
        const createSelect = document.getElementById('chat-instructions-file');
        const editSelect = document.getElementById('edit-chat-instructions-file');
        
        const options = instructionFiles.map(file => 
            `<option value="${file.path}">${file.name}</option>`
        ).join('');
        
        createSelect.innerHTML = '<option value="">-- Без инструкций --</option>' + options;
        editSelect.innerHTML = '<option value="">-- Без инструкций --</option>' + options;
    } catch (error) {
        // Файлы инструкций опциональны
        console.log('Файлы инструкций не загружены:', error);
        const createSelect = document.getElementById('chat-instructions-file');
        const editSelect = document.getElementById('edit-chat-instructions-file');
        createSelect.innerHTML = '<option value="">-- Нет доступных файлов --</option>';
        editSelect.innerHTML = '<option value="">-- Нет доступных файлов --</option>';
    }
}

// Открытие модального окна создания чата
function openCreateChatModal() {
    document.getElementById('create-chat-form').reset();
    renderRequestFiltersCheckboxes('chat-request-filters-container', []);
    openModal('create-chat-modal');
}

// Создание чата
async function handleCreateChat(event) {
    event.preventDefault();
    
    // Собираем данные из чекбоксов
    const checkboxes = document.querySelectorAll('#chat-request-filters-container input[type="checkbox"]:checked');
    const selectedRequestIds = Array.from(checkboxes).map(cb => parseInt(cb.value)).filter(id => id);
    
    const requestData = {
        project_id: currentProjectId,
        title: document.getElementById('chat-title').value,
        description: document.getElementById('chat-description').value || null,
        max_context_length: parseInt(document.getElementById('chat-max-context').value) || 0,
        request_filters: selectedRequestIds,
        instructions_file_path: document.getElementById('chat-instructions-file').value || null
    };
    
    try {
        const response = await fetch('/api/llm/sessions/create', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(requestData)
        });
        
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Ошибка создания чата');
        }
        
        const result = await response.json();
        showNotification(`Чат "${result.title}" успешно создан`, 'success');
        closeModal('create-chat-modal');
        loadChats();
        
        // Переход к созданному чату
        setTimeout(() => {
            openChatDetail(result.session_id);
        }, 500);
    } catch (error) {
        handleError(error, 'Ошибка создания чата');
    }
}

// Открытие модального окна редактирования чата
async function openEditChatModal(chatId) {
    currentChatId = chatId;
    
    try {
        const response = await fetch(`/api/llm/session/${chatId}/details`);
        if (!response.ok) throw new Error('Чат не найден');
        
        const chat = await response.json();
        
        document.getElementById('edit-chat-id').value = chat.session_id;
        document.getElementById('edit-chat-title').value = chat.title;
        document.getElementById('edit-chat-description').value = chat.description || '';
        document.getElementById('edit-chat-max-context').value = chat.max_context_length || 0;
        
        // Выбираем текущие фильтры запросов
        const selectedRequestIds = chat.request_filters || [];
        renderRequestFiltersCheckboxes('edit-chat-request-filters-container', selectedRequestIds);
        
        // Выбираем текущий файл инструкций
        const instructionsSelect = document.getElementById('edit-chat-instructions-file');
        if (chat.instructions_file_path) {
            instructionsSelect.value = chat.instructions_file_path;
        }
        
        openModal('edit-chat-modal');
    } catch (error) {
        handleError(error, 'Ошибка загрузки настроек чата');
    }
}

// Обновление чата
async function handleUpdateChat(event) {
    event.preventDefault();
    
    // Собираем данные из чекбоксов
    const checkboxes = document.querySelectorAll('#edit-chat-request-filters-container input[type="checkbox"]:checked');
    const selectedRequestIds = Array.from(checkboxes).map(cb => parseInt(cb.value)).filter(id => id);
    
    const requestData = {
        title: document.getElementById('edit-chat-title').value,
        description: document.getElementById('edit-chat-description').value || null,
        max_context_length: parseInt(document.getElementById('edit-chat-max-context').value) || 0,
        request_filters: selectedRequestIds,
        instructions_file_path: document.getElementById('edit-chat-instructions-file').value || null
    };
    
    try {
        const response = await fetch(`/api/llm/sessions/${currentChatId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(requestData)
        });
        
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Ошибка обновления чата');
        }
        
        const result = await response.json();
        showNotification(`Чат "${result.title}" успешно обновлен`, 'success');
        closeModal('edit-chat-modal');
        loadChats();
    } catch (error) {
        handleError(error, 'Ошибка обновления чата');
    }
}

// Подтверждение удаления чата
function confirmDeleteChat() {
    openModal('delete-confirm-modal');
}

// Удаление чата
async function deleteChat() {
    if (!currentChatId) return;
    
    try {
        const response = await fetch(`/api/llm/sessions/${currentChatId}`, {
            method: 'DELETE'
        });
        
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Ошибка удаления чата');
        }
        
        showNotification('Чат успешно удален', 'success');
        closeModal('delete-confirm-modal');
        closeModal('edit-chat-modal');
        loadChats();
    } catch (error) {
        handleError(error, 'Ошибка удаления чата');
    }
}

// Открытие детального просмотра чата
function openChatDetail(chatId) {
    window.location.href = `/chat/${chatId}`;
}

// Экранирование HTML
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
