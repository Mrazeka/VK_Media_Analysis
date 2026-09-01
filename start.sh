#!/bin/bash
set -e


# НАСТРОЙКА ЛОГИРОВАНИЯ

LOG_DIR="/var/log/vk_analytics"
mkdir -p "$LOG_DIR"

LOG_MAIN="$LOG_DIR/main.log"
LOG_MODEL="$LOG_DIR/model_download.log"
LOG_API="$LOG_DIR/api.log"
LOG_LLM="$LOG_DIR/llm.log"

# Очищаем старые логи
> "$LOG_MAIN"
> "$LOG_MODEL"
> "$LOG_API"
> "$LOG_LLM"

# Функция для логирования в основной лог + консоль
log_main() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_MAIN"
}

# Функция для логирования в файл + консоль
log_to_file() {
    local log_file="$1"
    local message="$2"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $message" | tee -a "$log_file"
}


# ЗАПУСК

log_main " Запуск VK Analytics..."
log_main " Порт 8000: Основное API"
log_main " Порт 8001: LLM сервис"
log_main " Директория логов: $LOG_DIR"


# ФУНКЦИЯ: Проверка запуска сервиса

check_service_start() {
    local service_name="$1"
    local port="$2"
    local pid="$3"
    local health_path="$4"
    local log_file="$5"
    local max_attempts=30
    local attempt=1

    log_to_file "$log_file" "⏳ Проверка запуска $service_name (порт $port)..."

    while [ $attempt -le $max_attempts ]; do
        # Процесс жив?
        if ! kill -0 $pid 2>/dev/null; then
            log_to_file "$log_file" " $service_name умер! (PID: $pid, попытка $attempt/$max_attempts)"
            return 1
        fi

        # ПРОВЕРКА 2: HTTP endpoint отвечает?
        if curl -s -f "http://localhost:$port$health_path" > /dev/null 2>&1; then
            log_to_file "$log_file" " $service_name запущен и готов (порт $port, PID: $pid)"
            return 0
        fi

        # === Сервер ещё запускается ===
        if [ $((attempt % 5)) -eq 0 ]; then
            log_to_file "$log_file" " $service_name запускается... (попытка $attempt/$max_attempts)"
        fi

        sleep 1
        attempt=$((attempt + 1))
    done

    log_to_file "$log_file" " $service_name не запустился за ${max_attempts} секунд!"
    return 1
}

#
# Загрузка модели
#
log_main "🔧 [ШАГ 1/4] Проверка наличия модели в кэше..."

python3 download_model.py >> "$LOG_MODEL" 2>&1
DOWNLOAD_STATUS=$?

if [ $DOWNLOAD_STATUS -ne 0 ]; then
    log_to_file "$LOG_MODEL" " Ошибка загрузки модели! Код: $DOWNLOAD_STATUS"
    log_main " Критическая ошибка: модель не загружена"
    exit 1
fi

log_to_file "$LOG_MODEL" "✅ Модель готова к работе"
log_main " [ШАГ 1/7] Модель загружена"

#
# ШАГ 2: Запуск основного API
#
log_main " [ШАГ 2/7] Запуск основного API..."

uvicorn main:app --host 0.0.0.0 --port 8000 >> "$LOG_API" 2>&1 &
MAIN_PID=$!

sleep 2

if ! check_service_start "Основной API" "8000" "$MAIN_PID" "/api/health" "$LOG_API"; then
    log_to_file "$LOG_API" " Критическая ошибка запуска основного API!"
    log_main " Критическая ошибка: основной API не запустился"
    kill $MAIN_PID 2>/dev/null
    exit 1
fi

log_main " [ШАГ 3/7] Основной API запущен (PID: $MAIN_PID)"

#
# ШАГ 3: Запуск LLM сервиса
#
log_main " [ШАГ 4/7] Запуск LLM сервиса..."

uvicorn llm_service:app --host 0.0.0.0 --port 8001 >> "$LOG_LLM" 2>&1 &
LLM_PID=$!

sleep 2

if ! check_service_start "LLM сервис" "8001" "$LLM_PID" "/health" "$LOG_LLM"; then
    log_to_file "$LOG_LLM" " Критическая ошибка запуска LLM сервиса!"
    log_main " Критическая ошибка: LLM сервис не запустился"
    kill $LLM_PID 2>/dev/null
    kill $MAIN_PID 2>/dev/null
    exit 1
fi

log_main " [ШАГ 5/7] LLM сервис запущен (PID: $LLM_PID)"

# =============================================================================
# ШАГ 4: Запуск интерфейса (FastAPI + Uvicorn)
# =============================================================================
log_main "🔧 [ШАГ 6/7] Запуск веб-интерфейса..."

python3 -m interface.server --port 8550 --host 0.0.0.0 >> "$LOG_DIR/interface.log" 2>&1 &
INTERFACE_PID=$!

sleep 2

# Проверка запуска интерфейса (простая проверка процесса)
if ! kill -0 $INTERFACE_PID 2>/dev/null; then
    log_to_file "$LOG_DIR/interface.log" " Критическая ошибка запуска интерфейса!"
    log_main " Критическая ошибка: интерфейс не запустился"
    kill $INTERFACE_PID 2>/dev/null
    kill $LLM_PID 2>/dev/null
    kill $MAIN_PID 2>/dev/null
    exit 1
fi

log_to_file "$LOG_DIR/interface.log" " Интерфейс запущен (порт 8550, PID: $INTERFACE_PID)"
log_main " [ШАГ 7/7] Веб-интерфейс запущен (PID: $INTERFACE_PID)"


# ВСЁ ЗАПУЩЕНО
log_main " Все сервисы работают!"
log_main " Основной API: http://localhost:8000"
log_main " LLM сервис: http://localhost:8001"
log_main " Интерфейс: http://localhost:8550"
log_main " Логи доступны в: $LOG_DIR"


# ЦИКЛ МОНИТОРИНГА (логирование в main.log)

log_main " Запуск мониторинга сервисов..."

while true; do
    # Проверка что процессы живы
    if ! kill -0 $MAIN_PID 2>/dev/null; then
        log_main "⚠️ Основной API завершился (PID: $MAIN_PID)"
        break
    fi

    if ! kill -0 $LLM_PID 2>/dev/null; then
        log_main "⚠️ LLM сервис завершился (PID: $LLM_PID)"
        break
    fi

    if ! kill -0 $INTERFACE_PID 2>/dev/null; then
        log_main "⚠️ Интерфейс завершился (PID: $INTERFACE_PID)"
        break
    fi

    # Периодический статус (каждые 60 секунд)
    sleep 60
    log_main " heartbeat — сервисы работают"
done

# ЗАВЕРШЕНИЕ

log_main "⚠️ Один из сервисов завершился, останавливаем все..."
kill $MAIN_PID $LLM_PID $INTERFACE_PID 2>/dev/null
exit 1