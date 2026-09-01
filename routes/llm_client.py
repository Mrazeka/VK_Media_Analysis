"""
Прокси-роутер для обращения к LLM-микросервису
Управление сессиями и ресурсами LLM
"""
import requests
# routes/llm_client.py
import os
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends, Query, UploadFile, File
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import func  # ← Добавить func
from init_db import get_db
from ModelsBD import ChatSession, ChatMessage, Project

# Создать роутер (вместо app!)
router = APIRouter(tags=["LLM Chat"])  # ← ДОБАВИТЬ ЭТУ СТРОКУ

LLM_SERVICE_URL = os.getenv("LLM_SERVICE_URL", "http://llm-service:8001")  # Имя сервиса из docker-compose


# ============================================================
# PYDANTIC МОДЕЛИ
# ============================================================

class ChatRequest(BaseModel):
    query: str
    project_id: int = Field(..., ge=1)
    session_id: Optional[int] = None


class ChatResponse(BaseModel):
    answer: str
    session_id: int
    sources_count: int
    applied_filters: Optional[Dict[str, Any]] = None


class SessionClearRequest(BaseModel):
    session_id: int = Field(..., ge=1, description="ID сессии для очистки")


class SessionClearResponse(BaseModel):
    status: str
    message: str
    session_id: int
    messages_deleted: int


class AllSessionsClearResponse(BaseModel):
    status: str
    message: str
    sessions_cleared: int
    messages_deleted: int


class SessionInfo(BaseModel):
    session_id: int
    project_id: int
    project_name: str
    title: str
    description: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    messages_count: int
    max_context_length: int = 0
    request_filters: Optional[List[int]] = None
    instructions_file_path: Optional[str] = None
    last_query: Optional[str] = None
    estimated_tokens: int = 0


class ChatSessionCreate(BaseModel):
    project_id: int = Field(..., ge=1)
    title: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    max_context_length: int = Field(default=0, ge=0)
    request_filters: Optional[List[int]] = None  # List of search_request IDs
    instructions_file_path: Optional[str] = None


class ChatSessionUpdate(BaseModel):
    title: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    max_context_length: Optional[int] = Field(None, ge=0)
    request_filters: Optional[List[int]] = None
    instructions_file_path: Optional[str] = None


class ChatMessageRequest(BaseModel):
    query: str
    session_id: int
    manual_filters: Optional[Dict[str, Any]] = None  # Ручные фильтры пользователя


class EnhancedChatRequest(BaseModel):
    """Запрос для расширенного чата с пакетной обработкой"""
    query: str
    session_id: int
    batch_size: Optional[int] = None  # Размер пакета (по умолчанию из env)
    manual_filters: Optional[Dict[str, Any]] = None


class EnhancedChatResponse(BaseModel):
    """Ответ расширенного чата"""
    id: int
    session_id: int
    role: str
    content: str
    created_at: datetime
    applied_filters: Optional[Dict[str, Any]] = None
    manual_filters: Optional[Dict[str, Any]] = None
    sources_count: int = 0
    batches_processed: int = 0  # Количество обработанных пакетов


class ChatMessageResponse(BaseModel):
    id: int
    session_id: int
    role: str
    content: str
    created_at: datetime
    applied_filters: Optional[Dict[str, Any]] = None
    manual_filters: Optional[Dict[str, Any]] = None
    sources_count: int = 0


class SessionsListResponse(BaseModel):
    total_sessions: int
    active_sessions: int
    total_messages: int
    estimated_total_tokens: int
    sessions: List[SessionInfo]


class LLMHealthResponse(BaseModel):
    status: str
    model_loaded: bool
    device: str
    gpu_lock_available: bool
    memory_usage: Optional[Dict[str, Any]] = None


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================

def _call_llm_service(endpoint: str, method: str = "GET", json_data: dict = None, timeout: int = 30):

    # Универсальная функция для вызова LLM-сервиса.
    #
    # Args:
    #     endpoint: Эндпоинт LLM-сервиса (например, "/chat", "/session/clear")
    #     method: HTTP метод
    #     json_data: Данные для отправки
    #     timeout: Таймаут запроса
    #
    # Returns:
    #     JSON ответ от сервиса
    #
    # Raises:
    #     HTTPException: При ошибках соединения или ответа сервиса

    url = f"{LLM_SERVICE_URL}{endpoint}"

    try:
        if method == "GET":
            response = requests.get(url, timeout=timeout)
        elif method == "POST":
            response = requests.post(url, json=json_data, timeout=timeout)
        elif method == "DELETE":
            response = requests.delete(url, json=json_data, timeout=timeout)
        else:
            raise ValueError(f"Неподдерживаемый метод: {method}")

        response.raise_for_status()
        return response.json()

    except requests.exceptions.Timeout:
        raise HTTPException(
            status_code=504,
            detail=f"Превышено время ожидания ответа от LLM сервиса ({timeout}с)"
        )
    except requests.exceptions.ConnectionError:
        raise HTTPException(
            status_code=503,
            detail="LLM сервис недоступен. Проверьте, запущен ли контейнер."
        )
    except requests.exceptions.RequestException as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка соединения с LLM сервисом: {str(e)}"
        )


def _calculate_estimated_tokens(text: str) -> int:

    # Грубая оценка количества токенов в тексте.
    # Для русского языка: ~1 токен ≈ 1.5 символа
    #
    # Args:
    #     text: Текст для оценки
    #
    # Returns:
    #     Примерное количество токенов

    if not text:
        return 0
    return int(len(text) / 1.5)


# ============================================================
# ЭНДПОИНТЫ
# ============================================================

@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):

    try:
        response = requests.post(
            f"{LLM_SERVICE_URL}/chat",
            json=request.dict(),
            timeout=120  # 2 минуты на генерацию
        )
        response.raise_for_status()
        return response.json()

    except requests.exceptions.Timeout:
        raise HTTPException(status_code=504, detail="Превышено время ожидания ответа от LLM")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="LLM сервис недоступен")
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Ошибка LLM сервиса: {str(e)}")


@router.post("/session/clear", response_model=SessionClearResponse)
async def clear_session(
    request: SessionClearRequest,
    db: Session = Depends(get_db)
):

    # Очищает историю сообщений конкретной сессии.
    #
    # Что делает:
    # 1. Находит сессию в БД
    # 2. Удаляет все сообщения этой сессии
    # 3. Сбрасывает флаг is_active
    # 4. Освобождает ресурсы в LLM-сервисе (если есть кэш)


    # 1. Проверяем существование сессии
    session = db.query(ChatSession).filter(
        ChatSession.id == request.session_id
    ).first()

    if not session:
        raise HTTPException(
            status_code=404,
            detail=f"Сессия с ID {request.session_id} не найдена"
        )

    # 2. Считаем количество сообщений перед удалением
    messages_count = db.query(ChatMessage).filter(
        ChatMessage.session_id == request.session_id
    ).count()

    # 3. Удаляем все сообщения сессии
    db.query(ChatMessage).filter(
        ChatMessage.session_id == request.session_id
    ).delete()

    # 4. Деактивируем сессию
    session.is_active = False
    session.updated_at = datetime.utcnow()

    db.commit()

    # 5. Уведомляем LLM-сервис об очистке (для очистки внутреннего кэша)
    try:
        _call_llm_service(
            endpoint="/session/clear",
            method="POST",
            json_data={"session_id": request.session_id},
            timeout=10
        )
    except HTTPException:

        pass

    return SessionClearResponse(
        status="success",
        message=f"Сессия {request.session_id} очищена",
        session_id=request.session_id,
        messages_deleted=messages_count
    )


@router.post("/session/clear/all", response_model=AllSessionsClearResponse)
async def clear_all_sessions(
    project_id: Optional[int] = Query(None, ge=1, description="ID проекта для фильтрации"),
    db: Session = Depends(get_db)
):

    # Очищает ВСЕ активные сессии (или сессии конкретного проекта).
    #
    #
    # Args:
    #     project_id: Если указан, очищает только сессии этого проекта
    #
    # Returns:
    #     Статистика очищенных сессий и сообщений

    # 1. Получаем список сессий для очистки
    sessions_query = db.query(ChatSession).filter(
        ChatSession.is_active == True
    )

    if project_id is not None:
        sessions_query = sessions_query.filter(ChatSession.project_id == project_id)

    sessions_to_clear = sessions_query.all()

    if not sessions_to_clear:
        return AllSessionsClearResponse(
            status="success",
            message="Нет активных сессий для очистки",
            sessions_cleared=0,
            messages_deleted=0
        )

    session_ids = [s.id for s in sessions_to_clear]

    # 2. Считаем общее количество сообщений
    total_messages = db.query(ChatMessage).filter(
        ChatMessage.session_id.in_(session_ids)
    ).count()

    # 3. Удаляем все сообщения
    db.query(ChatMessage).filter(
        ChatMessage.session_id.in_(session_ids)
    ).delete(synchronize_session=False)

    # 4. Деактивируем сессии
    sessions_query.update({
        "is_active": False,
        "updated_at": datetime.utcnow()
    }, synchronize_session=False)

    db.commit()

    # 5. Уведомляем LLM-сервис о массовой очистке
    try:
        _call_llm_service(
            endpoint="/session/clear/all",
            method="POST",
            json_data={"project_id": project_id},
            timeout=10
        )
    except HTTPException:
        pass

    return AllSessionsClearResponse(
        status="success",
        message=f"Очищено {len(session_ids)} сессий",
        sessions_cleared=len(session_ids),
        messages_deleted=total_messages
    )



@router.get("/sessions", response_model=SessionsListResponse)
async def list_sessions(
    project_id: Optional[int] = Query(None, ge=1, description="ID проекта для фильтрации"),
    active_only: bool = Query(True, description="Показывать только активные сессии"),
    limit: int = Query(100, ge=1, le=1000, description="Максимум сессий в ответе"),
    db: Session = Depends(get_db)
):

    # Выводит список всех сессий с информацией о потреблении ресурсов.
    #
    # Args:
    #     project_id: Фильтр по проекту
    #     active_only: Если True, показывает только активные сессии
    #     limit: Ограничение количества записей
    #
    # Returns:
    #     Список сессий с метаданными и оценкой потребления токенов

    # 1. Получаем сессии
    sessions_query = db.query(ChatSession)

    if active_only:
        sessions_query = sessions_query.filter(ChatSession.is_active == True)

    if project_id is not None:
        sessions_query = sessions_query.filter(ChatSession.project_id == project_id)

    sessions = sessions_query.order_by(
        ChatSession.updated_at.desc()
    ).limit(limit).all()

    # 2. Собираем детальную информацию по каждой сессии
    session_infos = []
    total_messages = 0
    total_tokens = 0

    for session in sessions:
        # Количество сообщений
        msg_count = db.query(ChatMessage).filter(
            ChatMessage.session_id == session.id
        ).count()

        # Последнее сообщение (для превью)
        last_msg = db.query(ChatMessage).filter(
            ChatMessage.session_id == session.id,
            ChatMessage.role == 'user'
        ).order_by(ChatMessage.created_at.desc()).first()

        # Оценка токенов (все сообщения сессии)
        all_messages = db.query(ChatMessage).filter(
            ChatMessage.session_id == session.id
        ).all()

        estimated_tokens = sum(
            _calculate_estimated_tokens(msg.content)
            for msg in all_messages
        )

        # Название проекта
        project_name = session.project.name if session.project else "Unknown"

        session_infos.append(SessionInfo(
            session_id=session.id,
            project_id=session.project_id,
            project_name=project_name,
            title=session.title or f"Сессия #{session.id}",
            description=session.description,
            is_active=session.is_active,
            created_at=session.created_at,
            updated_at=session.updated_at,
            messages_count=msg_count,
            max_context_length=session.max_context_length or 0,
            request_filters=session.request_filters,
            instructions_file_path=session.instructions_file_path,
            last_query=last_msg.content[:100] if last_msg else None,
            estimated_tokens=estimated_tokens
        ))

        total_messages += msg_count
        total_tokens += estimated_tokens

    return SessionsListResponse(
        total_sessions=len(sessions),
        active_sessions=sum(1 for s in sessions if s.is_active),
        total_messages=total_messages,
        estimated_total_tokens=total_tokens,
        sessions=session_infos
    )


@router.get("/session/{session_id}/details")
async def get_session_details(
    session_id: int,
    db: Session = Depends(get_db)
):
    """
    Получает детальную информацию о конкретной сессии.
    """
    session = db.query(ChatSession).filter(
        ChatSession.id == session_id
    ).first()

    if not session:
        raise HTTPException(
            status_code=404,
            detail=f"Сессия с ID {session_id} не найдена"
        )

    # Статистика сообщений
    messages = db.query(ChatMessage).filter(
        ChatMessage.session_id == session_id
    ).order_by(ChatMessage.created_at.desc()).all()

    total_tokens = sum(
        _calculate_estimated_tokens(msg.content)
        for msg in messages
    )

    # Группировка по ролям
    user_messages = sum(1 for m in messages if m.role == 'user')
    assistant_messages = sum(1 for m in messages if m.role == 'assistant')

    return {
        "session_id": session_id,
        "project_id": session.project_id,
        "project_name": session.project.name if session.project else "Unknown",
        "title": session.title,
        "is_active": session.is_active,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
        "statistics": {
            "total_messages": len(messages),
            "user_messages": user_messages,
            "assistant_messages": assistant_messages,
            "estimated_tokens": total_tokens,
            "avg_tokens_per_message": int(total_tokens / len(messages)) if messages else 0
        },
        "last_messages": [
            {
                "role": msg.role,
                "content": msg.content[:200],
                "created_at": msg.created_at,
                "sources_count": msg.sources_count
            }
            for msg in messages[:5]  # Последние 5 сообщений
        ]
    }


@router.get("/health", response_model=LLMHealthResponse)
async def llm_health():

    # Проверка доступности и статуса LLM-сервиса.

    try:

        response = requests.get(f"{LLM_SERVICE_URL}/health", timeout=5)
        data = response.json()
        return LLMHealthResponse(
            status=data.get("status", "unknown"),
            model_loaded=data.get("model_loaded", False),
            device=data.get("device", "unknown"),
            gpu_lock_available=data.get("gpu_lock_available", True),
        )
    except Exception:
        raise HTTPException(status_code=503, detail="LLM сервис недоступен")


@router.post("/model/reload")
async def reload_model():
    """Загружает модель обратно в память"""
    return _call_llm_service(
        endpoint="/model/reload",
        method="POST",
        timeout=600  # 10 минут на загрузку
    )


@router.get("/resources/usage")
async def get_resource_usage(
    project_id: Optional[int] = Query(None, ge=1),
    db: Session = Depends(get_db)
):
    """Получает информацию о потреблении ресурсов всеми сессиями."""
    # Общая статистика
    total_sessions = db.query(ChatSession).count()
    active_sessions = db.query(ChatSession).filter(
        ChatSession.is_active == True
    ).count()

    total_messages = db.query(ChatMessage).count()

    # Токены
    all_messages = db.query(ChatMessage).all()
    total_tokens = sum(
        _calculate_estimated_tokens(msg.content)
        for msg in all_messages
    )

    # Статистика по проектам (ИСПРАВЛЕНО: теперь func определен)
    projects_stats = db.query(
        ChatSession.project_id,
        func.count(ChatSession.id).label('sessions'),
        func.count(ChatMessage.id).label('messages')  # Исправлено sum на count для ID
    ).join(
        ChatMessage, ChatSession.id == ChatMessage.session_id
    ).group_by(
        ChatSession.project_id
    ).all()

    return {
        "sessions": {
            "total": total_sessions,
            "active": active_sessions,
            "inactive": total_sessions - active_sessions
        },
        "messages": {
            "total": total_messages,
            "avg_per_session": int(total_messages / total_sessions) if total_sessions else 0
        },
        "tokens": {
            "estimated_total": total_tokens,
            "avg_per_session": int(total_tokens / total_sessions) if total_sessions else 0
        },
        "by_project": [
            {
                "project_id": ps.project_id,
                "sessions": ps.sessions,
                "messages": ps.messages
            }
            for ps in projects_stats
        ],
        "llm_service": await llm_health()
    }

@router.get("/model/status")
async def get_model_status():
    """Проверяет состояние модели"""
    return _call_llm_service(
        endpoint="/model/status",
        method="GET",
        timeout=10
    )


@router.post("/model/unload")
async def unload_model():
    """Выгружает модель из памяти"""
    return _call_llm_service(
        endpoint="/model/unload",
        method="POST",
        timeout=30
    )



# ЭНДПОИНТЫ ДЛЯ УПРАВЛЕНИЯ ЧАТАМИ


@router.post("/sessions/create", response_model=SessionInfo)
async def create_chat_session(
    session_data: ChatSessionCreate,
    db: Session = Depends(get_db)
):
    """Создает новую сессию чата для проекта"""
    # Проверка существования проекта
    project = db.query(Project).filter(Project.id == session_data.project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Проект не найден")
    
    new_session = ChatSession(
        project_id=session_data.project_id,
        title=session_data.title,
        description=session_data.description,
        max_context_length=session_data.max_context_length,
        request_filters=session_data.request_filters,
        instructions_file_path=session_data.instructions_file_path
    )
    
    db.add(new_session)
    db.commit()
    db.refresh(new_session)
    
    return SessionInfo(
        session_id=new_session.id,
        project_id=new_session.project_id,
        project_name=project.name,
        title=new_session.title,
        description=new_session.description,
        is_active=new_session.is_active,
        created_at=new_session.created_at,
        updated_at=new_session.updated_at,
        messages_count=0,
        max_context_length=new_session.max_context_length or 0,
        request_filters=new_session.request_filters,
        instructions_file_path=new_session.instructions_file_path
    )


@router.put("/sessions/{session_id}", response_model=SessionInfo)
async def update_chat_session(
    session_id: int,
    session_data: ChatSessionUpdate,
    db: Session = Depends(get_db)
):
    """Обновляет настройки сессии чата"""
    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Сессия не найдена")
    
    # Обновление полей (только если переданы значения)
    if session_data.title is not None:
        session.title = session_data.title
    if session_data.description is not None:
        session.description = session_data.description
    if session_data.max_context_length is not None:
        session.max_context_length = session_data.max_context_length
    if session_data.request_filters is not None:
        session.request_filters = session_data.request_filters
    if session_data.instructions_file_path is not None:
        session.instructions_file_path = session_data.instructions_file_path
    
    session.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(session)
    
    project_name = session.project.name if session.project else "Unknown"
    
    return SessionInfo(
        session_id=session.id,
        project_id=session.project_id,
        project_name=project_name,
        title=session.title,
        description=session.description,
        is_active=session.is_active,
        created_at=session.created_at,
        updated_at=session.updated_at,
        messages_count=db.query(ChatMessage).filter(ChatMessage.session_id == session.id).count(),
        max_context_length=session.max_context_length or 0,
        request_filters=session.request_filters,
        instructions_file_path=session.instructions_file_path
    )


@router.delete("/sessions/{session_id}")
async def delete_chat_session(
    session_id: int,
    db: Session = Depends(get_db)
):
    """Удаляет сессию чата (деактивирует)"""
    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Сессия не найдена")
    
    session.is_active = False
    session.updated_at = datetime.utcnow()
    db.commit()
    
    return {"status": "success", "message": f"Сессия {session_id} деактивирована"}


@router.get("/sessions/{session_id}/messages", response_model=List[ChatMessageResponse])
async def get_session_messages(
    session_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db)
):
    """Получает сообщения сессии"""
    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Сессия не найдена")
    
    messages = db.query(ChatMessage).filter(
        ChatMessage.session_id == session_id
    ).order_by(ChatMessage.created_at.asc()).offset(skip).limit(limit).all()
    
    return [
        ChatMessageResponse(
            id=msg.id,
            session_id=msg.session_id,
            role=msg.role,
            content=msg.content,
            created_at=msg.created_at,
            applied_filters=msg.applied_filters,
            manual_filters=msg.manual_filters,
            sources_count=msg.sources_count
        )
        for msg in messages
    ]


@router.post("/sessions/{session_id}/message", response_model=ChatMessageResponse)
async def send_chat_message(
    session_id: int,
    message_data: ChatMessageRequest,
    db: Session = Depends(get_db)
):
    """Отправляет сообщение в чат и получает ответ от LLM"""
    # Проверка сессии
    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Сессия не найдена")
    
    if not session.is_active:
        raise HTTPException(status_code=400, detail="Сессия неактивна")
    
    # Запрос к LLM сервису (он сам сохранит сообщения в БД)
    try:
        llm_request = {
            "query": message_data.query,
            "project_id": session.project_id,
            "session_id": session_id
        }
        
        # Добавляем ручные фильтры если есть
        if message_data.manual_filters:
            llm_request["manual_filters"] = message_data.manual_filters
        
        response = requests.post(
            f"{LLM_SERVICE_URL}/chat",
            json=llm_request,
            timeout=120
        )
        response.raise_for_status()
        llm_response = response.json()
        
        return ChatMessageResponse(
            id=llm_response.get("message_id"),
            session_id=session_id,
            role='assistant',
            content=llm_response.get("answer", ""),
            created_at=datetime.now(),
            applied_filters=llm_response.get("applied_filters"),
            manual_filters=message_data.manual_filters,
            sources_count=llm_response.get("sources_count", 0)
        )
        
    except requests.exceptions.Timeout:
        raise HTTPException(status_code=504, detail="Превышено время ожидания ответа от LLM")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="LLM сервис недоступен")
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Ошибка LLM сервиса: {str(e)}")


@router.post("/sessions/{session_id}/message/enhanced", response_model=EnhancedChatResponse)
async def send_enhanced_chat_message(
    session_id: int,
    message_data: EnhancedChatRequest,
    db: Session = Depends(get_db)
):
    """
    Отправляет сообщение в чат с использованием расширенной пакетной обработки.
    
    Отличия от обычного /message:
    - Использует эндпоинт /chat/enhanced
    - Поддерживает настройку размера пакета (batch_size)
    - Возвращает количество обработанных пакетов
    """
    # Проверка сессии
    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Сессия не найдена")
    
    if not session.is_active:
        raise HTTPException(status_code=400, detail="Сессия неактивна")
    
    # Запрос к LLM сервису (используем enhanced endpoint)
    try:
        llm_request = {
            "query": message_data.query,
            "project_id": session.project_id,
            "session_id": session_id
        }
        
        # Добавляем размер пакета если указан
        if message_data.batch_size:
            llm_request["batch_size"] = message_data.batch_size
        
        # Добавляем ручные фильтры если есть
        if message_data.manual_filters:
            llm_request["manual_filters"] = message_data.manual_filters
        
        # Увеличиваем таймаут для пакетной обработки (может быть долгой)
        response = requests.post(
            f"{LLM_SERVICE_URL}/chat/enhanced",
            json=llm_request,
            timeout=1000
        )
        response.raise_for_status()
        llm_response = response.json()
        
        return EnhancedChatResponse(
            id=llm_response.get("message_id"),
            session_id=session_id,
            role='assistant',
            content=llm_response.get("answer", ""),
            created_at=datetime.now(),
            applied_filters=llm_response.get("applied_filters"),
            manual_filters=message_data.manual_filters,
            sources_count=llm_response.get("sources_count", 0),
            batches_processed=llm_response.get("batches_processed", 0)
        )
        
    except requests.exceptions.Timeout:
        raise HTTPException(status_code=504, detail="Превышено время ожидания ответа от LLM (пакетная обработка требует больше времени)")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="LLM сервис недоступен")
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Ошибка LLM сервиса: {str(e)}")


# ============================================================
# ЭНДПОИНТЫ ДЛЯ ФАЙЛОВ ИНСТРУКЦИЙ
# ============================================================

INSTRUCTIONS_DIR = os.getenv("INSTRUCTIONS_DIR", "/app/instructions")


@router.get("/instructions/list")
async def list_instruction_files():
    """Получает список доступных файлов инструкций"""
    import os
    
    instructions_path = Path(INSTRUCTIONS_DIR)
    
    if not instructions_path.exists():
        return []
    
    files = []
    for file in instructions_path.glob("*.txt"):
        files.append({
            "name": file.name,
            "path": str(file),
            "size": file.stat().st_size,
            "modified": datetime.fromtimestamp(file.stat().st_mtime).isoformat()
        })
    
    return sorted(files, key=lambda x: x["name"])


@router.post("/instructions/upload")
async def upload_instruction_file(
    file: UploadFile = File(...),
):
    """Загружает новый файл инструкций"""
    if not file.filename.endswith('.txt'):
        raise HTTPException(status_code=400, detail="Только TXT файлы поддерживаются")
    
    instructions_path = Path(INSTRUCTIONS_DIR)
    instructions_path.mkdir(parents=True, exist_ok=True)
    
    file_path = instructions_path / file.filename
    
    with open(file_path, "wb") as f:
        content = await file.read()
        f.write(content)
    
    return {
        "status": "success",
        "name": file.filename,
        "path": str(file_path)
    }
