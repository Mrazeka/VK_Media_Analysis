/**
 * Скрипт для страницы настроек
 */

document.addEventListener('DOMContentLoaded', loadSettings);

async function loadSettings() {
    try {
        // Загрузка системных настроек
        const response = await fetch('/api/settings/system');
        const settings = await response.json();
        
        // Заполнение полей API
        document.getElementById('api-host').value = settings.api_host || 'localhost';
        document.getElementById('api-port').value = settings.api_port || '8000';
        
        // Заполнение переменных окружения
        document.getElementById('env-debug-mode').textContent = settings.debug_mode ? 'true' : 'false';
        document.getElementById('env-interface-host').textContent = 
            localStorage.getItem('interface_host') || '0.0.0.0';
        document.getElementById('env-interface-port').textContent = 
            localStorage.getItem('interface_port') || '8550';
        
        // Проверка статуса модели (заглушка)
        updateModelStatus('inactive', 'Статус неизвестен');
        
    } catch (error) {
        handleError(error, 'Ошибка загрузки настроек');
    }
}

function updateModelStatus(status, text) {
    const statusDot = document.getElementById('model-status-dot');
    const statusText = document.getElementById('model-status-text');
    
    if (status === 'active') {
        statusDot.className = 'status-dot active';
        statusText.textContent = text || 'Модель активна';
    } else if (status === 'inactive') {
        statusDot.className = 'status-dot inactive';
        statusText.textContent = text || 'Модель неактивна';
    } else {
        statusDot.className = 'status-dot';
        statusText.textContent = text || 'Неизвестно';
    }
}

async function startModel() {
    const btn = document.getElementById('btn-start-model');
    const statusDiv = document.getElementById('model-operation-status');
    
    btn.disabled = true;
    statusDiv.className = 'operation-status show';
    statusDiv.textContent = 'Запуск модели...';
    
    try {
        const response = await fetch('/api/settings/model/start', {
            method: 'POST'
        });
        
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Ошибка запуска модели');
        }
        
        statusDiv.className = 'operation-status show success';
        statusDiv.textContent = 'Модель успешно запущена';
        updateModelStatus('active', 'Модель активна');
        showNotification('Модель успешно запущена', 'success');
        
    } catch (error) {
        statusDiv.className = 'operation-status show error';
        statusDiv.textContent = `Ошибка: ${error.message}`;
        handleError(error, 'Ошибка запуска модели');
    } finally {
        btn.disabled = false;
        
        // Скрыть статус через 5 секунд
        setTimeout(() => {
            statusDiv.classList.remove('show');
        }, 5000);
    }
}

async function stopModel() {
    const btn = document.getElementById('btn-stop-model');
    const statusDiv = document.getElementById('model-operation-status');
    
    btn.disabled = true;
    statusDiv.className = 'operation-status show';
    statusDiv.textContent = 'Остановка модели...';
    
    try {
        const response = await fetch('/api/settings/model/stop', {
            method: 'POST'
        });
        
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Ошибка остановки модели');
        }
        
        statusDiv.className = 'operation-status show success';
        statusDiv.textContent = 'Модель успешно остановлена';
        updateModelStatus('inactive', 'Модель неактивна');
        showNotification('Модель успешно остановлена', 'success');
        
    } catch (error) {
        statusDiv.className = 'operation-status show error';
        statusDiv.textContent = `Ошибка: ${error.message}`;
        handleError(error, 'Ошибка остановки модели');
    } finally {
        btn.disabled = false;
        
        // Скрыть статус через 5 секунд
        setTimeout(() => {
            statusDiv.classList.remove('show');
        }, 5000);
    }
}
