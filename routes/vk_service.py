# routes/vk_service.py
import requests
import time
from datetime import datetime, timedelta
import json
import os
from datetime import datetime
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Query, BackgroundTasks, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
# Импорт моделей из корня проекта
# Убедитесь, что путь корректен относительно расположения этого файла
import sys
from sentiment_model import get_analyzer
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ModelsBD import SearchRequest, Post, Profile, Comment
from init_db import get_db, SessionLocal  # Импортируем зависимость получения сессии из вашего init_db

# ============================================================
# КОНФИГУРАЦИЯ
# ============================================================
VK_ACCESS_TOKEN = os.getenv("VK_ACCESS_TOKEN", "ВАШ_ACCESS_TOKEN_HERE")
VK_API_VERSION = "5.131"
VK_API_URL = "https://api.vk.com/method/"

MAX_EXECUTE_CALLS = 25
MAX_COMMENTS_PER_CALL = 100

router = APIRouter(prefix="/api/vk", tags=["VK Integration"])

# Ленивая инициализация анализатора - будет загружен при первом использовании
_analyzer = None

def get_model():
    """Получить анализатор тональности (ленивая загрузка)"""
    global _analyzer
    if _analyzer is None:
        _analyzer = get_analyzer()
    return _analyzer

Model = None  # Будет установлен при первом вызове get_model()


class VKAPIError(Exception):
    pass


# --- Pydantic модели для ответов ---
class TaskResponse(BaseModel):
    message: str
    posts_found: Optional[int] = 0
    comments_found: Optional[int] = 0


class SearchRequestCreate(BaseModel):
    project_id: int = Field(..., ge=1, description="ID проекта, к которому относится запрос")

    query: str = Field(..., description="Поисковой запрос")
    extended: bool = Field(False, description="Расширенная информация")
    count: int = Field(100, ge=1, le=1000, description="Максимальное число записей")
    latitude: Optional[str] = Field(None, description="Широта")
    longitude: Optional[str] = Field(None, description="Долгота")
    start_time: Optional[datetime] = Field(None, description="Время начала")
    end_time: Optional[datetime] = Field(None, description="Время конца")
    start_id: Optional[str] = Field(None, description="ID последней записи")

    # Технические параметры
    offset: Optional[int] = Field(None, description="Смещение")
    start_from: Optional[str] = Field(None, description="ID следующей страницы")
    fields: Optional[str] = Field(None, description="Дополнительные поля")


# ============================================================
# ЛОГИКА РАБОТЫ С VK API (Внутренние функции)
# ============================================================

def _make_request(method_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Базовый запрос к API VK"""
    params['access_token'] = VK_ACCESS_TOKEN
    params['v'] = VK_API_VERSION
    url = f"{VK_API_URL}{method_name}"
    #print(f"Текст запроса: {url}, {params}")
    try:
        response = requests.post(url, data=params, timeout=15)
        data = response.json()
        #print(f"Response:{data}")
        if 'error' in data:
            err_msg = data['error'].get('error_msg', 'Unknown error')
            err_code = data['error'].get('error_code', -1)
            raise VKAPIError(f"VK API Error {err_code}: {err_msg}")

        return data.get('response', {})
    except requests.exceptions.RequestException as e:
        raise VKAPIError(f"Network error: {str(e)}")


def _make_execute_request(code: str) -> Any:
    """Запрос через execute"""

    return _make_request("execute", {"code": code})


# routes/vk_service.py

def get_or_create_profile(session: Session, vk_id: int, data: Dict[str, Any], p_type: str) -> Profile:

    # Подготавливаем данные для вставки/обновления
    profile_data = {
        "vk_id": vk_id,
        "type": p_type,
        "updated_at": datetime.utcnow()
    }

    if p_type == 'user':
        profile_data.update({
            "first_name": data.get('first_name', ''),
            "last_name": data.get('last_name', ''),
            "name": f"{data.get('first_name', '')} {data.get('last_name', '')}".strip(),
            "sex": data.get('sex', 0),
            "photo_50": data.get('photo_50', ''),
            "verified": bool(data.get('verified', 0)),
            "city_id": data.get('city', {}).get('id') if isinstance(data.get('city'), dict) else None,
            "city_title": data.get('city', {}).get('title') if isinstance(data.get('city'), dict) else None,
            "country_id": data.get('country', {}).get('id') if isinstance(data.get('country'), dict) else None,
            "country_title": data.get('country', {}).get('title') if isinstance(data.get('country'), dict) else None,
            # Сброс полей группы
            "is_closed": None,
            "members_count": 0,
            "screen_name": None
        })
    else:  # group
        profile_data.update({
            "name": data.get('name', ''),
            "screen_name": data.get('screen_name', ''),
            "photo_50": data.get('photo_50', ''),
            "verified": bool(data.get('is_verified', 0)),
            "is_closed": data.get('is_closed', 0),
            "members_count": data.get('members_count', 0),
            # Сброс полей пользователя
            "first_name": None,
            "last_name": None,
            "sex": None,
            "city_id": None,
            "city_title": None,
            "country_id": None,
            "country_title": None
        })


    stmt = insert(Profile).values(profile_data)

    # Добавляем логику ON CONFLICT: если vk_id существует, делаем UPDATE
    stmt = stmt.on_conflict_do_update(
        index_elements=['vk_id'],  # По какому полю проверять конфликт (первичный ключ)
        set_=profile_data  # Какие поля обновлять при конфликте
    )

    # Выполняем запрос
    session.execute(stmt)


    session.flush()  # Сбрасываем изменения в БД, чтобы следующий query видел актуальное состояние
    profile = session.query(Profile).filter_by(vk_id=vk_id).first()

    return profile


def save_post_to_db(session: Session, search_req_id: int, post_data: Dict[str, Any], owner_id: int) -> Post:

    author_vk_id = post_data.get('from_id', owner_id)

    # Проверка на существующий пост
    existing_post = session.query(Post).filter_by(
        vk_owner_id=owner_id,
        vk_post_id=post_data['id']
    ).first()

    if existing_post:
        existing_post.likes_count = post_data.get('likes', {}).get('count', 0)
        existing_post.comments_count = post_data.get('comments', {}).get('count', 0)
        existing_post.reposts_count = post_data.get('reposts', {}).get('count', 0)
        existing_post.views_count = post_data.get('views', {}).get('count', 0)
        existing_post.text = post_data.get('text', '')
        return existing_post

    post = Post(
        vk_post_id=post_data['id'],
        vk_owner_id=owner_id,
        search_request_id=search_req_id,
        author_vk_id=author_vk_id,
        date=datetime.fromtimestamp(post_data.get('date', 0)),
        text=post_data.get('text', ''),
        likes_count=post_data.get('likes', {}).get('count', 0),
        comments_count=post_data.get('comments', {}).get('count', 0),
        reposts_count=post_data.get('reposts', {}).get('count', 0),
        views_count=post_data.get('views', {}).get('count', 0),
    )


    session.add(post)
    return post


def fetch_search_posts(session: Session, search_request: SearchRequest) -> List[Post]:
    # Выполняет поиск постов и сохраняет их в БД.
    #
    # ВАЖНО: Временной диапазон разбивается на отдельные дни для обхода лимита VK API
    # (максимум 200 постов в одном запросе). Каждый день обрабатывается отдельным запросом.

    print(f"🔍 [VK Service] Поиск для запроса ID {search_request.id}: '{search_request.query}'")

    from datetime import timedelta
    

    if search_request.start_time:
        start_dt = search_request.start_time
    else:
        # По умолчанию - 1 год назад
        start_dt = datetime.utcnow() - timedelta(days=7)
    
    if search_request.end_time:
        end_dt = search_request.end_time
    else:
        # По умолчанию - текущее время
        end_dt = datetime.utcnow()
    
    # Разбиваем диапазон на отдельные дни
    current_date = start_dt.date()
    end_date = end_dt.date()
    
    all_created_posts = []
    day_count = 0
    
    while current_date <= end_date:
        # Вычисляем границы текущего дня
        day_start = datetime.combine(current_date, datetime.min.time())
        day_end = datetime.combine(current_date, datetime.max.time())
        
        # Корректируем end_time для последнего дня, чтобы не выходить за пределы запроса
        if current_date == end_date and end_dt.time() != datetime.max.time():
            day_end = end_dt
        
        start_ts = int(day_start.timestamp())
        end_ts = int(day_end.timestamp())
        
        params = {
            'q': search_request.query,
            'count': min(search_request.count, 200),
            'extended': 1,
            'start_time': start_ts,
            'end_time': end_ts
        }

        if search_request.latitude and search_request.longitude:
            params['lat'] = search_request.latitude
            params['long'] = search_request.longitude
        if search_request.start_id:
            params['start_from'] = search_request.start_id

        try:
            print(f" [VK Service] Обработка дня: {current_date}")
            response = _make_request("newsfeed.search", params)
        except VKAPIError as e:
            print(f" [VK Service] Ошибка поиска за {current_date}: {e}")
            current_date += timedelta(days=1)
            day_count += 1
            continue

        items = response.get('items', [])
        profiles_data = {p['id']: p for p in response.get('profiles', [])}
        groups_data = {g['id']: g for g in response.get('groups', [])}

        day_posts_count = 0
        for item in items:
            if item.get('type') != 'post':
                continue

            post_data = item
            owner_id = post_data.get('owner_id')
            author_id = post_data.get('from_id', owner_id)

            p_type = 'group' if author_id < 0 else 'user'
            info = groups_data.get(abs(author_id), {}) if author_id < 0 else profiles_data.get(author_id, {})

            # 1. Сначала гарантированно создаем/обновляем профиль
            get_or_create_profile(session, author_id, info, p_type)

            # 2. Затем сохраняем пост (профиль уже в сессии благодаря merge)
            new_post = save_post_to_db(session, search_request.id, post_data, owner_id)
            all_created_posts.append(new_post)
            day_posts_count += 1

        session.commit()
        print(f"За {current_date} найдено постов: {day_posts_count}")
        
        current_date += timedelta(days=1)
        day_count += 1
    
    print(f" [VK Service] Всего найдено постов за {day_count} дн.: {len(all_created_posts)}")
    return all_created_posts


def fetch_comments_batch(session: Session, posts: List[Post]) -> int:
    """
    Пакетная загрузка комментариев с анализом тональности.
    Логика:
    - Существующие комментарии обновляются (лайки).
    - Новые комментарии создаются и сразу анализируются моделью.
    """
    if not posts:
        return 0

    total_comments_processed = 0
    # Разбиваем посты на пачки для execute (лимит 25 запросов)
    batches = [posts[i:i + MAX_EXECUTE_CALLS] for i in range(0, len(posts), MAX_EXECUTE_CALLS)]

    for batch_idx, batch in enumerate(batches):
        print(f" [VK Service] Загрузка комментариев: пачка {batch_idx + 1}/{len(batches)}")

        script_calls = []
        post_mapping = {}

        for idx, post in enumerate(batch):
            oid = post.vk_owner_id
            pid = post.vk_post_id
            call = f"""
            var comm_{idx} = API.wall.getComments({{
                "owner_id": {oid},
                "post_id": {pid},
                "count": {MAX_COMMENTS_PER_CALL},
                "need_likes": 1,
                "extended": 1,
                "sort": "asc"
            }});
            """
            script_calls.append(call)
            post_mapping[idx] = post

        return_array = "[" + ",".join([f"comm_{i}" for i in range(len(batch))]) + "]"
        full_code = "".join(script_calls) + f"return {return_array};"

        try:
            responses = _make_execute_request(full_code)
        except VKAPIError as e:
            print(f" Ошибка пачки {batch_idx}: {e}")
            continue

        if not isinstance(responses, list):
            responses = [responses]

        # Буфер для пакетного анализа: (объект Comment, текст, объект Post)
        comments_to_save = []

        for idx, resp in enumerate(responses):
            post = post_mapping[idx]
            if not resp or 'items' not in resp:
                continue

            comments_items = resp.get('items', [])
            profiles_data = {p['id']: p for p in resp.get('profiles', [])}
            groups_data = {g['id']: g for g in resp.get('groups', [])}

            for c_data in comments_items:
                c_id = c_data.get('id')
                author_id = c_data.get('from_id')
                text = c_data.get('text', '')

                # Проверяем наличие комментария в БД
                exists = session.query(Comment).filter_by(
                    vk_comment_id=c_id,
                    post_id=post.id
                ).first()

                if exists:
                    # Если есть - обновляем лайки (вдруг изменились)
                    # Тональность можно не пересчитывать, если текст не изменился (а он редко меняется)
                    if exists.likes_count != c_data.get('likes', {}).get('count', 0):
                        exists.likes_count = c_data.get('likes', {}).get('count', 0)
                    continue

                    # Если комментария нет - готовим к созданию
                comment = Comment(
                    vk_comment_id=c_id,
                    post_id=post.id,
                    author_vk_id=author_id,
                    date=datetime.fromtimestamp(c_data.get('date', 0)),
                    text=text,
                    likes_count=c_data.get('likes', {}).get('count', 0),
                    parent_id=c_data.get('thread', {}).get('comment_id') if c_data.get('thread') else None,
                    reply_to_uid=c_data.get('reply_to_uid'),
                    reply_to_cid=c_data.get('reply_to_comment_id'),
                    raw_data={
                        "thread": c_data.get('thread'),
                        "owner_id": post.vk_owner_id,
                        "post_id": post.vk_post_id
                    },
                    emote=None,  # Будет заполнено моделью
                    conf=None  # Будет заполнено моделью
                )

                # Создаем/обновляем профиль автора
                p_type = 'group' if author_id < 0 else 'user'
                info = groups_data.get(abs(author_id), {}) if author_id < 0 else profiles_data.get(author_id, {})
                get_or_create_profile(session, author_id, info, p_type)

                # Добавляем в буфер
                comments_to_save.append((comment, text, post))

        # --- ПАКЕТНЫЙ АНАЛИЗ ТОНАЛЬНОСТИ ---
        if comments_to_save:
            texts_batch = [text for _, text, _ in comments_to_save]

            print(f"Анализ тональности для {len(texts_batch)} новых комментариев...")
            try:
                model = get_model()
                if model is None:
                    raise RuntimeError("Модель тональности не загружена")
                analysis_results = model.predict_batch(texts_batch)
            except Exception as model_err:
                print(f" Ошибка модели тональности: {model_err}")
                # Заглушки при ошибке модели
                analysis_results = [{"label": None, "confidence": None} for _ in texts_batch]

            # Применяем результаты и добавляем в сессию
            for (comment, _, _), result in zip(comments_to_save, analysis_results):
                comment.emote = result.get('label')
                comment.conf = result.get('confidence')
                session.add(comment)

            total_comments_processed += len(comments_to_save)



        session.commit()
        time.sleep(0.5)  # Пауза для соблюдения лимитов VK API

    print(
        f" [VK Service] Обработано комментариев: {total_comments_processed} (старые обновлены, новые проанализированы)")
    return total_comments_processed

# ============================================================
# ФОНОВЫЕ ЗАДАЧИ
# ============================================================

def _run_full_pipeline(request_id: int):

    db = SessionLocal()
    try:
        req = db.query(SearchRequest).filter(SearchRequest.id == request_id).first()
        if not req:
            print(f" Запрос {request_id} не найден")
            return

        print(f" Начало выполнения задачи {request_id}...")

        posts = fetch_search_posts(db, req)

        comments_count = 0
        if posts:
            comments_count = fetch_comments_batch(db, posts)

        print(f" Задача {request_id} завершена. Постов: {len(posts)}, Комментов: {comments_count}")

    except VKAPIError as e:
        print(f" Ошибка VK API в задаче {request_id}: {e}")
    except Exception as e:
        print(f" Критическая ошибка в задаче {request_id}: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()


def _crawl_comments_task(post_ids: List[int]):
    """Фоновая задача только для комментариев"""
    db = SessionLocal()
    try:
        posts = db.query(Post).filter(Post.id.in_(post_ids)).all()
        if not posts:
            return
        fetch_comments_batch(db, posts)
    except Exception as e:
        print(f"❌ Ошибка в задаче комментариев: {e}")
    finally:
        db.close()  # === ОБЯЗАТЕЛЬНО ЗАКРЫВАЕМ СЕССИЮ ===


# ============================================================
# ЭНДПОИНТЫ (API ROUTES)
# ============================================================


@router.post("/search-requests/{request_id}/run", response_model=TaskResponse)
async def run_search_task(
        request_id: int,
        background_tasks: BackgroundTasks,
        db: Session = Depends(get_db)
):

    req = db.query(SearchRequest).filter(SearchRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Запрос не найден")


    background_tasks.add_task(_run_full_pipeline, request_id)

    print(f" Задача {request_id} добавлена в очередь фоновых задач")

    return TaskResponse(
        message=f"Задача {request_id} запущена в фоновом режиме",
        posts_found=0,
        comments_found=0
    )


@router.post("/posts/crawl-comments", response_model=TaskResponse)
async def crawl_comments_for_posts(
    post_ids: List[int] = Query(..., description="Список ID постов из БД"),
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db)
):
    """Принудительный загрузчик комментариев для конкретных постов."""
    posts = db.query(Post).filter(Post.id.in_(post_ids)).all()
    if not posts:
        raise HTTPException(status_code=404, detail="Посты не найдены")

    if background_tasks:

        background_tasks.add_task(_crawl_comments_task, post_ids)
        return TaskResponse(message="Загрузка комментариев запущена в фоне")
    else:
        count = fetch_comments_batch(db, posts)
        db.commit()
        return TaskResponse(message="Готово", comments_found=count)


@router.get("/status/{request_id}")
async def get_request_status(request_id: int, db: Session = Depends(get_db)):
    """Проверка статуса выполнения задачи"""
    req = db.query(SearchRequest).filter(SearchRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Запрос не найден")

    # Подсчет связанных постов и комментариев для статистики
    posts_count = db.query(Post).filter(Post.search_request_id == request_id).count()
    comments_count = db.query(Comment).join(Post).filter(Post.search_request_id == request_id).count()

    return {
        "id": req.id,
        "query": req.query,
        "posts_count": posts_count,
        "comments_count": comments_count,
        "created_at": req.created_at
    }