from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, ForeignKey, UniqueConstraint, Float, BigInteger
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship, declarative_base
from datetime import datetime

Base = declarative_base()


class Project(Base):
    __tablename__ = 'projects'

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False, unique=True, index=True)  # Название проекта (например, "Linux")
    description = Column(Text, nullable=True)  # Описание проекта (опционально)
    created_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)  # Чтобы можно было архивировать проекты
    last_accessed_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    # Связь с поисковыми запросами
    search_requests = relationship("SearchRequest", back_populates="project")
    chat_sessions = relationship("ChatSession", back_populates="project")



class SearchRequest(Base):
    __tablename__ = 'search_requests'

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    project_id = Column(Integer, ForeignKey('projects.id', ondelete='CASCADE'), nullable=True, index=True)
    # Основные параметры поиска (отдельные колонки для аналитики)
    query = Column(String)  # Поисковой запрос (q)
    extended = Column(Boolean, default=False)  # Получать ли расширенную информацию
    count = Column(Integer, default=100)  # Максимальное число записей
    latitude = Column(String)  # Широта точки поиска
    longitude = Column(String)  # Долгота точки поиска
    start_time = Column(DateTime)  # Время начала поиска (из unixtime)
    end_time = Column(DateTime)  # Время конца поиска (из unixtime)
    start_id = Column(String)  # ID последней полученной записи
    # params_json хранит:
    # - offset (positive)   : Смещение выборки
    # - start_from (string) : Идентификатор для следующей страницы
    # - fields (string)     : Дополнительные поля профилей
    params_json = Column(JSONB)
    project = relationship("Project", back_populates="search_requests")
    posts = relationship("Post", back_populates="search_request", cascade="all, delete-orphan")


class Profile(Base):
    __tablename__ = 'profiles'

    vk_id = Column(BigInteger, primary_key=True)
    type = Column(String)  # 'user' или 'group'
    screen_name = Column(String)
    # Общие поля для пользователей и сообществ
    name = Column(String)  # first_name + last_name для user, name для group
    photo_50 = Column(String)  # Ссылка на аватар 50px
    verified = Column(Boolean, default=False)  # Галочка верификации
    # Поля только для пользователей
    first_name = Column(String)
    last_name = Column(String)
    sex = Column(Integer)  # 0-не указан, 1-женский, 2-мужской
    city_id = Column(Integer)  # ID города
    city_title = Column(String)  # Название города
    country_id = Column(Integer)  # ID страны
    country_title = Column(String)  # Название страны

    # Поля только для сообществ
    is_closed = Column(Integer)  # 0-открытое, 1-закрытое, 2-частное
    members_count = Column(Integer, default=0)

    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Post(Base):
    __tablename__ = 'posts'

    id = Column(Integer, primary_key=True)
    vk_post_id = Column(BigInteger)
    vk_owner_id = Column(BigInteger)
    __table_args__ = (UniqueConstraint('vk_owner_id', 'vk_post_id', name='uq_vk_post'),)

    search_request_id = Column(Integer, ForeignKey('search_requests.id'))
    author_vk_id = Column(BigInteger, ForeignKey('profiles.vk_id'))

    # Контент и метрики (отдельные колонки для быстрой аналитики)
    date = Column(DateTime, index=True)
    text = Column(Text)
    likes_count = Column(Integer, default=0)
    comments_count = Column(Integer, default=0)
    reposts_count = Column(Integer, default=0)
    views_count = Column(Integer, default=0)

    search_request = relationship("SearchRequest", back_populates="posts")
    author = relationship("Profile")
    comments = relationship("Comment", back_populates="post", cascade="all, delete-orphan")


class Comment(Base):
    __tablename__ = 'comments'

    id = Column(Integer, primary_key=True)
    vk_comment_id = Column(BigInteger)
    post_id = Column(Integer, ForeignKey('posts.id', ondelete='CASCADE'))
    author_vk_id = Column(BigInteger, ForeignKey('profiles.vk_id'))

    # Контент и метрики (отдельные колонки)
    date = Column(DateTime, index=True)
    text = Column(Text)
    likes_count = Column(Integer, default=0)
    parent_id = Column(BigInteger)  # Для тредов (веток)
    reply_to_uid = Column(BigInteger)  # ID пользователя, которому адресован ответ
    reply_to_cid = Column(BigInteger)  # ID комментария, на который дан ответ

    # raw_data хранит только технические данные:
    # - thread (object)         : Ветка комментариев (count, items)
    # - owner_id (integer)      : Владелец стены
    # - post_id (integer)       : ID поста (дубль для надежности)
    # - parents_stack (array)   : Массив ID родителей для вложенности
    raw_data = Column(JSONB)

    emote = Column(String, nullable=True, index=True)   # Например: "NEGATIVE", "POSITIVE", "NEUTRAL"
    conf = Column(Float, nullable=True, index=True)      # Уверенность модели от 0.0 до 1.0

    post = relationship("Post", back_populates="comments")
    author = relationship("Profile")

class ChatSession(Base):
    __tablename__ = 'chat_sessions'

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey('projects.id', ondelete='CASCADE'), nullable=False, index=True)
    title = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)  # Описание чата
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = Column(Boolean, default=True)
    
    # Настройки контекста
    max_context_length = Column(Integer, default=0)  # Количество последних сообщений для контекста (0 = не использовать)
    
    # Фильтрация по запросам (список ID search_request)
    request_filters = Column(JSONB, nullable=True)  # Массив ID запросов для фильтрации
    
    # Путь к файлу с инструкциями
    instructions_file_path = Column(String(500), nullable=True)
    
    project = relationship("Project", back_populates="chat_sessions")
    messages = relationship("ChatMessage", back_populates="session", cascade="all, delete-orphan", order_by="ChatMessage.created_at")


class ChatMessage(Base):
    __tablename__ = 'chat_messages'

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey('chat_sessions.id', ondelete='CASCADE'), nullable=False, index=True)
    role = Column(String(50), nullable=False)  # 'user', 'assistant', 'system'
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    applied_filters = Column(JSONB, nullable=True)  # Фильтры из Intent Parser
    sources_count = Column(Integer, default=0)  # Сколько записей из БД использовано
    
    # Дополнительные пользовательские фильтры для конкретного сообщения
    manual_filters = Column(JSONB, nullable=True)  # Ручные фильтры, заданные пользователем
    
    session = relationship("ChatSession", back_populates="messages")