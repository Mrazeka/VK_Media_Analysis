# interface/server.py



from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
import logging
import sys
import argparse
from pathlib import Path

# Импортируем роутеры из основного проекта
from routes.database import router as database_router
from routes.vk_service import router as vk_router
from routes.posts import router as posts_router
from routes.projects import router as projects_router
from routes.llm_client import router as llm_router

# ============================================================
# НАСТРОЙКА ЛОГГИРОВАНИЯ
# ============================================================
log_dir = Path("/var/log/vk_analytics")
log_dir.mkdir(parents=True, exist_ok=True)

log_file = log_dir / "interface.log"
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

logger = logging.getLogger("interface")
logger.setLevel(logging.INFO)
logger.handlers.clear()

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

file_handler = logging.FileHandler(log_file, encoding='utf-8')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

logging.info("=" * 60)
logging.info(" Запуск веб-интерфейса VK Analytics...")
logging.info(f" Директория логов: {log_dir}")
logging.info(f" Файл логов: {log_file}")
logging.info("=" * 60)

# ============================================================
# ПРИЛОЖЕНИЕ FASTAPI
# ============================================================
app = FastAPI(
    title="VK Analytics Interface",
    version="1.0.0",
    description="Веб-интерфейс для VK Analytics",
    redirect_slashes=False  # Отключаем авто-редирект со слешами
)

# Разрешаем CORS для всех источников (для разработки)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# ПОДКЛЮЧЕНИЕ РОУТЕРОВ ИЗ routes/
# ============================================================
app.include_router(database_router)
app.include_router(vk_router)
app.include_router(posts_router)
app.include_router(projects_router)
app.include_router(llm_router, prefix="/api/llm", tags=["LLM Chat"])

# ============================================================
# СТАТИЧЕСКИЕ ФАЙЛЫ И ШАБЛОНЫ
# ============================================================
BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

# Инициализируем Jinja2 для рендеринга шаблонов
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Монтируем статику
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    logging.info(f" Статика подключена: {STATIC_DIR}")
else:
    logging.warning(f" Директория статики не найдена: {STATIC_DIR}")

# ============================================================
# ЭНДПОИНТЫ ДЛЯ СТРАНИЦ
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def root():
    """Главная страница"""
    index_html = TEMPLATES_DIR / "index.html"
    if not index_html.exists():
        raise HTTPException(status_code=404, detail="index.html не найден")
    return FileResponse(str(index_html))


@app.get("/projects")
@app.get("/projects/")
async def projects_page():
    """Страница списка проектов"""
    page_html = TEMPLATES_DIR / "projects.html"
    if not page_html.exists():
        raise HTTPException(status_code=404, detail="projects.html не найден")
    return FileResponse(str(page_html))


@app.get("/project/{project_id}")
async def project_detail_page(request: Request, project_id: int):
    """Страница деталей проекта"""
    return templates.TemplateResponse("project_detail.html", {"request": request, "project_id": project_id})


@app.get("/settings")
@app.get("/settings/")
async def settings_page():
    """Страница настроек"""
    page_html = TEMPLATES_DIR / "settings.html"
    if not page_html.exists():
        raise HTTPException(status_code=404, detail="settings.html не найден")
    return FileResponse(str(page_html))


@app.get("/projects/{project_id}/chats")
async def chats_page(request: Request, project_id: int):
    """Страница списка чатов проекта"""
    return templates.TemplateResponse("chats.html", {"request": request, "project_id": project_id})


@app.get("/chat/{chat_id}")
async def chat_detail_page(request: Request, chat_id: int):
    """Страница конкретного чата"""
    return templates.TemplateResponse("chat_detail.html", {"request": request, "chat_id": chat_id, "project_id": None})


# ============================================================
# СИСТЕМНЫЕ ЭНДПОИНТЫ
# ============================================================

@app.get("/api/health")
async def health_check():
    """Проверка здоровья интерфейса"""
    return {
        "status": "healthy",
        "service": "vk-analytics-interface",
        "version": "1.0.0"
    }


# ============================================================
# ЗАПУСК
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Запуск веб-интерфейса")
    parser.add_argument("--host", default="0.0.0.0", help="Хост для прослушивания")
    parser.add_argument("--port", type=int, default=8550, help="Порт для прослушивания")
    args = parser.parse_args()

    import uvicorn
    logging.info(f"🌐 Запуск сервера на {args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
