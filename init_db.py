from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker
from ModelsBD import Base  # Импорт вашего Base из models.py
import os
from sqlalchemy.orm import sessionmaker

# URL подключения к БД
DATABASE_URL = os.getenv("DATABASE_URL")

# Проверка наличия DATABASE_URL перед созданием engine
if not DATABASE_URL:
    raise ValueError(
        " Переменная окружения DATABASE_URL не установлена!\n"
        "Установите её, например: export DATABASE_URL='postgresql://user:pass@host:5432/dbname'"
    )

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)



def init_database():
    """Создает все таблицы, если их нет"""
    print(" Инициализация базы данных...")
    try:
        Base.metadata.create_all(bind=engine)
        print(" Таблицы созданы успешно!")
        
        # Выполняем миграцию для chat_sessions
        
        return True
    except Exception as e:
        print(f"❌ Ошибка инициализации БД: {e}")
        raise

def get_db():
    """
    Генератор сессии базы данных для использования в Depends().
    Гарантирует закрытие сессии после завершения запроса.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

if __name__ == "__main__":
    init_database()
