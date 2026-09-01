/**
 * Скрипт для страницы деталей проекта
 */

// Функция для экранирования HTML (защита от XSS)
function escapeHtml(text) {
    if (text === null || text === undefined) return '';
    const map = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
    };
    return String(text).replace(/[&<>"']/g, function(m) { return map[m]; });
}

const pathParts = window.location.pathname.split('/').filter(p => p);
const PROJECT_ID = pathParts[pathParts.length - 1] || pathParts[pathParts.length - 2];
let currentDeleteRequestId = null;
let deleteProjectMode = false;

// Загрузка данных при загрузке страницы
document.addEventListener('DOMContentLoaded', () => {
    loadProjectDetails();
    loadSearchRequests();
});

async function loadProjectDetails() {
    try {
        const response = await fetch(`/api/projects/${PROJECT_ID}`);
        if (!response.ok) throw new Error('Проект не найден');
        
        const project = await response.json();
        
        // Обновление заголовка и хлебных крошек
        document.getElementById('project-title').textContent = project.name;
        document.getElementById('breadcrumb-project-name').textContent = `Проект: ${project.name}`;
        document.title = `${project.name} - VK Analytics`;
        
        // Заполнение информации
        document.getElementById('project-description').textContent = project.description || '-';
        document.getElementById('project-keywords').textContent = formatKeywords(project.keywords) || '-';
        document.getElementById('project-model').textContent = project.model_name || '-';
        
        // Сохранение данных для редактирования
        window.currentProject = project;
        
    } catch (error) {
        handleError(error, 'Ошибка загрузки данных проекта');
        document.getElementById('project-title').textContent = 'Ошибка загрузки';
    }
}

async function loadSearchRequests() {
    const requestsList = document.getElementById('search-requests-list');
    if (!requestsList) return;

    try {
        const response = await fetch(`/api/projects/${PROJECT_ID}/search_requests/`);
        if (!response.ok) {
            throw new Error(`Ошибка ${response.status}: ${response.statusText}`);
        }
        
        const result = await response.json();
        
        // Обработка ответа в формате { total: N, data: [...] }
        const requests = result.data || result.items || result.results || (Array.isArray(result) ? result : []);

        // Обновление статистики
        document.getElementById('stat-requests').textContent = requests.length;
        
        // Подсчет постов и комментариев
        let totalPosts = 0;
        let totalComments = 0;
        requests.forEach(req => {
            totalPosts += req.posts_count || 0;
            totalComments += req.comments_count || 0;
        });
        document.getElementById('stat-posts').textContent = totalPosts;
        document.getElementById('stat-comments').textContent = totalComments;

        if (requests.length === 0) {
            requestsList.innerHTML = `
                <div class="empty-state">
                    <p>Поисковые запросы еще не созданы</p>
                    <p>Нажмите "Добавить запрос" чтобы создать первый запрос</p>
                </div>
            `;
            return;
        }

        requestsList.innerHTML = requests.map(request => createRequestCard(request)).join('');
    } catch (error) {
        console.error('Ошибка загрузки поисковых запросов:', error);
        handleError(error, 'Ошибка загрузки поисковых запросов');
        requestsList.innerHTML = '<div class="empty-state">Ошибка загрузки запросов</div>';
    }
}

function createRequestCard(request) {
    const postsCount = request.posts_count || 0;
    const commentsCount = request.comments_count || 0;
    const queryName = request.query || `Запрос #${request.id}`;
    
    // Статистика по тональности
    const negativeCount = request.negative_count || 0;
    const positiveCount = request.positive_count || 0;
    const neutralCount = request.neutral_count || 0;
    
    return `
        <div class="request-card">
            <div class="request-content" style="flex: 1;">
                <h3>${escapeHtml(queryName)}</h3>
                <div class="request-stats">
                    <span>📝 Постов: ${postsCount}</span>
                    <span>💬 Комментариев: ${commentsCount}</span>
                </div>
                <div class="request-emote-stats" style="margin-top: 8px; font-size: 0.9em; color: #666;">
                    <span style="color: #dc3545;">🔴 NEG: ${negativeCount}</span>
                    <span style="margin: 0 8px;">|</span>
                    <span style="color: #28a745;">🟢 POS: ${positiveCount}</span>
                    <span style="margin: 0 8px;">|</span>
                    <span style="color: #6c757d;">⚪ NEU: ${neutralCount}</span>
                </div>
            </div>
            <div class="request-actions">
                <button class="btn btn-secondary" onclick="runSearchRequest(${request.id}, this)" title="Запустить сбор данных">
                    ▶️
                </button>
                <button class="btn btn-secondary" onclick="openEditRequestModal(${request.id})" title="Редактировать">
                    ⚙️
                </button>
                <button class="btn btn-secondary" onclick="openRequestCommentsModal(${request.id})" title="Комментарии запроса">
                    💬
                </button>
                <button class="btn btn-danger" onclick="confirmDeleteRequest(${request.id})" title="Удалить">
                    ✖️
                </button>
            </div>
        </div>
    `;
}

// === Управление проектом ===

function openEditProjectModal() {
    const project = window.currentProject;
    if (!project) return;
    
    document.getElementById('edit-project-id').value = project.id;
    document.getElementById('edit-project-name').value = project.name;
    document.getElementById('edit-project-description').value = project.description || '';
    document.getElementById('edit-project-keywords').value = formatKeywords(project.keywords);
    document.getElementById('edit-project-model').value = project.model_name || '';
    
    openModal('edit-project-modal');
}

async function handleUpdateProject(event) {
    event.preventDefault();
    
    const projectId = document.getElementById('edit-project-id').value;
    const projectData = {
        name: document.getElementById('edit-project-name').value,
        description: document.getElementById('edit-project-description').value || null,
        keywords: parseKeywords(document.getElementById('edit-project-keywords').value),
        model_name: document.getElementById('edit-project-model').value || null
    };

    try {
        const response = await fetch(`/api/projects/${projectId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(projectData)
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Ошибка обновления проекта');
        }

        closeModal('edit-project-modal');
        showNotification('Проект успешно обновлен', 'success');
        loadProjectDetails();
    } catch (error) {
        handleError(error, 'Ошибка обновления проекта');
    }
}

function confirmDeleteProject() {
    deleteProjectMode = true;
    document.getElementById('delete-confirm-text').textContent = 
        `Вы уверены, что хотите удалить проект "${window.currentProject?.name}"?`;
    
    const deleteBtn = document.getElementById('confirm-delete-action-btn');
    deleteBtn.onclick = deleteProject;
    
    openModal('delete-confirm-modal');
}

async function deleteProject() {
    if (!deleteProjectMode || !window.currentProject) return;

    try {
        const response = await fetch(`/api/projects/${window.currentProject.id}`, {
            method: 'DELETE'
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Ошибка удаления проекта');
        }

        closeModal('delete-confirm-modal');
        showNotification('Проект успешно удален', 'success');
        
        // Перенаправление на страницу проектов
        setTimeout(() => {
            window.location.href = '/projects';
        }, 1000);
    } catch (error) {
        handleError(error, 'Ошибка удаления проекта');
    }
}

// === Управление поисковыми запросами ===

function openCreateRequestModal() {
    document.getElementById('create-request-form').reset();
    openModal('create-request-modal');
}

async function handleCreateRequest(event) {
    event.preventDefault();

    const formData = new FormData(event.target);

    // Сбор данных формы с обработкой пустых значений как null
    const requestData = {
        query: formData.get('query') || null,
        extended: formData.get('extended') === 'on',
        count: parseInt(formData.get('count')) || 100,
        start_time: formData.get('start_time') || null,
        end_time: formData.get('end_time') || null,
        params_json: formData.get('params_json') ? JSON.parse(formData.get('params_json')) : null
    };

    try {
        const response = await fetch(`/api/projects/${PROJECT_ID}/search_requests/`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(requestData)
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Ошибка создания запроса');
        }

        closeModal('create-request-modal');
        showNotification('Запрос успешно создан', 'success');
        loadSearchRequests();
    } catch (error) {
        handleError(error, 'Ошибка создания запроса');
    }
}

async function openEditRequestModal(requestId) {
    try {
        const response = await fetch(`/api/projects/${PROJECT_ID}/search_requests/`);
        if (!response.ok) throw new Error('Ошибка загрузки запросов');

        const result = await response.json();
        const requests = result.data || result.items || result.results || (Array.isArray(result) ? result : []);
        const request = requests.find(r => r.id === requestId);

        if (!request) throw new Error('Запрос не найден');

        document.getElementById('edit-request-id').value = request.id;
        document.getElementById('edit-request-query').value = request.query || '';
        document.getElementById('edit-request-extended').checked = request.extended || false;
        document.getElementById('edit-request-count').value = request.count || 100;
        document.getElementById('edit-request-start-time').value = request.start_time ? new Date(request.start_time).toISOString().slice(0, 16) : '';
        document.getElementById('edit-request-end-time').value = request.end_time ? new Date(request.end_time).toISOString().slice(0, 16) : '';
        document.getElementById('edit-request-params-json').value = request.params_json ? JSON.stringify(request.params_json) : '';

        openModal('edit-request-modal');
    } catch (error) {
        handleError(error, 'Ошибка загрузки данных запроса');
    }
}
async function handleUpdateRequest(event) {
    event.preventDefault();

    const requestId = document.getElementById('edit-request-id').value;
    const requestData = {
        query: document.getElementById('edit-request-query').value || null,
        extended: document.getElementById('edit-request-extended').checked,
        count: parseInt(document.getElementById('edit-request-count').value) || 100,
        start_time: document.getElementById('edit-request-start-time').value || null,
        end_time: document.getElementById('edit-request-end-time').value || null,
        params_json: document.getElementById('edit-request-params-json').value ? JSON.parse(document.getElementById('edit-request-params-json').value) : null
    };

    try {
        const response = await fetch(`/api/projects/${PROJECT_ID}/search_requests/${requestId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(requestData)
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Ошибка обновления запроса');
        }

        closeModal('edit-request-modal');
        showNotification('Запрос успешно обновлен', 'success');
        loadSearchRequests();
    } catch (error) {
        handleError(error, 'Ошибка обновления запроса');
    }
}

function confirmDeleteRequest(requestId) {
    currentDeleteRequestId = requestId;
    deleteProjectMode = false;
    
    document.getElementById('delete-confirm-text').textContent = 
        'Вы уверены, что хотите удалить этот поисковый запрос?';
    
    const deleteBtn = document.getElementById('confirm-delete-action-btn');
    deleteBtn.onclick = deleteRequest;
    
    openModal('delete-confirm-modal');
}

async function deleteRequest() {
    if (deleteProjectMode || !currentDeleteRequestId) return;

    try {
        const response = await fetch(`/api/projects/${PROJECT_ID}/search_requests/${currentDeleteRequestId}`, {
            method: 'DELETE'
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Ошибка удаления запроса');
        }

        closeModal('delete-confirm-modal');
        showNotification('Запрос успешно удален', 'success');
        currentDeleteRequestId = null;
        loadSearchRequests();
    } catch (error) {
        handleError(error, 'Ошибка удаления запроса');
    }
}

// === Запуск сбора данных для поискового запроса ===

async function runSearchRequest(requestId, buttonElement) {
    // Показываем индикатор загрузки на кнопке
    const originalText = buttonElement.innerHTML;
    buttonElement.innerHTML = '⏳';
    buttonElement.disabled = true;
    
    try {
        const response = await fetch(`/api/vk/search-requests/${requestId}/run`, {
            method: 'POST'
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Ошибка запуска сбора данных');
        }

        const result = await response.json();
        showNotification(result.message || 'Задача запущена в фоновом режиме', 'success');
        
        // Обновляем статистику через некоторое время
        setTimeout(() => {
            loadSearchRequests();
        }, 3000);
        
    } catch (error) {
        handleError(error, 'Ошибка запуска сбора данных');
    } finally {
        // Возвращаем кнопку в исходное состояние
        buttonElement.innerHTML = originalText;
        buttonElement.disabled = false;
    }
}

// ============================================================
// Функции для работы с комментариями проекта
// ============================================================

let currentCommentsPage = 1;
const COMMENTS_PER_PAGE = 50;

function openCommentsModal() {
    currentCommentsPage = 1;
    const modal = document.getElementById('comments-modal');
    modal.classList.add('active');
    loadComments();
}

async function loadComments(direction = null) {
    const commentsList = document.getElementById('comments-list');
    const paginationContainer = document.getElementById('comments-pagination');
    const pageInfo = document.getElementById('comments-page-info');

    if (direction === 'next') {
        currentCommentsPage++;
    } else if (direction === 'prev' && currentCommentsPage > 1) {
        currentCommentsPage--;
    } else if (direction === 'page') {
        // Переход на конкретную страницу (обработано в renderPagination)
    }

    const skip = (currentCommentsPage - 1) * COMMENTS_PER_PAGE;

    try {
        commentsList.innerHTML = '<div class="loading-spinner">Загрузка комментариев...</div>';
        
        const response = await fetch(`/api/projects/${PROJECT_ID}/comments/?skip=${skip}&limit=${COMMENTS_PER_PAGE}`);
        
        if (!response.ok) {
            throw new Error(`Ошибка ${response.status}: ${response.statusText}`);
        }

        const result = await response.json();
        const comments = result.data || [];
        const total = result.total || 0;

        if (comments.length === 0) {
            commentsList.innerHTML = '<div class="empty-state">Нет комментариев для этого проекта</div>';
            if (paginationContainer) paginationContainer.innerHTML = '';
            return;
        }

        // Обновляем информацию о странице
        const totalPages = Math.ceil(total / COMMENTS_PER_PAGE);
        pageInfo.textContent = `Страница ${currentCommentsPage} из ${totalPages} (всего: ${total})`;
        
        // Рендерим комментарии
        commentsList.innerHTML = comments.map(comment => `
            <div class="comment-card">
                <div class="comment-header">
                    <span class="comment-author">Автор: ${comment.author_name || comment.author_vk_id || 'Аноним'}</span>
                    <span class="comment-date">${new Date(comment.date).toLocaleString('ru-RU')}</span>
                </div>
                <div class="comment-text">${escapeHtml(comment.text)}</div>
                <div class="comment-footer">
                    <span class="comment-emote ${getEmoteClass(comment.emote)}">
                        ${getEmoteIcon(comment.emote)} ${comment.emote || 'Не определено'}
                    </span>
                    ${comment.conf ? `<span class="comment-conf">Уверенность: ${(comment.conf * 100).toFixed(1)}%</span>` : ''}
                    <span class="comment-likes">❤️ ${comment.likes_count}</span>
                    ${comment.post_link ? `<a href="${comment.post_link}" target="_blank" class="comment-link">Открыть пост ↗</a>` : ''}
                </div>
            </div>
        `).join('');

        // Рендерим пагинацию
        renderPagination(totalPages);

    } catch (error) {
        handleError(error, 'Ошибка загрузки комментариев');
        commentsList.innerHTML = '<div class="error-state">Ошибка загрузки комментариев</div>';
    }
}

function renderPagination(totalPages) {
    const paginationContainer = document.getElementById('comments-pagination');
    if (!paginationContainer) return;

    if (totalPages <= 1) {
        paginationContainer.innerHTML = '';
        return;
    }

    let html = '<button onclick="loadComments(\'prev\')" class="btn btn-secondary" ' + 
               (currentCommentsPage === 1 ? 'disabled' : '') + '>← Назад</button>';

    // Показываем до 3 страниц перед текущей
    const startPage = Math.max(1, currentCommentsPage - 3);
    const endPage = Math.min(totalPages, currentCommentsPage + 3);

    // Первая страница
    if (startPage > 1) {
        html += `<button onclick="goToPage(1)" class="btn btn-secondary ${currentCommentsPage === 1 ? 'active' : ''}">1</button>`;
    }

    // Троеточие после первой страницы
    if (startPage > 2) {
        html += '<span class="pagination-ellipsis">...</span>';
    }

    // Страницы вокруг текущей
    for (let i = startPage; i <= endPage; i++) {
        html += `<button onclick="goToPage(${i})" class="btn btn-secondary ${currentCommentsPage === i ? 'active' : ''}">${i}</button>`;
    }

    // Троеточие перед последней страницей
    if (endPage < totalPages - 1) {
        html += '<span class="pagination-ellipsis">...</span>';
    }

    // Последняя страница
    if (endPage < totalPages) {
        html += `<button onclick="goToPage(${totalPages})" class="btn btn-secondary ${currentCommentsPage === totalPages ? 'active' : ''}">${totalPages}</button>`;
    }

    html += '<button onclick="loadComments(\'next\')" class="btn btn-secondary" ' + 
            (currentCommentsPage >= totalPages ? 'disabled' : '') + '>Вперед →</button>';

    paginationContainer.innerHTML = html;
}

function goToPage(page) {
    currentCommentsPage = page;
    loadComments('page');
}

function getEmoteClass(emote) {
    if (!emote) return '';
    const e = emote.toUpperCase();
    if (e === 'POSITIVE') return 'emote-positive';
    if (e === 'NEGATIVE') return 'emote-negative';
    if (e === 'NEUTRAL') return 'emote-neutral';
    return '';
}

function getEmoteIcon(emote) {
    if (!emote) return '⚪';
    const e = emote.toUpperCase();
    if (e === 'POSITIVE') return '🟢';
    if (e === 'NEGATIVE') return '🔴';
    if (e === 'NEUTRAL') return '⚪';
    return '⚪';
}

// ============================================================
// Функции для работы с комментариями поискового запроса
// ============================================================

let currentRequestCommentsPage = 1;
const REQUEST_COMMENTS_PER_PAGE = 50;
let currentRequestId = null;

function openRequestCommentsModal(requestId) {
    currentRequestId = requestId;
    currentRequestCommentsPage = 1;
    const modal = document.getElementById('request-comments-modal');
    modal.classList.add('active');
    loadRequestComments();
}

async function loadRequestComments(direction = null) {
    const commentsList = document.getElementById('request-comments-list');
    const paginationContainer = document.getElementById('request-comments-pagination');
    const pageInfo = document.getElementById('request-comments-page-info');

    if (direction === 'next') {
        currentRequestCommentsPage++;
    } else if (direction === 'prev' && currentRequestCommentsPage > 1) {
        currentRequestCommentsPage--;
    } else if (direction === 'page') {
        // Переход на конкретную страницу (обработано в renderPagination)
    }

    const skip = (currentRequestCommentsPage - 1) * REQUEST_COMMENTS_PER_PAGE;

    try {
        commentsList.innerHTML = '<div class="loading-spinner">Загрузка комментариев...</div>';
        
        const response = await fetch(`/api/projects/${PROJECT_ID}/search_requests/${currentRequestId}/comments/?skip=${skip}&limit=${REQUEST_COMMENTS_PER_PAGE}`);
        
        if (!response.ok) {
            throw new Error(`Ошибка ${response.status}: ${response.statusText}`);
        }

        const result = await response.json();
        const comments = result.data || [];
        const total = result.total || 0;

        if (comments.length === 0) {
            commentsList.innerHTML = '<div class="empty-state">Нет комментариев для этого запроса</div>';
            if (paginationContainer) paginationContainer.innerHTML = '';
            return;
        }

        // Обновляем информацию о странице
        const totalPages = Math.ceil(total / REQUEST_COMMENTS_PER_PAGE);
        pageInfo.textContent = `Страница ${currentRequestCommentsPage} из ${totalPages} (всего: ${total})`;
        
        // Рендерим комментарии
        commentsList.innerHTML = comments.map(comment => `
            <div class="comment-card">
                <div class="comment-header">
                    <span class="comment-author">Автор: ${comment.author_name || comment.author_vk_id || 'Аноним'}</span>
                    <span class="comment-date">${new Date(comment.date).toLocaleString('ru-RU')}</span>
                </div>
                <div class="comment-text">${escapeHtml(comment.text)}</div>
                <div class="comment-footer">
                    <span class="comment-emote ${getEmoteClass(comment.emote)}">
                        ${getEmoteIcon(comment.emote)} ${comment.emote || 'Не определено'}
                    </span>
                    ${comment.conf ? `<span class="comment-conf">Уверенность: ${(comment.conf * 100).toFixed(1)}%</span>` : ''}
                    <span class="comment-likes">❤️ ${comment.likes_count}</span>
                    ${comment.post_link ? `<a href="${comment.post_link}" target="_blank" class="comment-link">Открыть пост ↗</a>` : ''}
                </div>
            </div>
        `).join('');

        // Рендерим пагинацию
        renderRequestPagination(totalPages);

    } catch (error) {
        handleError(error, 'Ошибка загрузки комментариев');
        commentsList.innerHTML = '<div class="error-state">Ошибка загрузки комментариев</div>';
    }
}

function renderRequestPagination(totalPages) {
    const paginationContainer = document.getElementById('request-comments-pagination');
    if (!paginationContainer) return;

    if (totalPages <= 1) {
        paginationContainer.innerHTML = '';
        return;
    }

    let html = '<button onclick="loadRequestComments(\'prev\')" class="btn btn-secondary" ' + 
               (currentRequestCommentsPage === 1 ? 'disabled' : '') + '>← Назад</button>';

    // Показываем до 3 страниц перед текущей
    const startPage = Math.max(1, currentRequestCommentsPage - 3);
    const endPage = Math.min(totalPages, currentRequestCommentsPage + 3);

    // Первая страница
    if (startPage > 1) {
        html += `<button onclick="goToRequestPage(1)" class="btn btn-secondary ${currentRequestCommentsPage === 1 ? 'active' : ''}">1</button>`;
    }

    // Троеточие после первой страницы
    if (startPage > 2) {
        html += '<span class="pagination-ellipsis">...</span>';
    }

    // Страницы вокруг текущей
    for (let i = startPage; i <= endPage; i++) {
        html += `<button onclick="goToRequestPage(${i})" class="btn btn-secondary ${currentRequestCommentsPage === i ? 'active' : ''}">${i}</button>`;
    }

    // Троеточие перед последней страницей
    if (endPage < totalPages - 1) {
        html += '<span class="pagination-ellipsis">...</span>';
    }

    // Последняя страница
    if (endPage < totalPages) {
        html += `<button onclick="goToRequestPage(${totalPages})" class="btn btn-secondary ${currentRequestCommentsPage === totalPages ? 'active' : ''}">${totalPages}</button>`;
    }

    html += '<button onclick="loadRequestComments(\'next\')" class="btn btn-secondary" ' + 
            (currentRequestCommentsPage >= totalPages ? 'disabled' : '') + '>Вперед →</button>';

    paginationContainer.innerHTML = html;
}

function goToRequestPage(page) {
    currentRequestCommentsPage = page;
    loadRequestComments('page');
}

// Функция crawlProjectComments удалена, так как кнопка обновления больше не используется