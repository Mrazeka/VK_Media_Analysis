/**
 * Скрипт для страницы списка проектов
 */

let currentDeleteProjectId = null;

// Загрузка проектов при загрузке страницы
document.addEventListener('DOMContentLoaded', loadProjects);

async function loadProjects() {
    const projectsList = document.getElementById('projects-list');
    if (!projectsList) return;

    try {
        const response = await fetch('/api/projects/');
        
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            throw new Error(`Ошибка ${response.status}: ${errorData.detail || 'Неизвестная ошибка'}`);
        }
        
        let data = await response.json();
        console.log('Полученные данные от API:', data);
        console.log('Ключи объекта:', Object.keys(data));
        
        // Нормализация данных: гарантируем, что projects - это массив
        let projects = [];
        
        if (Array.isArray(data)) {
            projects = data;
        } else if (data && typeof data === 'object') {
            // Пытаемся найти массив в распространенных форматах ответа
            if (Array.isArray(data.data)) projects = data.data;
            else if (Array.isArray(data.items)) projects = data.items;
            else if (Array.isArray(data.results)) projects = data.results;
            else if (Array.isArray(data.projects)) projects = data.projects;
            // Если это одиночный объект с id, оборачиваем в массив
            else if (data.id) projects = [data];
            // Перебор всех полей объекта в поисках массива (если структура нестандартная)
            else {
                for (const key in data) {
                    if (Array.isArray(data[key])) {
                        console.log(`Массив найден в поле "${key}"`);
                        projects = data[key];
                        break;
                    }
                }
                if (projects.length === 0) {
                    console.warn('Неожиданный формат ответа (нет массива):', data);
                }
            }
        }

        console.log('Обработано проектов:', projects.length);

        if (projects.length === 0) {
            projectsList.innerHTML = `
                <div class="empty-state">
                    <p>Проекты еще не созданы</p>
                    <p>Нажмите "Создать проект" чтобы добавить первый проект</p>
                </div>
            `;
            return;
        }

        // Рендеринг карточек с проверкой ID
        projectsList.innerHTML = '';
        projects.forEach(project => {
            if (!project.id) {
                console.warn('Проект без ID пропущен:', project);
                return;
            }
            projectsList.innerHTML += createProjectCard(project);
        });
        
    } catch (error) {
        console.error('Ошибка загрузки проектов:', error);
        handleError(error, 'Ошибка загрузки проектов');
        projectsList.innerHTML = '<div class="empty-state">Ошибка загрузки проектов. Проверьте логи.</div>';
    }
}

function createProjectCard(project) {
    const description = project.description || 'Нет описания';
    const keywords = formatKeywords(project.keywords) || 'Нет ключевых слов';
    
    return `
        <div class="project-card" onclick="window.location.href='/project/${project.id}'">
            <div class="card-content" style="width: 60%;">
                <h2>${escapeHtml(project.name)}</h2>
                <p><strong>Описание:</strong> ${escapeHtml(description)}</p>
                <p><strong>Ключевые слова:</strong> ${escapeHtml(keywords)}</p>
            </div>
            <div class="card-actions" style="width: 40%;" onclick="event.stopPropagation()">
                <button class="btn btn-secondary" onclick="openEditProjectModal(${project.id})">
                    ✏️ Редактировать
                </button>
                <button class="btn btn-danger" onclick="confirmDeleteProject(${project.id})">
                    🗑️ Удалить
                </button>
            </div>
        </div>
    `;
}

// Открытие модального окна создания проекта
function openCreateProjectModal() {
    document.getElementById('create-project-form').reset();
    openModal('create-project-modal');
}

// Создание проекта
async function handleCreateProject(event) {
    event.preventDefault();
    
    const form = event.target;
    const formData = new FormData(form);
    
    const projectData = {
        name: formData.get('name'),
        description: formData.get('description') || null,
        keywords: parseKeywords(formData.get('keywords')),
        model_name: formData.get('model_name') || null
    };

    try {
        const response = await fetch('/api/projects/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(projectData)
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Ошибка создания проекта');
        }

        closeModal('create-project-modal');
        showNotification('Проект успешно создан', 'success');
        loadProjects();
    } catch (error) {
        handleError(error, 'Ошибка создания проекта');
    }
}

// Открытие модального окна редактирования проекта
async function openEditProjectModal(projectId) {
    try {
        const response = await fetch(`/api/projects/${projectId}`);
        if (!response.ok) throw new Error('Проект не найден');
        
        const project = await response.json();
        
        document.getElementById('edit-project-id').value = project.id;
        document.getElementById('edit-project-name').value = project.name;
        document.getElementById('edit-project-description').value = project.description || '';
        document.getElementById('edit-project-keywords').value = formatKeywords(project.keywords);
        document.getElementById('edit-project-model').value = project.model_name || '';
        
        openModal('edit-project-modal');
    } catch (error) {
        handleError(error, 'Ошибка загрузки данных проекта');
    }
}

// Обновление проекта
async function handleUpdateProject(event) {
    event.preventDefault();
    
    const form = event.target;
    const formData = new FormData(form);
    const projectId = formData.get('id') || document.getElementById('edit-project-id').value;
    
    const projectData = {
        name: formData.get('name') || document.getElementById('edit-project-name').value,
        description: (formData.get('description') || document.getElementById('edit-project-description').value) || null,
        keywords: parseKeywords(formData.get('keywords') || document.getElementById('edit-project-keywords').value),
        model_name: (formData.get('model_name') || document.getElementById('edit-project-model').value) || null
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
        loadProjects();
    } catch (error) {
        handleError(error, 'Ошибка обновления проекта');
    }
}

// Подтверждение удаления проекта
function confirmDeleteProject(projectId) {
    currentDeleteProjectId = projectId;
    
    const deleteBtn = document.getElementById('confirm-delete-btn');
    deleteBtn.onclick = deleteProject;
    
    openModal('delete-confirm-modal');
}

// Удаление проекта
async function deleteProject() {
    if (!currentDeleteProjectId) return;

    try {
        const response = await fetch(`/api/projects/${currentDeleteProjectId}`, {
            method: 'DELETE'
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Ошибка удаления проекта');
        }

        closeModal('delete-confirm-modal');
        showNotification('Проект успешно удален', 'success');
        currentDeleteProjectId = null;
        loadProjects();
    } catch (error) {
        handleError(error, 'Ошибка удаления проекта');
    }
}

// Экранирование HTML
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
