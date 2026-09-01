# routes/projects.py

from fastapi import APIRouter, HTTPException, Query, Depends, BackgroundTasks
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import func
from sqlalchemy import text
from init_db import get_db
from ModelsBD import Project, SearchRequest, Post, Comment, Profile
import json
from routes.vk_service import crawl_comments_for_posts

router = APIRouter(prefix="/api/projects", tags=["Projects Management"])

def touch_project(db: Session, project_id: int):
    """Обновляет дату последнего обращения к проекту"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if project:
        project.last_accessed_at = datetime.utcnow()
        db.commit()
# ============================================================
# Pydantic Модели
# ============================================================

class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="Название проекта")
    description: Optional[str] = Field(None, description="Описание проекта")


class ProjectResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    created_at: datetime
    last_accessed_at: Optional[datetime] = None
    is_active: bool
    search_requests_count: int = 0
    total_posts: int = 0
    total_comments: int = 0

    class Config:
        from_attributes = True


class ProjectListResponse(BaseModel):
    total: int
    data: List[ProjectResponse]



# ============================================================
# Вспомогательные функции
# ============================================================

def get_project_stats(session: Session, project_id: int) -> dict:
    """Получает статистику по проекту"""
    # Количество поисковых запросов
    requests_count = session.query(SearchRequest).filter(
        SearchRequest.project_id == project_id
    ).count()

    # Количество постов (через join с search_requests)
    posts_count = session.query(Post).join(SearchRequest).filter(
        SearchRequest.project_id == project_id
    ).count()

    # Количество комментариев (через join с Post -> SearchRequest)
    comments_count = session.query(Comment).join(Post).join(SearchRequest).filter(
        SearchRequest.project_id == project_id
    ).count()

    return {
        "search_requests_count": requests_count,
        "total_posts": posts_count,
        "total_comments": comments_count
    }


# Эндпоинты для SearchRequest (должны быть ДО общих путей)

class SearchRequestResponse(BaseModel):
    id: int
    created_at: datetime
    project_id: Optional[int] = None
    query: Optional[str] = None
    extended: bool = False
    count: int = 100
    latitude: Optional[str] = None
    longitude: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    start_id: Optional[str] = None
    params_json: Optional[dict] = None
    posts_count: int = 0
    comments_count: int = 0
    negative_count: int = 0
    positive_count: int = 0
    neutral_count: int = 0

    class Config:
        from_attributes = True

class SearchRequestListResponse(BaseModel):
    total: int
    data: List[SearchRequestResponse]

class SearchRequestCreate(BaseModel):
    query: Optional[str] = Field(None, description="Поисковой запрос")
    extended: Optional[bool] = Field(False, description="Расширенная информация")
    count: Optional[int] = Field(100, ge=1, le=1000, description="Максимальное число записей")
    latitude: Optional[str] = Field(None, description="Широта точки поиска")
    longitude: Optional[str] = Field(None, description="Долгота точки поиска")
    start_time: Optional[datetime] = Field(None, description="Время начала поиска")
    end_time: Optional[datetime] = Field(None, description="Время конца поиска")
    start_id: Optional[str] = Field(None, description="ID последней полученной записи")
    params_json: Optional[dict] = Field(None, description="Дополнительные параметры пагинации")

@router.get("/{project_id}/search_requests/", response_model=SearchRequestListResponse)
async def list_search_requests(
        project_id: int,
        skip: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=1000),
        db: Session = Depends(get_db)
):

    # Проверка существования проекта
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Проект не найден")

    query = db.query(SearchRequest).filter(
        SearchRequest.project_id == project_id
    )

    total = query.count()
    requests = query.offset(skip).limit(limit).all()

    result = []
    for req in requests:
        stats = get_search_request_stats(db, req.id)
        result.append(SearchRequestResponse(
            id=req.id,
            created_at=req.created_at,
            project_id=req.project_id,
            query=req.query,
            extended=req.extended,
            count=req.count,
            latitude=req.latitude,
            longitude=req.longitude,
            start_time=req.start_time,
            end_time=req.end_time,
            start_id=req.start_id,
            params_json=req.params_json,
            **stats
        ))

    return SearchRequestListResponse(total=total, data=result)


@router.post("/{project_id}/search_requests/", response_model=SearchRequestResponse)
async def create_search_request(
        project_id: int,
        request: SearchRequestCreate,
        db: Session = Depends(get_db)
):
    # Проверка существования проекта
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Проект не найден")

    db_request = SearchRequest(
        project_id=project_id,
        query=request.query,
        extended=request.extended if request.extended is not None else False,
        count=request.count if request.count is not None else 100,
        latitude=request.latitude,
        longitude=request.longitude,
        start_time=request.start_time,
        end_time=request.end_time,
        start_id=request.start_id,
        params_json=request.params_json
    )

    db.add(db_request)
    db.commit()
    db.refresh(db_request)

    return SearchRequestResponse(
        id=db_request.id,
        created_at=db_request.created_at,
        project_id=db_request.project_id,
        query=db_request.query,
        extended=db_request.extended,
        count=db_request.count,
        latitude=db_request.latitude,
        longitude=db_request.longitude,
        start_time=db_request.start_time,
        end_time=db_request.end_time,
        start_id=db_request.start_id,
        params_json=db_request.params_json,
        posts_count=0,
        comments_count=0
    )


@router.put("/{project_id}/search_requests/{req_id}", response_model=SearchRequestResponse)
async def update_search_request(
        project_id: int,
        req_id: int,
        request: SearchRequestCreate,
        db: Session = Depends(get_db)
):
    # Проверка существования проекта
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Проект не найден")

    # Проверка существования запроса
    db_request = db.query(SearchRequest).filter(
        SearchRequest.id == req_id,
        SearchRequest.project_id == project_id
    ).first()

    if not db_request:
        raise HTTPException(status_code=404, detail="Поисковый запрос не найден")

    # Обновление полей (только если переданы значения)
    if request.query is not None:
        db_request.query = request.query
    if request.extended is not None:
        db_request.extended = request.extended
    if request.count is not None:
        db_request.count = request.count
    if request.latitude is not None:
        db_request.latitude = request.latitude
    if request.longitude is not None:
        db_request.longitude = request.longitude
    if request.start_time is not None:
        db_request.start_time = request.start_time
    if request.end_time is not None:
        db_request.end_time = request.end_time
    if request.start_id is not None:
        db_request.start_id = request.start_id
    if request.params_json is not None:
        db_request.params_json = request.params_json

    db.commit()
    db.refresh(db_request)

    stats = get_search_request_stats(db, db_request.id)

    return SearchRequestResponse(
        id=db_request.id,
        created_at=db_request.created_at,
        project_id=db_request.project_id,
        query=db_request.query,
        extended=db_request.extended,
        count=db_request.count,
        latitude=db_request.latitude,
        longitude=db_request.longitude,
        start_time=db_request.start_time,
        end_time=db_request.end_time,
        start_id=db_request.start_id,
        params_json=db_request.params_json,
        **stats
    )


@router.delete("/{project_id}/search_requests/{req_id}")
async def delete_search_request(
        project_id: int,
        req_id: int,
        db: Session = Depends(get_db)
):
    """Удалить поисковый запрос"""
    # Проверка существования проекта
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Проект не найден")

    # Проверка существования запроса
    db_request = db.query(SearchRequest).filter(
        SearchRequest.id == req_id,
        SearchRequest.project_id == project_id
    ).first()

    if not db_request:
        raise HTTPException(status_code=404, detail="Поисковый запрос не найден")

    db.delete(db_request)
    db.commit()

    return {"message": "Поисковый запрос удален", "id": req_id}


# ============================================================
# Эндпоинты
# ============================================================

@router.post("/", response_model=ProjectResponse)
async def create_project(
        project: ProjectCreate,
        db: Session = Depends(get_db)
):
    """Создает новый проект"""
    # Проверка на уникальность названия
    existing = db.query(Project).filter(
        Project.name.ilike(project.name)
    ).first()

    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Проект с названием '{project.name}' уже существует"
        )

    db_project = Project(
        name=project.name,
        description=project.description
    )

    db.add(db_project)
    db.commit()
    db.refresh(db_project)

    return ProjectResponse(
        id=db_project.id,
        name=db_project.name,
        description=db_project.description,
        created_at=db_project.created_at,
        is_active=db_project.is_active,
        search_requests_count=0,
        total_posts=0,
        total_comments=0
    )


@router.get("/", response_model=ProjectListResponse)
async def list_projects(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    active_only: bool = Query(True, description="Только активные проекты"),
    db: Session = Depends(get_db)
):
    query = db.query(Project)
    if active_only:
        query = query.filter(Project.is_active == True)

    query = query.order_by(Project.last_accessed_at.desc())

    total = query.count()
    projects = query.offset(skip).limit(limit).all()

    result = []
    for proj in projects:
        stats = get_project_stats(db, proj.id)
        result.append(ProjectResponse(
            id=proj.id,
            name=proj.name,
            description=proj.description,
            created_at=proj.created_at,
            last_accessed_at=proj.last_accessed_at,
            is_active=proj.is_active,
            **stats
        ))

    return ProjectListResponse(total=total, data=result)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
        project_id: int,
        db: Session = Depends(get_db)
):
    project = db.query(Project).filter(Project.id == project_id).first()

    if not project:
        raise HTTPException(status_code=404, detail="Проект не найден")
    touch_project(db, project_id)
    stats = get_project_stats(db, project_id)

    return ProjectResponse(
        id=project.id,
        name=project.name,
        description=project.description,
        created_at=project.created_at,
        is_active=project.is_active,
        **stats
    )


@router.put("/{project_id}", response_model=ProjectResponse)
async def update_project(
        project_id: int,
        project: ProjectCreate,
        db: Session = Depends(get_db)
):
    # 1. Ищем проект в БД
    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Проект не найден")

    # 2. Проверка на уникальность названия (если пользователь его изменил)
    if project.name != db_project.name:
        existing = db.query(Project).filter(
            Project.name.ilike(project.name),
            Project.id != project_id  # Исключаем текущий проект из проверки
        ).first()
        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Проект с названием '{project.name}' уже существует"
            )

    # 3. Обновление основных полей
    db_project.name = project.name
    db_project.description = project.description

    # 4. Сохраняем изменения
    db.commit()
    db.refresh(db_project)

    # 5. Возвращаем обновленный проект со статистикой
    stats = get_project_stats(db, project_id)
    return ProjectResponse(
        id=db_project.id,
        name=db_project.name,
        description=db_project.description,
        created_at=db_project.created_at,
        is_active=db_project.is_active,
        **stats
    )



@router.put("/{project_id}/toggle")
async def toggle_project_status(
        project_id: int,
        db: Session = Depends(get_db)
):
    """Активировать/деактивировать проект"""
    project = db.query(Project).filter(Project.id == project_id).first()

    if not project:
        raise HTTPException(status_code=404, detail="Проект не найден")

    project.is_active = not project.is_active
    db.commit()
    db.refresh(project)

    return {
        "id": project.id,
        "name": project.name,
        "is_active": project.is_active
    }


@router.delete("/{project_id}")
async def delete_project(
        project_id: int,
        db: Session = Depends(get_db)
):
    """
    Удалить проект.

    """
    project = db.query(Project).filter(Project.id == project_id).first()

    if not project:
        raise HTTPException(status_code=404, detail="Проект не найден")

    # Проверка: есть ли связанные запросы
    requests_count = db.query(SearchRequest).filter(
        SearchRequest.project_id == project_id
    ).count()

    if requests_count > 0:

        project.is_active = False
        db.commit()
        return {
            "message": f"Проект деактивирован (имеется {requests_count} связанных запросов)",
            "id": project.id,
            "is_active": False
        }

    db.delete(project)
    db.commit()

    return {"message": "Проект удален", "id": project_id}


# ============================================================
# Pydantic Модели для SearchRequest
# ============================================================


class CommentResponse(BaseModel):
    id: int
    post_id: int
    author_vk_id: Optional[int] = None
    author_name: Optional[str] = None
    date: datetime
    text: str
    likes_count: int = 0
    emote: Optional[str] = None
    conf: Optional[float] = None
    post_link: Optional[str] = None

    class Config:
        from_attributes = True


class CommentListResponse(BaseModel):
    total: int
    data: List[CommentResponse]



# ============================================================
# Вспомогательные функции для SearchRequest
# ============================================================

def get_search_request_stats(session: Session, request_id: int) -> dict:
    """Получает статистику по поисковому запросу"""
    posts_count = session.query(Post).filter(
        Post.search_request_id == request_id
    ).count()

    comments_count = session.query(Comment).join(Post).filter(
        Post.search_request_id == request_id
    ).count()

    # Статистика по тональности (emote) - с игнорированием регистра и пробелов
    # Группируем по верхнему регистру для подсчета
    emotions_query = session.query(
        func.upper(func.trim(Comment.emote)).label("emote_upper"),
        func.count(Comment.id).label("cnt")
    ).join(Post, Comment.post_id == Post.id).filter(
        Post.search_request_id == request_id,
        Comment.emote.isnot(None),
        func.trim(Comment.emote) != ""
    ).group_by(func.upper(func.trim(Comment.emote)))
    
    emotions_result = emotions_query.all()
    
    negative_count = 0
    positive_count = 0
    neutral_count = 0
    
    for row in emotions_result:
        emote_val = row[0]
        count_val = row[1]
        
        if emote_val == "NEGATIVE":
            negative_count = count_val
        elif emote_val == "POSITIVE":
            positive_count = count_val
        elif emote_val == "NEUTRAL":
            neutral_count = count_val
            
    # Логирование для отладки
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"Stats for request {request_id}: Posts={posts_count}, Comments={comments_count}, Emotions={emotions_result} -> NEG={negative_count}, POS={positive_count}, NEU={neutral_count}")

    return {
        "posts_count": posts_count,
        "comments_count": comments_count,
        "negative_count": negative_count,
        "positive_count": positive_count,
        "neutral_count": neutral_count
    }


# ============================================================
# Эндпоинты для комментариев проекта
# ============================================================

@router.get("/{project_id}/comments/", response_model=CommentListResponse)
async def list_project_comments(
        project_id: int,
        skip: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=200),
        db: Session = Depends(get_db)
):
    """Получить список комментариев для всех постов проекта с пагинацией"""
    # Проверка существования проекта
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Проект не найден")

    # Получаем все SearchRequest для этого проекта
    search_request_ids = [sr.id for sr in db.query(SearchRequest.id).filter(
        SearchRequest.project_id == project_id
    ).all()]

    if not search_request_ids:
        return CommentListResponse(total=0, data=[])

    # Получаем все Post для этих SearchRequest
    post_ids = [p.id for p in db.query(Post.id).filter(
        Post.search_request_id.in_(search_request_ids)
    ).all()]

    if not post_ids:
        return CommentListResponse(total=0, data=[])

    # Запрос комментариев с информацией о посте
    query = db.query(Comment, Post).join(Post, Comment.post_id == Post.id).filter(
        Post.id.in_(post_ids)
    ).order_by(Comment.date.desc())

    total = query.count()
    items = query.offset(skip).limit(limit).all()

    result = []
    for comment, post in items:
        # Формируем ссылку на пост (примерная, зависит от структуры VK)
        post_link = f"https://vk.com/wall{post.vk_owner_id}_{post.vk_post_id}" if post.vk_post_id else None
        
        # Получаем имя автора из профиля
        author_name = None
        if comment.author:
            author_name = comment.author.name
        
        result.append(CommentResponse(
            id=comment.id,
            post_id=comment.post_id,
            author_vk_id=comment.author_vk_id,
            author_name=author_name,
            date=comment.date,
            text=comment.text,
            likes_count=comment.likes_count,
            emote=comment.emote,
            conf=comment.conf,
            post_link=post_link
        ))

    return CommentListResponse(total=total, data=result)


@router.get("/{project_id}/search_requests/{req_id}/comments/", response_model=CommentListResponse)
async def list_request_comments(
        project_id: int,
        req_id: int,
        skip: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=200),
        db: Session = Depends(get_db)
):
    """Получить список комментариев для постов конкретного поискового запроса с пагинацией"""
    # Проверка существования проекта
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Проект не найден")

    # Проверка существования запроса
    search_request = db.query(SearchRequest).filter(
        SearchRequest.id == req_id,
        SearchRequest.project_id == project_id
    ).first()
    
    if not search_request:
        raise HTTPException(status_code=404, detail="Поисковый запрос не найден")

    # Получаем все Post для этого SearchRequest
    post_ids = [p.id for p in db.query(Post.id).filter(
        Post.search_request_id == req_id
    ).all()]

    if not post_ids:
        return CommentListResponse(total=0, data=[])

    # Запрос комментариев с информацией о посте
    query = db.query(Comment, Post).join(Post, Comment.post_id == Post.id).filter(
        Post.id.in_(post_ids)
    ).order_by(Comment.date.desc())

    total = query.count()
    items = query.offset(skip).limit(limit).all()

    result = []
    for comment, post in items:
        # Формируем ссылку на пост (примерная, зависит от структуры VK)
        post_link = f"https://vk.com/wall{post.vk_owner_id}_{post.vk_post_id}" if post.vk_post_id else None
        
        # Получаем имя автора из профиля
        author_name = None
        if comment.author:
            author_name = comment.author.name
        
        result.append(CommentResponse(
            id=comment.id,
            post_id=comment.post_id,
            author_vk_id=comment.author_vk_id,
            author_name=author_name,
            date=comment.date,
            text=comment.text,
            likes_count=comment.likes_count,
            emote=comment.emote,
            conf=comment.conf,
            post_link=post_link
        ))

    return CommentListResponse(total=total, data=result)


@router.post("/{project_id}/crawl-comments/")
async def crawl_project_comments(
        project_id: int,
        background_tasks: BackgroundTasks,
        limit: int = Query(50, ge=1, le=500),
        db: Session = Depends(get_db)
):
    """Запустить сбор комментариев для всех постов проекта в фоновом режиме"""
    # Проверка существования проекта
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Проект не найден")
    touch_project(db, project_id)
    # Получаем все SearchRequest для этого проекта
    search_requests = db.query(SearchRequest).filter(
        SearchRequest.project_id == project_id
    ).all()

    if not search_requests:
        return {"status": "no_requests", "message": "В проекте нет поисковых запросов"}

    # Собираем все посты
    all_posts = []
    for sr in search_requests:
        posts = db.query(Post).filter(Post.search_request_id == sr.id).limit(limit).all()
        all_posts.extend(posts)

    if not all_posts:
        return {"status": "no_posts", "message": "В проекте нет постов для сбора комментариев"}

    # Запускаем сбор комментариев в фоне
    post_ids = [p.id for p in all_posts]
    background_tasks.add_task(crawl_comments_for_posts, post_ids=post_ids)

    return {
        "status": "started",
        "message": f"Запущен сбор комментариев для {len(all_posts)} постов",
        "posts_count": len(all_posts)
    }
