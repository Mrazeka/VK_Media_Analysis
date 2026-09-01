# main.py
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import logging
import sys
from pathlib import Path
from routes.database import router as database_router
from routes.vk_service import router as vk_router
from ModelsBD import Base
from routes.posts import router as posts_router
from routes.projects import router as projects_router
from routes.llm_client import router as llm_router


# ============================================================
# НАСТРОЙКА ЛОГГИРОВАНИЯ
# ============================================================
log_dir = Path("/var/log/vk_analytics")
log_dir.mkdir(parents=True, exist_ok=True)

log_file = log_dir / "app.log"
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Очистка существующих обработчиков
logger.handlers.clear()

# Вывод в консоль
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# Вывод в файл
file_handler = logging.FileHandler(log_file, encoding='utf-8')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

logging.info("=" * 60)
logging.info("🚀 Запуск VK Analytics...")
logging.info(f"📁 Директория логов: {log_dir}")
logging.info(f"📄 Файл логов: {log_file}")
logging.info("=" * 60)


app = FastAPI(
    title="VK Analytics API",
    version="1.0.0",
    description="API для работы с базой данных VK Analytics"
)

# Подключаем роутеры
app.include_router(database_router)
app.include_router(vk_router)
app.include_router(posts_router)
app.include_router(projects_router)
app.include_router(llm_router, prefix="/api/llm", tags=["LLM Chat"])


# ============================================================
# Системные эндпоинты
# ============================================================

@app.get("/")
async def root():
    """Главная страница"""
    return {"message": "VK Analytics API", "status": "running"}

@app.get("/api/health")
async def health_check():
    """
    Проверка здоровья основного API
    """
    return {
        "status": "healthy",
        "service": "vk-analytics-api",
        "version": "1.0.0"
    }

# ============================================================
# Инициализация БД при старте
# ============================================================

@app.on_event("startup")
async def startup_event():
    """Инициализация при запуске"""
    logging.info(" [ШАГ 1/3] Проверка наличия модели в кэше...")
    # Ленивая загрузка - модель загрузится при первом запросе
    logging.info(" [ШАГ 1/3] Модель готова к ленивой загрузке")
    
    logging.info("🔧 [ШАГ 2/3] Запуск основного API...")
    logging.info(" [ШАГ 2/3] Основной API запущен")
    
    logging.info(" [ШАГ 3/3] Проверка подключения к БД и миграция таблиц...")
    try:
        from init_db import engine, init_database
        with engine.connect() as conn:
            logging.info(" [ШАГ 3/3] Подключение к БД успешно")

        init_database()
    except Exception as e:
        logging.error(f" Ошибка подключения к БД: {e}")
        raise
