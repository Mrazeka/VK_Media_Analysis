"""
LLM Микросервис для VK Analytics
Использует Qwen2.5-7B-Instruct через PyTorch + Transformers
"""

import os
import json
import asyncio
import re
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from ModelsBD import ChatSession, ChatMessage, Project
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM, TextStreamer
from sqlalchemy import create_engine, text, desc
from sqlalchemy.orm import sessionmaker, Session
import gc
# ============================================================
# НАСТРОЙКА ЛОГИРОВАНИЯ
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/app/llm.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ============================================================
# КОНФИГУРАЦИЯ
# ============================================================

processing_progress = {}

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:pass@db:5432/vk_analytics")
MODEL_NAME = os.getenv("MODEL_NAME", "Qwen/Qwen2.5-7B-Instruct")
MODEL_PATH = os.getenv("MODEL_PATH", "./models/qwen2.5-7b-instruct")
is_unloading = False
# Параметры генерации
MAX_CONTEXT_LENGTH = int(os.getenv("MAX_CONTEXT_LENGTH", "32000"))
MAX_NEW_TOKENS = int(os.getenv("MAX_NEW_TOKENS", "2048"))
TEMPERATURE_CHAT = float(os.getenv("TEMPERATURE_CHAT", "0.4"))
TEMPERATURE_INTENT = float(os.getenv("TEMPERATURE_INTENT", "0.3"))
TOP_P = float(os.getenv("TOP_P", "0.9"))
REPETITION_PENALTY = float(os.getenv("REPETITION_PENALTY", "1.1"))

MAX_CHARS_PER_BATCH = 12500
MAX_POST_LENGTH = 300
MAX_COMMENT_LENGTH = 300
MAX_AUTHOR_LENGTH = 50

def truncate_text(text: str, max_length: int) -> str:
    """Обрезает текст до указанной длины, добавляя многоточие."""
    if not text:
        return ""
    if len(text) <= max_length:
        return text
    return text[:max_length].rstrip() + "..."

# Параметры выборки данных
MAX_CONTEXT_ITEMS = int(os.getenv("MAX_CONTEXT_ITEMS", "250"))
HISTORY_LIMIT = int(os.getenv("HISTORY_LIMIT", "3"))
SESSION_TTL_HOURS = int(os.getenv("SESSION_TTL_HOURS", "24"))

# Параметры пакетной обработки для расширенного чата
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "250"))  # Размер пакета для обработки
BATCH_TEMPERATURE = float(os.getenv("BATCH_TEMPERATURE", "0.3"))  # Температура для batch обработки
BATCH_MAX_TOKENS = int(os.getenv("BATCH_MAX_TOKENS", "512"))  # Токены для пакетной обработки
FINAL_MAX_TOKENS = int(os.getenv("FINAL_MAX_TOKENS", "2048"))  # Токены для финального ответа
MAX_CONTEXT_WINDOW = int(os.getenv("MAX_CONTEXT_WINDOW", "131072"))  # 128k токенов контекст


CACHE_DIR = os.getenv("CACHE_DIR", "/app/models/cache")
# ============================================================
# ИНИЦИАЛИЗАЦИЯ БД
# ============================================================

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """Зависимость для получения сессии БД"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ============================================================
# ЗАГРУЗКА МОДЕЛИ
# ============================================================

tokenizer = None
model = None
device = None
gpu_lock = asyncio.Lock()
model_loaded = False
last_activity_time = None
MODEL_IDLE_TIMEOUT_MINUTES = int(os.getenv("MODEL_IDLE_TIMEOUT_MINUTES", "15"))
unload_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):

    
    print("=" * 60)
    print("ℹ LLM сервис запущен. Модель НЕ загружена.")
    print(" Модель будет загружена при первом запросе к /model/reload")
    print("=" * 60)
    
    yield
    
    # Выгрузка при остановке сервиса (если модель была загружена)
    global model, tokenizer
    if model_loaded:
        print("🛑 Выгрузка модели...")
        del model
        del tokenizer
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

app = FastAPI(title="VK Analytics LLM Service", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


async def schedule_unload():
    """Планирует выгрузку модели через MODEL_IDLE_TIMEOUT_MINUTES после последнего запроса"""
    global last_activity_time, unload_task
    
    await asyncio.sleep(MODEL_IDLE_TIMEOUT_MINUTES * 60)
    
    # Проверяем, не было ли новых запросов за время ожидания
    if last_activity_time and (datetime.utcnow() - last_activity_time).total_seconds() >= MODEL_IDLE_TIMEOUT_MINUTES * 60:
        print(f" Модель бездействует {MODEL_IDLE_TIMEOUT_MINUTES} минут. Выгружаем...")
        await unload_model_internal()


async def unload_model_internal():
    """Внутренняя функция для выгрузки модели"""
    global model, tokenizer, model_loaded, unload_task
    import torch
    import gc

    if not model_loaded:
        return

    print(" Выгрузка модели из памяти по таймауту...")

    # Отменяем предыдущую задачу выгрузки, если она есть
    if unload_task and not unload_task.done():
        unload_task.cancel()
        try:
            await unload_task
        except asyncio.CancelledError:
            pass

    # Выгрузка текущей модели
    del model
    del tokenizer

    # Очистка CUDA памяти
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.synchronize()

    # Сборка мусора
    gc.collect()

    # Сброс флагов
    model = None
    tokenizer = None
    model_loaded = False
    unload_task = None

    print(" Модель выгружена. VRAM освобождена.")


def reset_unload_timer():
    """Сбрасывает таймер выгрузки модели при новой активности"""
    global last_activity_time, unload_task
    
    last_activity_time = datetime.utcnow()
    
    # Отменяем предыдущую задачу, если она есть
    if unload_task and not unload_task.done():
        unload_task.cancel()
    
    # Создаем новую задачу
    unload_task = asyncio.create_task(schedule_unload())

# ============================================================
# PYDANTIC МОДЕЛИ
# ============================================================


class ChatRequest(BaseModel):
    query: str
    project_id: int = Field(..., ge=1)
    session_id: Optional[int] = None
    manual_filters: Optional[Dict[str, Any]] = None  # Ручные фильтры пользователя


class ChatResponse(BaseModel):
    answer: str
    session_id: int
    sources_count: int
    applied_filters: Optional[Dict[str, Any]] = None
    message_id: Optional[int] = None  # ID последнего сохраненного сообщения


class EnhancedChatRequest(BaseModel):
    """Запрос для расширенного чата с пакетной обработкой"""
    query: str
    project_id: int = Field(..., ge=1)
    session_id: Optional[int] = None
    manual_filters: Optional[Dict[str, Any]] = None
    batch_size: Optional[int] = None  # Переопределение размера пакета


class EnhancedChatResponse(BaseModel):
    """Ответ расширенного чата"""
    answer: str
    session_id: int
    sources_count: int
    batches_processed: int  # Количество обработанных пакетов
    applied_filters: Optional[Dict[str, Any]] = None
    message_id: Optional[int] = None


class IntentRequest(BaseModel):
    query: str
    project_stats: Optional[Dict[str, Any]] = None


class IntentResponse(BaseModel):
    filters: Dict[str, Any]


class SessionResetRequest(BaseModel):
    session_id: int


# ============================================================
# ГЕНЕРАЦИЯ ТЕКСТА (Обертка над моделью)
# ============================================================


async def generate_text(
        messages: List[Dict[str, str]],
        max_new_tokens: int = MAX_NEW_TOKENS,
        temperature: float = TEMPERATURE_CHAT,
        top_p: float = TOP_P,
        repetition_penalty: float = REPETITION_PENALTY,
) -> str:
    """Генерирует ответ модели, не блокируя основной цикл событий FastAPI."""
    global is_unloading

    # Применяем chat template
    prompt = tokenizer.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True
    )

    max_length = MAX_CONTEXT_LENGTH - max_new_tokens
    if max_length <= 0:
        max_length = MAX_CONTEXT_LENGTH

    inputs = tokenizer(
        prompt,
        return_tensors="pt",
        truncation=True,
        max_length=max_length,
        padding=False
    ).to(device)

    if inputs.input_ids.shape[1] == 0:
        logger.warning("Пустой тензор после токенизации.")
        return '{"selected_items": [], "summary": "Ошибка: пустой ввод"}'

    # ===  Запускаем синхронную генерацию в отдельном потоке ===
    def _run_generate():
        with torch.no_grad():
            return model.generate(
                inputs.input_ids,
                attention_mask=inputs.attention_mask,
                max_new_tokens=max_new_tokens,
                temperature=temperature,
                top_p=top_p,
                repetition_penalty=repetition_penalty,
                do_sample=temperature > 0.1,
                pad_token_id=tokenizer.eos_token_id,
                eos_token_id=tokenizer.eos_token_id,
            )

    # Это не блокирует FastAPI, позволяя обработать /model/unload
    outputs = await asyncio.to_thread(_run_generate)

    # Декодирование ответа
    generated_ids = outputs[0][inputs.input_ids.shape[1]:]
    response = tokenizer.decode(generated_ids, skip_special_tokens=True).strip()

    return response


# ============================================================
# УПРАВЛЕНИЕ СЕССИЯМИ
# ============================================================


def get_or_create_session(db: Session, project_id: int, session_id: Optional[int] = None) -> int:
    """Получает или создает сессию чата"""
    from ModelsBD import ChatSession, Project

    if session_id:
        session = db.query(ChatSession).filter(
            ChatSession.id == session_id,
            ChatSession.project_id == project_id
        ).first()

        if not session:
            raise HTTPException(
                status_code=404,
                detail="Сессия не найдена или не принадлежит проекту"
            )

        session.updated_at = datetime.utcnow()
        db.commit()
        return session.id
    else:
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise HTTPException(status_code=404, detail=f"Проект {project_id} не найден")

        new_session = ChatSession(project_id=project_id, title="Новый диалог")
        db.add(new_session)
        db.commit()
        db.refresh(new_session)
        return new_session.id


def get_history(db: Session, session_id: int, limit: int = HISTORY_LIMIT) -> List[Dict[str, str]]:
    # Получает последние N сообщений из истории сессии
    from ModelsBD import ChatMessage

    messages = db.query(ChatMessage).filter(
        ChatMessage.session_id == session_id
    ).order_by(ChatMessage.created_at.desc()).limit(limit).all()

    messages.reverse()
    return [{"role": msg.role, "content": msg.content} for msg in messages]


def save_message(
    db: Session,
    session_id: int,
    role: str,
    content: str,
    filters: dict = None,
    sources: int = 0,
    manual_filters: dict = None
):
    # Сохраняет сообщение в историю сессии и возвращает объект сообщения с ID.
    from ModelsBD import ChatMessage

    msg = ChatMessage(
        session_id=session_id,
        role=role,
        content=content,
        applied_filters=filters,
        sources_count=sources,
        manual_filters=manual_filters
    )
    db.add(msg)

    db.flush()
    
    if msg.id is None:
        logger.error(f"ID сообщения None после flush()! Роль: {role}, Сессия: {session_id}")
        raise Exception("Failed to generate message ID after flush")
    
    logger.info(f"Сообщение подготовлено к сохранению. ID: {msg.id}, Роль: {role}, Сессия: {session_id}")
    
    return msg



# INTENT PARSER (Преобразование запроса в фильтры)



async def parse_intent(query: str, stats: dict = None, manual_filters: dict = None) -> Dict[str, Any]:

    # Преобразует естественный запрос в JSON-фильтры для БД.
    # Возвращает только то, что извлекла LLM из текста запроса.
    # Слияние с manual_filters происходит программно в эндпоинтах.

    if not model_loaded or model is None:
        raise HTTPException(
            status_code=503,
            detail="LLM модель не загружена. Отправьте POST /model/reload для загрузки."
        )

    stats_hint = f"\nСтатистика проекта: {json.dumps(stats)}" if stats else ""

    system_message = {
        "role": "system",
        "content": """Ты — парсер запросов для SQL фильтрации базы данных VK Analytics.
Верни ТОЛЬКО валидный JSON объект без лишнего текста, без маркеров кода.

ТВОЯ ЗАДАЧА:
Извлеки параметры фильтрации из текста запроса пользователя.
Не выдумывай значения, если их нет в запросе.

=== ДОСТУПНЫЕ ПОЛЯ ДЛЯ ИЗВЛЕЧЕНИЯ ===
sentiment: "POSITIVE" | "NEGATIVE" | "NEUTRAL" | null
min_likes: integer
min_comments: integer
sort_by: "likes_count" | "comments_count" | "date" | null
limit: integer (по умолчанию 50, если не задано в запросе)
months: float/integer

=== ДОСТУПНЫЕ ПОЛЯ И ИНСТРУКЦИИ ===

1. sentiment (Тональность)
- Значения: "POSITIVE" | "NEGATIVE" | "NEUTRAL" | null
- Триггеры POSITIVE: "нравится", "позитив", "хорошо", "одобряю", "что понравилось".
- Триггеры NEGATIVE: "жалобы", "критика", "негатив", "проблемы", "недовольны", "баги".
- Триггеры NEUTRAL: "вопросы", "нейтрально", "интересно".
- Пример: "Покажи, чем люди недовольны" -> {"sentiment": "NEGATIVE"}

2. min_likes (Минимум лайков у поста)
- Тип: integer
- Триггеры: "более X лайков", "от X лайков", "популярные".
- Пример: "Посты с более чем 100 лайками" -> {"min_likes": 100}

3. min_comments (Минимум комментариев у поста)
- Тип: integer
- Триггеры: "более X ответов", "от X комментариев".
- Пример: "Обсуждаемые записи, где больше 20 ответов" -> {"min_comments": 20}


5. sort_by (Сортировка результатов)
- Значения: "likes_count" | "comments_count" | "date" | null
- Триггеры "likes_count": "топ", "лучшие", "популярные", "по лайкам".
- Триггеры "comments_count": "обсуждаемые", "спорные", "много ответов".
- Триггеры "date": "новые", "свежие", "последние", "недавние".
- Пример: "Свежие обсуждения" -> {"sort_by": "date"}

6. limit (Количество результатов)
- Тип: integer
- Триггеры: явные числа ("10 постов", "50 комментариев", "дай 15 отзывов").
- Пример: "Дай мне 15 негативных отзывов" -> {"limit": 15, "sentiment": "NEGATIVE"}

7. months (Период времени в месяцах)
- Тип: float/integer
- Триггеры: "за неделю" (0.25), "за месяц" (1), "за полгода" (6), "за год" (12).
- Пример: "Жалобы за последнюю неделю" -> {"months": 0.25, "sentiment": "NEGATIVE"}

Отвечай ТОЛЬКО JSON без маркеров кода, без пояснений."""
    }

    user_message = {
        "role": "user",
        "content": f'Запрос: "{query}"{stats_hint}'
    }

    messages = [system_message, user_message]

    try:
        async def call_llm():
            async with gpu_lock:
                raw_response = await generate_text(
                    messages,
                    max_new_tokens=512,
                    temperature=TEMPERATURE_INTENT,
                    top_p=0.9,
                    repetition_penalty=1.1
                )
                return raw_response

        raw_json = await call_llm()

        # Очистка от маркеров кода
        if raw_json.startswith("`"):
            raw_json = re.sub(r"^`(?:json)?\s*", "", raw_json)
            raw_json = re.sub(r"\s*`$", "", raw_json)

        llm_filters = json.loads(raw_json)

        # Возвращаем только фильтры, которые LLM смогла определить (без None)
        clean_llm_filters = {k: v for k, v in llm_filters.items() if v is not None}
        return clean_llm_filters

    except Exception as e:
        logger.error(f"⚠️ Ошибка парсинга intent: {e}")
        return {}



# ============================================================
# ВЫБОРКА ДАННЫХ ИЗ БД
# ============================================================


def fetch_relevant_data(
    db: Session,
    query: str,
    project_id: int,
    filters: dict,
    limit: int = MAX_CONTEXT_ITEMS
) -> tuple[str, int]:
    #
    # Извлекает релевантные комментарии из БД с применением фильтров.
    #
    # Returns:
    #     tuple: (текст контекста, количество источников)
    #
    from ModelsBD import Comment, Post, SearchRequest
    logger.info(f"[DEBUG] Фильтры: {filters}")
    logger.info(f"[DEBUG] Лимит: {limit}")
    # === БАЗОВЫЙ ЗАПРОС ===
    db_query = db.query(Comment).join(Post).join(SearchRequest).filter(
        SearchRequest.project_id == project_id
    )

    # === ПРИМЕНЕНИЕ ФИЛЬТРОВ ===
    if filters.get('sentiment'):
        db_query = db_query.filter(Comment.emote == filters['sentiment'])

    if filters.get('min_likes') is not None:
        db_query = db_query.filter(Post.likes_count >= filters['min_likes'])

    if filters.get('min_comments') is not None:
        db_query = db_query.filter(Post.comments_count >= filters['min_comments'])

    if filters.get('keywords'):
        keyword = f"%{filters['keywords']}%"
        db_query = db_query.filter(Comment.text.ilike(keyword))

    # === СОРТИРОВКА ===
    sort_map = {
        "likes_count": Post.likes_count,
        "comments_count": Post.comments_count,
        "date": Comment.date
    }
    sort_field = filters.get('sort_by', 'date')
    if sort_field in sort_map:
        db_query = db_query.order_by(sort_map[sort_field].desc())
    else:
        db_query = db_query.order_by(Comment.date.desc())

    # === ПОЛУЧЕНИЕ РЕЗУЛЬТАТОВ ===
    results = db_query.limit(limit).all()
    count = len(results)

    # === ФОРМИРОВАНИЕ КОНТЕКСТА ===
    context_parts = []
    for item in results:
        sentiment_marker = f"[{item.emote}]" if item.emote else "[UNKNOWN]"
        text_preview = item.text[:450] if item.text else ""
        context_parts.append(
            f"{sentiment_marker} (Лайки: {item.post.likes_count}): {text_preview}"
        )

    context_text = "\n---\n".join(context_parts) if context_parts else "Данных не найдено."
    return context_text, count



# ПАКЕТНАЯ ОБРАБОТКА КОММЕНТАРИЕВ (Enhanced Chat)


def fetch_posts_with_comments(
    db: Session,
    project_id: int,
    filters: dict,
    limit: int = MAX_CONTEXT_ITEMS
) -> list[dict]:

    # Извлекает посты с комментариями из БД, группируя их по постам.
    #
    # Returns:
    #     list[dict]: Список словарей вида {"post": {...}, "comments": [...]}

    from ModelsBD import Comment, Post, SearchRequest
    logger.info(f"DEBUG:filters:{filters}")
    # === БАЗОВЫЙ ЗАПРОС ===
    db_query = db.query(Post).join(SearchRequest).filter(
        SearchRequest.project_id == project_id
    )

    # === ПРИМЕНЕНИЕ ФИЛЬТРОВ ===
    if filters.get('sentiment'):
        # Фильтруем по тональности комментариев к посту
        db_query = db_query.filter(
            Post.id.in_(
                db.query(Comment.post_id).filter(Comment.emote == filters['sentiment'])
            )
        )

    if filters.get('min_likes') is not None:
        db_query = db_query.filter(Post.likes_count >= filters['min_likes'])

    if filters.get('min_comments') is not None:
        db_query = db_query.filter(Post.comments_count >= filters['min_comments'])

    # === СОРТИРОВКА ===
    sort_map = {
        "likes_count": Post.likes_count,
        "comments_count": Post.comments_count,
        "date": Post.date
    }
    sort_field = filters.get('sort_by', 'date')
    if sort_field in sort_map:
        db_query = db_query.order_by(sort_map[sort_field].desc())
    else:
        db_query = db_query.order_by(Post.date.desc())


    posts = db_query.limit(limit).all()

    result = []
    for post in posts:

        comments_query = db.query(Comment).filter(Comment.post_id == post.id)
        
        # Применяем фильтр по тональности к комментариям
        if filters.get('sentiment'):
            comments_query = comments_query.filter(Comment.emote == filters['sentiment'])
        
        comments = comments_query.order_by(Comment.likes_count.desc()).limit(50).all()  # Лимит комментариев на пост
        
        post_data = {
            "post": {
                "id": post.id,
                "vk_post_id": post.vk_post_id,
                "text": post.text if post.text else "",
                "likes_count": post.likes_count,
                "comments_count": post.comments_count,
                "reposts_count": post.reposts_count,
                "views_count": post.views_count,
                "date": post.date.isoformat() if post.date else None,
                "author_name": post.author.name if post.author else "Unknown"
            },
            "comments": [
                {
                    "id": c.id,
                    "text": c.text if c.text else "",
                    "likes_count": c.likes_count,
                    "emote": c.emote,
                    "conf": c.conf,
                    "date": c.date.isoformat() if c.date else None,
                    "author_name": c.author.name if c.author else "Unknown"
                }
                for c in comments
            ]
        }
        result.append(post_data)

    return result


async def process_comments_batch(
    db: Session,
    query: str,
    project_id: int,
    filters: dict,
    session_id: int,  # Добавляем session_id
    batch_size: int = BATCH_SIZE
) -> tuple[str, int, list[str]]:
    # Пакетная обработка комментариев для расширенного чата.
    #
    # Returns:
    #     tuple: (итоговый контекст, общее количество источников, список промежуточных результатов)
    from ModelsBD import Comment, Post, SearchRequest
    
    # Проверка что модель загружена
    if not model_loaded or model is None:
        raise HTTPException(
            status_code=503,
            detail="LLM модель не загружена. Отправьте POST /model/reload для загрузки."
        )
    db_limit = filters.get('limit', MAX_CONTEXT_ITEMS)
    raw_posts = await asyncio.to_thread(fetch_posts_with_comments, db, project_id, filters, db_limit)
    total_items = len(raw_posts)

    if total_items == 0:
        return "Данных не найдено.", 0, []

    # Предварительно обрезаем все тексты, чтобы корректно считать размер батчей
    posts_with_comments = []
    for item in raw_posts:
        post = item["post"]
        comments = item.get("comments", []) or []

        # Обрезаем текст поста
        truncated_post = {
            **post,
            "text": truncate_text(post.get("text", ""), MAX_POST_LENGTH),
            "author_name": truncate_text(post.get("author_name", ""), MAX_AUTHOR_LENGTH),
        }

        # Обрезаем тексты комментариев
        truncated_comments = [
            {
                **c,
                "text": truncate_text(c.get("text", ""), MAX_COMMENT_LENGTH),
                "author_name": truncate_text(c.get("author_name", ""), MAX_AUTHOR_LENGTH),
            }
            for c in comments
        ]

        posts_with_comments.append({
            "post": truncated_post,
            "comments": truncated_comments
        })

    logger.info(f"Извлечено {total_items} записей, тексты обрезаны")

    # РАЗБИЕНИЕ НА ПАКЕТЫ (с учётом уже обрезанных текстов) ===
    def split_by_text_length(items, max_chars):
    # Разбивает записи на пакеты по суммарной длине текста.
        batches = []
        current_batch = []
        current_chars = 0

        for item in items:
            # Считаем длину уже обрезанного текста
            post_len = len(item["post"].get("text", ""))
            comments_len = sum(len(c.get("text", "")) for c in item.get("comments", []))
            # Запас на форматирование, метаданные, служебные слова
            item_chars = post_len + comments_len + 500

            if current_chars + item_chars > max_chars and current_batch:
                batches.append(current_batch)
                current_batch = [item]
                current_chars = item_chars
            else:
                current_batch.append(item)
                current_chars += item_chars

        if current_batch:
            batches.append(current_batch)

        return batches

    batches = split_by_text_length(posts_with_comments, max_chars=MAX_CHARS_PER_BATCH)

    logger.info(f"Разбито {total_items} записей на {len(batches)} пакетов (лимит {MAX_CHARS_PER_BATCH} символов)")

    global processing_progress
    if session_id in processing_progress:
        del processing_progress[session_id]

    processing_progress[session_id] = {
        "current_batch": 0,
        "total_batches": len(batches),
        "total_posts": total_items,
        "total_comments": sum(len(item.get("comments", [])) for item in posts_with_comments),
        "status": "processing"
    }
    # ПАКЕТНАЯ ОБРАБОТКА
    batch_results = []
    
    # Системный промпт для пакетной обработки
    batch_system_message = {
        "role": "system",
        "content": """Ты — ассистент для фильтрации комментариев в социальной сети VK.
Твоя задача: проанализировать предоставленные посты с комментариями и выбрать ТОЛЬКО те, которые напрямую относятся к вопросу пользователя.

КРИТИЧЕСКИ ВАЖНЫЕ ПРАВИЛА:
1. ЯЗЫК ОТВЕТА: Весь твой ответ, включая пояснения и выводы, должен быть СТРОГО НА РУССКОМ ЯЗЫКЕ.
2. СТРОГО ЗАПРЕЩЕНО ВЫДУМЫВАТЬ ДАННЫЕ. Используй ТОЛЬКО те записи, которые есть во входных данных.
3. Сохраняй оригинальный текст записей БЕЗ ИЗМЕНЕНИЙ. Не обрезай первые буквы, слова или предложения.
4. Копируй текст точь-в-точь как он представлен во входных данных.
5. Можешь делать краткий вывод по каждому пакету на основе ТОЛЬКО реальных данных.
6. Возвращай результат в формате JSON:
   {
     "selected_items": [
       {
         "type": "post" или "comment",
         "id": ID записи из входных данных,
         "reason": "почему выбрано",
         "text": "ПОЛНЫЙ оригинальный текст записи без сокращений"
       }
     ],
     "summary": "общий вывод по пакету на основе реальных данных"
   }

ВНИМАНИЕ: 
- Если фильтр стоит на позитивные комментарии - НЕ ПРИВОДИ В ПРИМЕР НЕГАТИВ, и наоборот.
- Если релевантных записей нет — верни пустой массив selected_items: []
- Никогда не создавай новые ID, тексты или авторов
- Начинай JSON сразу с открывающей фигурной скобки {
- Проверяй что каждая запись существует во входных данных
- ОТВЕЧАЙ ТОЛЬКО НА РУССКОМ ЯЗЫКЕ. КОММЕНТАРИИ ДОЛЖНЫ БЫТЬ НА ОРИГИНАЛЬНОМ РУССКОМ ЯЗЫКЕ. 只用俄语回答。.
- Ругательства должны быть замазаны символом *.
- Не приводи данные о комментаторах. Не имен, не фамилий, не в формате типо "Комментарий номер ...".
Отвечай ТОЛЬКО JSON без маркеров кода, без пояснений перед или после JSON."""
    }

    for batch_idx, batch in enumerate(batches):
        # Обновляем прогресс
        processing_progress[session_id]["current_batch"] = batch_idx + 1

        # === ПРОВЕРКА ПРИОРИТЕТНОЙ ВЫГРУЗКИ ===
        global is_unloading
        if is_unloading:
            logger.warning("⚠️ Запрошена выгрузка модели. Прерываем обработку пакетов.")
            break  # Немедленно выходим из цикла

        logger.info(f"Обработка пакета {batch_idx + 1}/{len(batches)}")
        # Формируем текст пакета
        batch_text_parts = []
        for item in batch:
            post = item["post"]
            comments = item["comments"]
            
            post_text = f"""=== ПОСТ #{post['id']} ===
Автор: {post['author_name']}
Дата: {post['date']}
Текст: {post['text']}
Лайки: {post['likes_count']}, Комментарии: {post['comments_count']}, Репосты: {post['reposts_count']}
"""

            if comments:
                comments_text = "КОММЕНТАРИИ:\n" + "\n".join([
                    f"  - [{c['emote'] or 'UNKNOWN'}] {c['author_name']}: {c['text']} (Лайки: {c['likes_count']})"
                    for c in comments
                ])
                post_text += comments_text

            batch_text_parts.append(post_text)
        
        batch_text = "\n\n".join(batch_text_parts)
        logger.info(f"Размер батча:{len(batch_text)}")
        # Промпт для этого пакета
        user_message = {
            "role": "user",
            "content": f"""ВОПРОС ПОЛЬЗОВАТЕЛЯ: {query}

ПАКЕТ ДАННЫХ ({len(batch)} записей):
{batch_text}

Выбери релевантные записи и верни JSON."""
        }
        
        # Генерация ответа для пакета
        async with gpu_lock:
            # Повторная проверка внутри блокировки на всякий случай
            if is_unloading:
                logger.warning("⚠️ Выгрузка запрошена во время захвата GPU. Прерываем.")
                break

            try:
                # ВАЖНО: добавляем await, так как функция теперь асинхронная
                batch_response = await generate_text(
                    [batch_system_message, user_message],
                    max_new_tokens=BATCH_MAX_TOKENS,
                    temperature=BATCH_TEMPERATURE,
                    top_p=0.15,  # Используем оптимизированное значение
                    repetition_penalty=1.05
                )
            except Exception as e:
                logger.error(f"Ошибка генерации для пакета {batch_idx}: {e}")
                logger.error(f"Текст запроса:{batch_system_message}")
                #logger.error(f"Размер батча:{len(batch_results)}")
                logger.error(f"Сообщение юзера (первые 200 символов): {user_message.get('content', '')[:200]}")
                batch_response = '{"selected_items": [], "summary": "Ошибка обработки"}'
        
        # Очистка от маркеров кода
        if batch_response.startswith("```"):
            batch_response = re.sub(r"^```(?:json)?\s*", "", batch_response)
            batch_response = re.sub(r"\s*```$", "", batch_response)
        
        batch_results.append(batch_response)

        torch.cuda.empty_cache()
        gc.collect()
        # Логирование ответа модели для этого пакета
        logger.info(f"=== ПАКЕТ {batch_idx + 1}/{len(batches)} - ОТВЕТ МОДЕЛИ ===")
        logger.info(f"Входные данные пакета ({len(batch)} записей): {batch_text[:2000]}...")
        logger.info(f"Ответ модели: {batch_response}")
        logger.info(f"=========================================")
        
        logger.info(f"Пакет {batch_idx + 1} обработан")
    
    # ОБЪЕДИНЕНИЕ РЕЗУЛЬТАТОВ
    # Формируем итоговый промпт для финальной обработки
    combined_results = "\n\n===\n\n".join([
        f"РЕЗУЛЬТАТ ПАКЕТА {i+1}:\n{result}"
        for i, result in enumerate(batch_results)
    ])
    
    final_system_message = {
        "role": "system",
        "content": """Ты — аналитик социальной сети VK.
Твоя задача: на основе кратких выборок из пакетов создать итоговый ответ на вопрос пользователя.

КРИТИЧЕСКИ ВАЖНЫЕ ПРАВИЛА:
1. ЯЗЫК ОТВЕТА: Весь твой ответ должен быть СТРОГО НА РУССКОМ ЯЗЫКЕ.
2. СТРОГО ЗАПРЕЩЕНО ВЫДУМЫВАТЬ ДАННЫЕ. Используй ТОЛЬКО информацию из предоставленных выборок.
3. Проанализируй все предоставленные выборки
4. Найди наиболее релевантные ответы
5. Сделай общий вывод на основе ТОЛЬКО реальных данных
6. Если данных недостаточно — скажи об этом честно
7. Не создавай новые факты, цитаты или мнения которых нет во входных данных
8. Сохраняй оригинальные формулировки из комментариев без искажений
9. Не прикладывай личные данные авторов. Не нужно приводить номера записей, типо "Комментатор N написал..." или "В комментарии N написано..."
Не надо приводить в пример их имена и т.д. ."""
    }
    
    final_user_message = {
        "role": "user",
        "content": f"""ВОПРОС ПОЛЬЗОВАТЕЛЯ: {query}

КРАТКАЯ ВЫБОРКА САМЫХ ПОДХОДЯЩИХ ОТВЕТОВ ИЗ ВСЕХ ПАКЕТОВ:
{combined_results}

ДАЙ РАЗВЕРНУТЫЙ ОТВЕТ НА ВОПРОС, ОСНОВЫВАЯСЬ НА ЭТИХ ДАННЫХ С УЧЕТОМ СИСТЕМНЫХ ИНСТРУКЦИЙ.
КРИТИЧЕСКИ ВАЖНЫЕ ПРАВИЛА:
1. ЯЗЫК ОТВЕТА: Весь твой ответ должен быть СТРОГО НА РУССКОМ ЯЗЫКЕ.
2. СТРОГО ЗАПРЕЩЕНО ВЫДУМЫВАТЬ ДАННЫЕ. Используй ТОЛЬКО информацию из предоставленных выборок.
3. Проанализируй все предоставленные выборки
4. Найди наиболее релевантные ответы
5. Сделай общий вывод на основе ТОЛЬКО реальных данных
6. Если данных недостаточно — скажи об этом честно
7. Не создавай новые факты, цитаты или мнения которых нет во входных данных
8. Сохраняй оригинальные формулировки из комментариев без искажений
9. Не прикладывай личные данные авторов. Не нужно приводить номера записей, типо "Комментатор N написал..." или "В комментарии N написано..."
Не надо приводить в пример их имена и т.д. Вся информация должна быть максимально анонимна. ."""
    }
    
    # Финальная генерация
    async with gpu_lock:
        try:
            final_answer = await generate_text(
                [final_system_message, final_user_message],
                max_new_tokens=FINAL_MAX_TOKENS,
                temperature=TEMPERATURE_CHAT,
                top_p=TOP_P,
                repetition_penalty=REPETITION_PENALTY
            )
        except Exception as e:
            logger.error(f"Ошибка финальной генерации: {e}")
            raise HTTPException(status_code=500, detail=f"Ошибка генерации: {str(e)}")
    processing_progress[session_id]["status"] = "completed"
    # Логирование финального ответа
    logger.info("=== ФИНАЛЬНЫЙ ОТВЕТ МОДЕЛИ ===")
    logger.info(f"Запрос пользователя: {query}")
    logger.info(f"Обработано пакетов: {len(batches)}, Всего записей: {total_items}")
    logger.info(f"Финальный ответ: {final_answer}")
    logger.info("==============================")
    
    return final_answer, total_items, batch_results


# ============================================================
# ЭНДПОИНТЫ
# ============================================================




@app.post("/chat/enhanced", response_model=EnhancedChatResponse)
async def enhanced_chat_endpoint(request: EnhancedChatRequest, db: Session = Depends(get_db)):
    """
    Расширенный эндпоинт чата с пакетной обработкой комментариев.
    
    Отличия от обычного /chat:
    1. Использует пакетную обработку между этапами 4 и 5
    2. Обрабатывает данные порциями (по умолчанию 250 записей)
    3. Каждый пакет фильтруется моделью на релевантность
    4. Посты группируются с комментариями для лучшего контекста
    5. Итоговый ответ формируется на основе всех обработанных пакетов
    """
    
    # Проверка что модель загружена
    if not model_loaded:
        raise HTTPException(
            status_code=503,
            detail="Модель выгружена. Вызовите POST /model/reload для загрузки."
        )
    
    # Сбрасываем таймер выгрузки при каждом запросе
    reset_unload_timer()
    
    # === 1. СЕССИЯ ===
    session_id = get_or_create_session(db, request.project_id, request.session_id)

    # Логирование входящего сообщения пользователя
    logger.info(f"=== ENHANCED CHAT: ВХОДЯЩЕЕ СООБЩЕНИЕ (Session ID: {session_id}) ===")
    logger.info(f"Текст запроса: {request.query}")
    logger.info(f"Размер пакета: {request.batch_size or BATCH_SIZE}")

    # === 2. ИСТОРИЯ ===
    history = await asyncio.to_thread(get_history, db, session_id, HISTORY_LIMIT)

    # === 3. INTENT PARSER ===
    llm_filters = await parse_intent(request.query)

    # Программное слияние: manual_filters имеют абсолютный приоритет
    filters = llm_filters.copy()
    if request.manual_filters:
        for key, value in request.manual_filters.items():
            if value is not None:
                # Если в интерфейсе задано значение (не null) - используем его принудительно
                filters[key] = value

    # Логирование результатов parse_intent
    logger.info(f"=== ENHANCED CHAT: РЕЗУЛЬТАТ PARSE_INTENT (Session ID: {session_id}) ===")
    logger.info(f"Полученные фильтры: {json.dumps(filters, ensure_ascii=False)}")

    # === 4-5. ПАКЕТНАЯ ОБРАБОТКА КОММЕНТАРИЕВ ===
    batch_size = request.batch_size or BATCH_SIZE

    try:
        final_answer, total_items, batch_results = await process_comments_batch(
            db=db,
            query=request.query,
            project_id=request.project_id,
            filters=filters,
            session_id=session_id,  # Добавляем session_id
            batch_size=batch_size
        )
        
        batches_processed = len(batch_results)
        logger.info(f"Обработано {batches_processed} пакетов, всего {total_items} записей")

    except Exception as e:
        logger.error(f"Ошибка пакетной обработки: {e}", exc_info=True)
        # Очищаем прогресс при ошибке
        if session_id in processing_progress:
            del processing_progress[session_id]
        raise HTTPException(status_code=500, detail=f"Ошибка пакетной обработки: {str(e)}")

    # Логирование ответа модели
    logger.info(f"=== ENHANCED CHAT: ОТВЕТ МОДЕЛИ (Session ID: {session_id}) ===")
    logger.info(f"Текст ответа: {final_answer}")

    # === 6. СОХРАНЕНИЕ В БД ===
    try:
        user_msg = save_message(db, session_id, 'user', request.query, filters=filters, manual_filters=request.manual_filters)
        assistant_msg = save_message(db, session_id, 'assistant', final_answer, sources=total_items, filters=filters, manual_filters=request.manual_filters)
        
        # Теперь делаем commit один раз для всех сообщений
        db.commit()
        
        logger.info(f"Сообщения сохранены и закоммичены. ID сообщения ассистента: {assistant_msg.id}")
    except Exception as e:
        logger.error(f"Ошибка при сохранении сообщений: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка сохранения сообщений в БД: {str(e)}")

    return EnhancedChatResponse(
        answer=final_answer,
        session_id=session_id,
        sources_count=total_items,
        batches_processed=batches_processed,
        applied_filters=filters,
        message_id=assistant_msg.id
    )


@app.post("/parse-intent", response_model=IntentResponse)
async def parse_intent_endpoint(request: IntentRequest):
    """Отдельный эндпоинт для тестирования парсера намерений"""
    filters = await parse_intent(request.query, request.project_stats)
    return IntentResponse(filters=filters)


@app.post("/session/reset")
async def reset_session(request: SessionResetRequest, db: Session = Depends(get_db)):
    """Очищает историю сообщений сессии"""
    from ModelsBD import ChatMessage

    db.query(ChatMessage).filter(ChatMessage.session_id == request.session_id).delete()
    db.commit()
    return {"status": "success", "message": "История диалога очищена"}


@app.delete("/session/{session_id}")
async def delete_session(session_id: int, db: Session = Depends(get_db)):
    """Удаляет сессию полностью"""
    from ModelsBD import ChatSession

    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Сессия не найдена")

    db.delete(session)
    db.commit()
    return {"status": "deleted"}


@app.get("/health")
async def health_check():
    """Проверка статуса сервиса"""

    # === СБОР ИНФОРМАЦИИ О ПАМЯТИ GPU ===
    # =====================================

    return {
        "status": "healthy" if model_loaded else "degraded",
        "model_loaded": model_loaded,
        "device": str(device) if device else "none",
        "gpu_lock_available": not gpu_lock.locked() if gpu_lock else True,
    }


@app.on_event("startup")
async def cleanup_old_sessions():
    async def cleanup_task():
        while True:
            await asyncio.sleep(3600)
            try:
                from ModelsBD import ChatSession  # ← Явный импорт внутри функции
                db = SessionLocal()
                try:
                    cutoff = datetime.utcnow() - timedelta(hours=SESSION_TTL_HOURS)
                    db.query(ChatSession).filter(
                        ChatSession.updated_at < cutoff,
                        ChatSession.is_active == True
                    ).update({"is_active": False})
                    db.commit()
                    print("🧹 Очистка старых сессий завершена")
                finally:
                    db.close()
            except Exception as e:
                print(f"⚠️ Ошибка очистки: {e}")
    asyncio.create_task(cleanup_task())


# В конец файла llm_service.py, перед if __name__ == "__main__":

@app.post("/session/clear")
async def clear_session(request: SessionResetRequest):
    """Очистка конкретной сессии (для внутреннего кэша LLM)"""
    from ModelsBD import ChatMessage

    # Очистка в БД
    db = SessionLocal()
    try:
        db.query(ChatMessage).filter(
            ChatMessage.session_id == request.session_id
        ).delete()
        db.commit()
    finally:
        db.close()

    # Здесь можно добавить очистку внутреннего кэша модели если есть
    return {"status": "success", "session_id": request.session_id}


@app.post("/session/clear/all")
async def clear_all_sessions(project_id: Optional[int] = None):
    """Массовая очистка сессий"""
    from ModelsBD import ChatSession, ChatMessage

    db = SessionLocal()
    try:
        sessions_query = db.query(ChatSession)
        if project_id:
            sessions_query = sessions_query.filter(ChatSession.project_id == project_id)

        session_ids = [s.id for s in sessions_query.all()]

        if session_ids:
            db.query(ChatMessage).filter(
                ChatMessage.session_id.in_(session_ids)
            ).delete(synchronize_session=False)

            sessions_query.update({"is_active": False})
            db.commit()

        return {"status": "success", "sessions_cleared": len(session_ids)}
    finally:
        db.close()


@app.post("/model/reload")
async def reload_model():
    global model, tokenizer, model_loaded, device

    if model_loaded:
        return {"status": "info", "message": "Модель уже загружена"}

    print(" Загрузка модели Qwen2.5-7B-Instruct (4-bit quantization)...")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    logger.info(f"device:{device}")
    try:

        tokenizer = AutoTokenizer.from_pretrained(
            MODEL_NAME,
            trust_remote_code=True,
            cache_dir=CACHE_DIR
        )
        tokenizer.pad_token = tokenizer.eos_token


        from transformers import BitsAndBytesConfig
        quantization_config = BitsAndBytesConfig(
            load_in_8bit=True,  # 8 бит вместо 4
        )


        model = AutoModelForCausalLM.from_pretrained(
            MODEL_NAME,
            quantization_config=quantization_config,
            device_map=device,
            trust_remote_code=True,
            cache_dir=CACHE_DIR,
            low_cpu_mem_usage=True,
            torch_dtype=torch.float16
        )

        model.eval()
        model_loaded = True
        reset_unload_timer()

        print(" Модель загружена.")
        return {"status": "success", "message": "Модель загружена", "device": str(device)}

    except Exception as e:
        print(f" Ошибка загрузки модели: {e}")
        model_loaded = False
        raise HTTPException(status_code=500, detail=f"Ошибка загрузки модели: {str(e)}")


@app.get("/resources/usage")
async def get_resource_usage():
    """Информация о потреблении ресурсов"""
    import torch

    memory_usage = {}

    if torch.cuda.is_available():
        memory_usage = {
            "gpu_allocated": torch.cuda.memory_allocated() / 1024 ** 2,  # MB
            "gpu_reserved": torch.cuda.memory_reserved() / 1024 ** 2,  # MB
            "gpu_utilization": torch.cuda.utilization() if hasattr(torch.cuda, 'utilization') else None
        }

    return {
        "device": str(device),
        "model_loaded": model is not None,
        "memory_usage": memory_usage,
        "gpu_lock_available": not gpu_lock.locked() if gpu_lock else True
    }


@app.post("/model/unload")
async def unload_model():
    """Приоритетная выгрузка модели из памяти."""
    global model, tokenizer, model_loaded, is_unloading, unload_task
    import torch
    import gc

    if not model_loaded:
        return {"status": "info", "message": "Модель уже выгружена"}

    print(" ЗАПРОШЕНА ПРИОРИТЕТНАЯ ВЫГРУЗКА МОДЕЛИ...")

    # 1. Устанавливаем флаг, чтобы циклы генерации прервались
    is_unloading = True

    # 2. Отменяем фоновую задачу таймера, если она есть
    if unload_task and not unload_task.done():
        unload_task.cancel()
        try:
            await unload_task
        except asyncio.CancelledError:
            pass
    unload_task = None

    # 3. Ждем освобождения GPU.
    # Благодаря asyncio.to_thread, мы получим этот лок либо сразу,
    # либо через максимум 15-30 сек (когда догенерируется текущий короткий батч).
    print(" Ожидание завершения текущей микро-операции GPU...")
    async with gpu_lock:
        print(" Очистка памяти и удаление объектов...")

        del model
        del tokenizer

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.synchronize()

        gc.collect()

        model = None
        tokenizer = None
        model_loaded = False
        is_unloading = False  # Сбрасываем флаг для будущих загрузок

    print(" Модель успешно выгружена. VRAM освобождена.")
    return {
        "status": "success",
        "message": "Модель выгружена",
        "memory_freed": "approximately 5-8 GB"
    }


@app.get("/model/status")
async def get_model_status():
    """
    Проверяет текущее состояние модели.
    """
    import torch

    memory_usage = {}
    if torch.cuda.is_available():
        memory_usage = {
            "gpu_allocated_mb": round(torch.cuda.memory_allocated() / 1024 ** 2, 2),
            "gpu_reserved_mb": round(torch.cuda.memory_reserved() / 1024 ** 2, 2),
        }

    return {
        "model_loaded": model_loaded,
        "device": str(device) if device else "none",
        "model_name": MODEL_NAME if model_loaded else None,
        "memory_usage": memory_usage,
        "gpu_lock_available": not gpu_lock.locked() if gpu_lock else True
    }

from fastapi.responses import StreamingResponse
import json
import asyncio


@app.get("/api/llm/sessions/{session_id}/enhanced-progress")
async def get_enhanced_progress(session_id: int):
    """SSE endpoint для отслеживания прогресса пакетной обработки"""

    async def event_generator():
        if session_id in processing_progress and processing_progress[session_id].get('status') == 'completed':
            del processing_progress[session_id]
        last_batch = -1
        while True:
            if session_id in processing_progress:
                progress = processing_progress[session_id]

                if progress.get('current_batch', 0) != last_batch:
                    last_batch = progress.get('current_batch', 0)

                    total_batches = progress.get('total_batches', 1)
                    current_batch = progress.get('current_batch', 0)
                    avg_time_per_batch = 50

                    # === ИСПРАВЛЕНИЕ: Учет финальной обработки ===
                    remaining_batches = total_batches - current_batch

                    # Если все пакеты обработаны (remaining_batches == 0),
                    # но статус еще не 'completed', добавляем 45 секунд на финальную генерацию
                    if remaining_batches == 0 and progress.get('status') != 'completed':
                        estimated_seconds = 60
                    else:
                        estimated_seconds = remaining_batches * avg_time_per_batch
                    # ==============================================

                    event_data = {
                        "current_batch": current_batch,
                        "total_batches": total_batches,
                        "total_posts": progress.get('total_posts', 0),
                        "total_comments": progress.get('total_comments', 0),
                        "estimated_seconds": estimated_seconds,
                        "status": progress.get('status', 'processing')
                    }

                    yield f"data: {json.dumps(event_data)}\n\n"

                if progress.get('status') == 'completed':
                    # Отправляем финальный статус с 0 секунд
                    final_data = {
                        "current_batch": total_batches,
                        "total_batches": total_batches,
                        "total_posts": progress.get('total_posts', 0),
                        "total_comments": progress.get('total_comments', 0),
                        "estimated_seconds": 0,
                        "status": "completed"
                    }
                    yield f"data: {json.dumps(final_data)}\n\n"

                    await asyncio.sleep(2)  # Небольшая задержка, чтобы фронтенд успел отрисовать 100%
                    if session_id in processing_progress:
                        del processing_progress[session_id]
                    break

            await asyncio.sleep(1)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )



# АЛИАСЫ ДЛЯ СОВМЕСТИМОСТИ С ФРОНТЕНДОМ



@app.post("/api/llm/sessions/{session_id}/message/enhanced", response_model=EnhancedChatResponse)
async def session_message_enhanced_alias(session_id: int, request: EnhancedChatRequest, db: Session = Depends(get_db)):
    """Принимает запрос по старому пути и передает его в основной обработчик"""
    request.session_id = session_id
    return await enhanced_chat_endpoint(request, db)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)