/**
 * Основные утилиты и функции для интерфейса
 */

// Показ уведомления
function showNotification(message, type = 'info') {
    const container = document.getElementById('notification-container');
    if (!container) return;

    const notification = document.createElement('div');
    notification.className = `notification ${type}`;
    notification.textContent = message;

    container.appendChild(notification);

    // Автоматическое удаление через 5 секунд
    setTimeout(() => {
        notification.style.animation = 'slideIn 0.3s ease-out reverse';
        setTimeout(() => notification.remove(), 300);
    }, 5000);
}

// Открытие модального окна
function openModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.classList.add('active');
    }
}

// Закрытие модального окна
function closeModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.classList.remove('active');
        modal.style.display = '';
    }
}

// Закрытие модального окна по клику вне контента
document.addEventListener('click', (e) => {
    if (e.target.classList.contains('modal')) {
        e.target.classList.remove('active');
        e.target.style.display = '';
    }
});

// Обработка ошибок
function handleError(error, defaultMessage = 'Произошла ошибка') {
    console.error(error);
    const message = error.message || error.detail || defaultMessage;
    showNotification(message, 'error');
}

// Форматирование даты
function formatDate(dateString) {
    if (!dateString) return '-';
    const date = new Date(dateString);
    return date.toLocaleDateString('ru-RU', {
        year: 'numeric',
        month: 'long',
        day: 'numeric'
    });
}

// Проверка пустого значения
function isEmpty(value) {
    return value === null || value === undefined || value === '';
}

// Преобразование строки ключевых слов в массив
function parseKeywords(keywordsString) {
    if (!keywordsString) return [];
    return keywordsString.split(',').map(k => k.trim()).filter(k => k);
}

// Преобразование массива ключевых слов в строку
function formatKeywords(keywordsArray) {
    if (!keywordsArray || !Array.isArray(keywordsArray)) return '';
    return keywordsArray.join(', ');
}
