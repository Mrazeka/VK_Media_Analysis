# routes/posts.py

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field
from typing import Optional, List, Any
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import func, Integer

from init_db import get_db
from ModelsBD import Post, Comment, SearchRequest, Project

router = APIRouter(prefix="/api/posts", tags=["Posts Analytics"])


# ============================================================
# Pydantic Модели
# ============================================================

class SentimentStats(BaseModel):
    """Статистика тональности комментариев"""
    total_comments: int = 0
    positive_count: int = 0
    negative_count: int = 0
    neutral_count: int = 0


class PostResponse(BaseModel):
    """Модель ответа с данными поста и статистикой тональности"""
    id: int
    vk_post_id: int
    vk_owner_id: int
    author_vk_id: int
    date: datetime
    text: str
    likes_count: int
    comments_count: int
    reposts_count: int
    views_count: int
    search_request_id: int
    sentiment_stats: SentimentStats

    class Config:
        from_attributes = True


class PostsListResponse(BaseModel):
    """Ответ со списком постов и ОБЩЕЙ статистикой по всем постам"""
    total_count: int
    returned_count: int
    limit: int
    offset: int
    filters_applied: dict

    # === НОВОЕ: Агрегированная статистика по ВСЕМ постам ===
    total_sentiment_stats: SentimentStats = Field(
        default_factory=SentimentStats,
        description="Суммарная статистика тональности по всем найденным постам"
    )
    # ========================================================

    data: List[PostResponse]


class FilterDescription(BaseModel):
    type: str
    description: str
    example: Any = None


class FiltersInfoResponse(BaseModel):
    filters: dict[str, FilterDescription]
    pagination: dict
    sorting: dict


# ============================================================
# Вспомогательные функции
# ============================================================

def get_sentiment_stats_for_posts(session: Session, post_ids: List[int]) -> dict:
    """Вычисляет статистику тональности для списка постов"""
    if not post_ids:
        return {}

    query = (
        session.query(
            Comment.post_id,
            func.count(Comment.id).label('total'),
            func.sum(func.cast(Comment.emote == 'POSITIVE', Integer)).label('positive'),
            func.sum(func.cast(Comment.emote == 'NEGATIVE', Integer)).label('negative'),
            func.sum(func.cast(Comment.emote == 'NEUTRAL', Integer)).label('neutral'),
        )
        .filter(Comment.post_id.in_(post_ids))
        .group_by(Comment.post_id)
        .all()
    )

    stats_map = {}
    for row in query:
        stats_map[row.post_id] = SentimentStats(
            total_comments=row.total or 0,
            positive_count=row.positive or 0,
            negative_count=row.negative or 0,
            neutral_count=row.neutral or 0
        )

    for pid in post_ids:
        if pid not in stats_map:
            stats_map[pid] = SentimentStats()

    return stats_map


# ============================================================
# ЭНДПОИНТЫ
# ============================================================

@router.get("/search", response_model=PostsListResponse)
async def search_posts(
        # === ОБЯЗАТЕЛЬНЫЙ ФИЛЬТР ПО ПРОЕКТУ ===
        project_id: int = Query(..., ge=1, description="ID проекта (ОБЯЗАТЕЛЬНО)"),
        # =======================================

        # Фильтры: Автор
        vk_owner_id: Optional[int] = Query(None, description="ID владельца поста"),

        # Фильтры: Дата
        date_from: Optional[datetime] = Query(None, description="Начальная дата"),
        date_to: Optional[datetime] = Query(None, description="Конечная дата"),

        # Фильтры: Лайки
        likes_from: Optional[int] = Query(None, description="Мин. лайки"),
        likes_to: Optional[int] = Query(None, description="Макс. лайки"),

        # Фильтры: Комментарии
        comments_from: Optional[int] = Query(None, description="Мин. комментарии"),
        comments_to: Optional[int] = Query(None, description="Макс. комментарии"),

        # Фильтры: Просмотры
        views_from: Optional[int] = Query(None, description="Мин. просмотры"),
        views_to: Optional[int] = Query(None, description="Макс. просмотры"),

        # Фильтры: Текст
        search_text: Optional[str] = Query(None, description="Поиск по тексту"),

        # Пагинация
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),

        # Сортировка
        sort_by: str = Query("date", description="Поле сортировки"),
        sort_order: str = Query("desc", description="Порядок сортировки"),

        db: Session = Depends(get_db)
):
    try:
        # 1. Проверяем существование проекта
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise HTTPException(status_code=404, detail=f"Проект с ID {project_id} не найден")

        # 2. Формируем запрос с JOIN к search_requests для фильтрации по проекту
        query = db.query(Post).join(SearchRequest, Post.search_request_id == SearchRequest.id)

        # === ОБЯЗАТЕЛЬНЫЙ ФИЛЬТР ПО ПРОЕКТУ ===
        query = query.filter(SearchRequest.project_id == project_id)
        # =======================================

        # Применяем остальные фильтры
        if vk_owner_id is not None:
            query = query.filter(Post.vk_owner_id == vk_owner_id)
        if date_from is not None:
            query = query.filter(Post.date >= date_from)
        if date_to is not None:
            query = query.filter(Post.date <= date_to)
        if likes_from is not None:
            query = query.filter(Post.likes_count >= likes_from)
        if likes_to is not None:
            query = query.filter(Post.likes_count <= likes_to)
        if comments_from is not None:
            query = query.filter(Post.comments_count >= comments_from)
        if comments_to is not None:
            query = query.filter(Post.comments_count <= comments_to)
        if views_from is not None:
            query = query.filter(Post.views_count >= views_from)
        if views_to is not None:
            query = query.filter(Post.views_count <= views_to)
        if search_text is not None and search_text.strip():
            query = query.filter(Post.text.ilike(f"%{search_text}%"))

        # Считаем общее количество
        total_count = query.count()

        # Сортировка
        allowed_sort_fields = ["date", "likes_count", "comments_count", "views_count", "reposts_count"]
        if sort_by not in allowed_sort_fields:
            sort_by = "date"
        sort_column = getattr(Post, sort_by)
        if sort_order.lower() == "asc":
            query = query.order_by(sort_column.asc())
        else:
            query = query.order_by(sort_column.desc())

        # Пагинация
        posts = query.offset(offset).limit(limit).all()

        # === ПОЛУЧАЕМ СТАТИСТИКУ ТОНАЛЬНОСТИ ===
        if posts:
            post_ids = [p.id for p in posts]
            stats_map = get_sentiment_stats_for_posts(db, post_ids)

            # === СОЗДАЁМ PostResponse ОБЪЕКТЫ ===
            response_data = []

            # === НОВОЕ: Суммируем статистику по всем постам ===
            total_positive = 0
            total_negative = 0
            total_neutral = 0
            total_comments = 0
            # ================================================

            for post in posts:
                stats = stats_map.get(post.id, SentimentStats())

                post_resp = PostResponse(
                    id=post.id,
                    vk_post_id=post.vk_post_id,
                    vk_owner_id=post.vk_owner_id,
                    author_vk_id=post.author_vk_id,
                    date=post.date,
                    text=post.text[:500] if post.text else "",
                    likes_count=post.likes_count,
                    comments_count=post.comments_count,
                    reposts_count=post.reposts_count,
                    views_count=post.views_count,
                    search_request_id=post.search_request_id,
                    sentiment_stats=stats
                )
                response_data.append(post_resp)

                # === НОВОЕ: Накопление статистики ===
                total_positive += stats.positive_count
                total_negative += stats.negative_count
                total_neutral += stats.neutral_count
                total_comments += stats.total_comments
                # ====================================

            # === Создаём агрегированную статистику ===
            total_sentiment_stats = SentimentStats(
                total_comments=total_comments,
                positive_count=total_positive,
                negative_count=total_negative,
                neutral_count=total_neutral
            )
            # =========================================

        else:
            response_data = []
            total_sentiment_stats = SentimentStats()

        # Отчет о фильтрах
        applied_filters = {
            "project_id": project_id,
            "project_name": project.name,
            **{
                k: v for k, v in {
                    "vk_owner_id": vk_owner_id,
                    "date_from": date_from.isoformat() if date_from else None,
                    "date_to": date_to.isoformat() if date_to else None,
                    "likes_from": likes_from,
                    "likes_to": likes_to,
                    "comments_from": comments_from,
                    "comments_to": comments_to,
                    "views_from": views_from,
                    "views_to": views_to,
                    "search_text": search_text,
                    "sort_by": sort_by,
                    "sort_order": sort_order
                }.items() if v is not None
            }
        }


        return PostsListResponse(
            total_count=total_count,
            returned_count=len(response_data),
            limit=limit,
            offset=offset,
            filters_applied=applied_filters,
            total_sentiment_stats=total_sentiment_stats,  # ← НОВОЕ ПОЛЕ
            data=response_data
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка при выполнении поиска: {str(e)}")


@router.get("/filters/available", response_model=FiltersInfoResponse)
async def get_available_filters():
    """Описание фильтров"""
    return FiltersInfoResponse(
        filters={
            "project_id": FilterDescription(
                type="integer",
                description="ID проекта (ОБЯЗАТЕЛЬНЫЙ параметр)",
                example=1
            ),
            "vk_owner_id": FilterDescription(type="integer", description="ID автора", example=-123456),
            "date_from": FilterDescription(type="datetime", description="Дата от", example="2026-03-01T00:00:00"),
            "date_to": FilterDescription(type="datetime", description="Дата до", example="2026-03-13T23:59:59"),
            "likes_from": FilterDescription(type="integer", description="Лайки от", example=100),
            "likes_to": FilterDescription(type="integer", description="Лайки до", example=5000),
            "comments_from": FilterDescription(type="integer", description="Комменты от", example=10),
            "comments_to": FilterDescription(type="integer", description="Комменты до", example=200),
            "views_from": FilterDescription(type="integer", description="Просмотры от", example=1000),
            "views_to": FilterDescription(type="integer", description="Просмотры до", example=100000),
            "search_text": FilterDescription(type="string", description="Текст", example="новости"),
        },
        pagination={"limit": {"type": "integer", "default": 100}, "offset": {"type": "integer", "default": 0}},
        sorting={"sort_by": {"type": "string", "default": "date"}, "sort_order": {"type": "string", "default": "desc"}}
    )