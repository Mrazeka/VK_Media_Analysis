# routes/database.py

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from sqlalchemy import text, inspect
from sqlalchemy.orm import Session
import os
import json
from init_db import get_db

# ============================================================
# Роутер
# ============================================================
router = APIRouter(prefix="/api", tags=["Database"])


# ============================================================
# Pydantic модели (для ответов)
# ============================================================

class SearchRequestResponse(BaseModel):
    id: int
    project_id: int
    project_name: str
    query: str
    created_at: datetime
    count: int
    extended: bool

    class Config:
        from_attributes = True



# ЭНДПОИНТЫ


@router.get("/tables")
async def get_all_tables(db: Session = Depends(get_db)):
    """Возвращает список всех таблиц в базе данных"""
    try:
        inspector = inspect(db.bind)
        tables = inspector.get_table_names()

        tables_info = []
        for table in tables:
            # Используем параметризованный запрос через text()
            result = db.execute(text(f"SELECT COUNT(*) FROM {table}"))
            count = result.scalar()
            tables_info.append({
                "table_name": table,
                "records_count": count
            })

        return {
            "total_tables": len(tables_info),
            "tables": tables_info
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка: {str(e)}")


@router.get("/tables/{table_name}")
async def get_table_data(
        table_name: str,
        limit: int = Query(10, ge=1, le=1000, description="Количество записей"),
        db: Session = Depends(get_db)
):
    """Возвращает первые N записей из указанной таблицы"""
    allowed_tables = ["posts", "comments", "profiles", "search_requests", "projects"]
    if table_name not in allowed_tables:
        raise HTTPException(
            status_code=400,
            detail=f"Таблица '{table_name}' не доступна. Разрешенные: {', '.join(allowed_tables)}"
        )

    try:
        # Для profiles сортируем по vk_id, для остальных по id
        order_by_column = "vk_id" if table_name == "profiles" else "id"

        result = db.execute(
            text(f"SELECT * FROM {table_name} ORDER BY {order_by_column} DESC LIMIT :limit"),
            {"limit": limit}
        )

        columns = result.keys()
        data = [dict(zip(columns, row)) for row in result.fetchall()]

        count_result = db.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
        total_count = count_result.scalar()

        return {
            "table_name": table_name,
            "total_records": total_count,
            "returned_records": len(data),
            "limit": limit,
            "data": data
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка: {str(e)}")


@router.get("/database/search-requests")
async def list_search_requests_for_project(
        project_id: int = Query(..., ge=1, description="ID проекта"),
        db: Session = Depends(get_db)
):
    """Получить список поисковых запросов для проекта (для использования в чатах)"""
    try:
        from ModelsBD import SearchRequest, Project
        
        # Проверка существования проекта
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise HTTPException(status_code=404, detail="Проект не найден")
        
        requests = db.query(SearchRequest).filter(
            SearchRequest.project_id == project_id
        ).all()
        
        result = []
        for req in requests:
            result.append({
                "id": req.id,
                "project_id": req.project_id,
                "query": req.query,
                "created_at": req.created_at.isoformat() if req.created_at else None,
                "count": req.count,
                "extended": req.extended
            })
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка: {str(e)}")






