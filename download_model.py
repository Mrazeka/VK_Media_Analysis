
# Скрипт для предварительной загрузки модели
# Запустите один раз перед стартом контейнера
# Модель скачивается в кэш, но НЕ загружается в RAM/VRAM

from transformers import AutoTokenizer, AutoModelForCausalLM
import os
import sys
import torch


# КОНФИГУРАЦИЯ

MODEL_NAME = os.getenv("MODEL_NAME", "Qwen/Qwen2.5-7B-Instruct")
CACHE_DIR = os.getenv("CACHE_DIR", "/app/models/cache")
HF_TOKEN = os.getenv("HF_TOKEN", None)


# ФУНКЦИЯ ПРОВЕРКИ НАЛИЧИЯ МОДЕЛИ В КЭШЕ

def is_model_already_downloaded(cache_dir: str, model_name: str) -> bool:

    # Проверяет, есть ли уже скачанная модель в кэше.
    # Возвращает True, если все ключевые файлы присутствуют.
    print("\n" + "="*60)
    print(" ПРОВЕРКА НАЛИЧИЯ МОДЕЛИ В КЭШЕ")
    print("="*60)

    # Показываем параметры проверки
    print(f" CACHE_DIR: {os.path.abspath(cache_dir)}")
    print(f" MODEL_NAME: {model_name}")
    print(f" HF_TOKEN: {'Установлен' if HF_TOKEN else 'Не установлен'}")
    print("-"*60)

    # Проверяем существование директории кэша
    if not os.path.exists(cache_dir):
        print(f" Директория кэша НЕ существует: {cache_dir}")
        return False

    print(f" Директория кэша существует: {cache_dir}")

    # Показываем содержимое директории кэша
    print("\n Содержимое CACHE_DIR:")
    try:
        cache_contents = os.listdir(cache_dir)
        if cache_contents:
            for item in cache_contents:
                item_path = os.path.join(cache_dir, item)
                if os.path.isdir(item_path):
                    print(f"   {item}/")
                else:
                    size = os.path.getsize(item_path) / 1024**2
                    print(f"   {item} ({size:.2f} MB)")
        else:
            print(f"  ⚠ Директория пустая!")
    except Exception as e:
        print(f"   Ошибка чтения директории: {e}")

    # Формируем путь к кэшу модели
    cache_path = os.path.join(cache_dir, f"models--{model_name.replace('/', '--')}")
    print(f"\n Ожидаемый путь к модели: {cache_path}")
    print(f" Путь существует: {os.path.exists(cache_path)}")
    print("-"*60)

    if not os.path.exists(cache_path):
        print(f" Директория модели НЕ найдена")

        # Ищем похожие директории (для отладки)
        print("\n Поиск похожих директорий в кэше:")
        try:
            for item in os.listdir(cache_dir):
                if "models--" in item and model_name.replace("/", "--") in item:
                    print(f"  ⚠️ Найдено похожее: {item}")
        except Exception as e:
            print(f"   Ошибка поиска: {e}")

        return False

    print(f" Директория модели найдена")

    # Проверяем наличие snapshots директории
    snapshots_dir = os.path.join(cache_path, "snapshots")
    print(f"\n Проверка snapshots директории: {snapshots_dir}")
    print(f" Существует: {os.path.exists(snapshots_dir)}")

    if not os.path.exists(snapshots_dir):
        print(f" Snapshots директория НЕ найдена")
        return False

    # Показываем доступные snapshots
    print("\n Доступные snapshots:")
    try:
        snapshots = os.listdir(snapshots_dir)
        if snapshots:
            for snapshot in snapshots:
                snapshot_path = os.path.join(snapshots_dir, snapshot)
                print(f"  📁 {snapshot}/")

                # Показываем содержимое первого snapshot
                if snapshot == snapshots[0]:
                    print(f"     Содержимое snapshot:")
                    try:
                        for item in os.listdir(snapshot_path):
                            item_path = os.path.join(snapshot_path, item)
                            if os.path.isfile(item_path):
                                size = os.path.getsize(item_path) / 1024**2
                                print(f"       📄 {item} ({size:.2f} MB)")
                            else:
                                print(f"       📁 {item}/")
                    except Exception as e:
                        print(f"        Ошибка чтения: {e}")
        else:
            print(f"  ⚠ Snapshots директория пустая!")
            return False
    except Exception as e:
        print(f"   Ошибка чтения snapshots: {e}")
        return False

    # Берём первый (последний) snapshot для проверки файлов
    latest_snapshot = os.path.join(snapshots_dir, snapshots[0])
    print(f"\n Активный snapshot: {latest_snapshot}")
    print("-"*60)

    # Проверяем наличие ключевых файлов
    required_files = [
        "config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "generation_config.json"
    ]

    print("\n Проверка ключевых файлов:")
    for req_file in required_files:
        file_path = os.path.join(latest_snapshot, req_file)
        exists = os.path.exists(file_path)
        status = "✅" if exists else "❌"

        if exists:
            size = os.path.getsize(file_path) / 1024**2
            print(f"  {status} {req_file} ({size:.2f} MB)")
        else:
            print(f"  {status} {req_file} (НЕ НАЙДЕН)")

    # Проверяем наличие весов модели
    print("\n📋 Проверка весов модели (.bin или .safetensors):")
    bin_files = []
    for f in os.listdir(latest_snapshot):
        if f.endswith('.bin') or f.endswith('.safetensors'):
            bin_files.append(f)

    if bin_files:
        total_bin_size = 0
        for bin_file in bin_files:
            file_path = os.path.join(latest_snapshot, bin_file)
            size = os.path.getsize(file_path) / 1024**3
            total_bin_size += size
            print(f"   {bin_file} ({size:.2f} GB)")
        print(f"   Общий размер весов: {total_bin_size:.2f} GB")

        # Проверяем минимальный размер (модель должна быть > 1 GB)
        if total_bin_size < 1.0:
            print(f"  ️ Весов слишком мало ({total_bin_size:.2f} GB < 1 GB)")
            return False
    else:
        print(f"   Веса модели НЕ найдены")
        return False

    print("\n" + "="*60)
    print(" ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ — МОДЕЛЬ НАЙДЕНА В КЭШЕ")
    print("="*60)
    return True


# ОСНОВНАЯ ФУНКЦИЯ

def main():
    print("="*60)
    print(" VK Analytics - Предварительная загрузка модели")
    print("="*60)
    print(f" Модель: {MODEL_NAME}")
    print(f" Путь к кэшу: {os.path.abspath(CACHE_DIR)}")
    print(f" Ожидаемый размер: ~14 GB")

    if HF_TOKEN:
        print("🔑 HF_TOKEN: Установлен")
    else:
        print("⚠️ HF_TOKEN: Не установлен")

    print("="*60)

    os.makedirs(CACHE_DIR, exist_ok=True)


    # ПРОВЕРКА НАЛИЧИЯ МОДЕЛИ ПЕРЕД СКАЧИВАНИЕМ

    print("\n🔍 [0/3] Проверка наличия модели в кэше...")

    if is_model_already_downloaded(CACHE_DIR, MODEL_NAME):
        print("\n" + "="*60)
        print(" МОДЕЛЬ УЖЕ ЗАГРУЖЕНА В КЭШ! Пропускаем скачивание.")
        print("="*60)

        # Показываем итоговый размер кэша
        cache_path = os.path.join(CACHE_DIR, f"models--{MODEL_NAME.replace('/', '--')}")
        total_size = 0
        file_count = 0

        for root, dirs, files in os.walk(cache_path):
            for file in files:
                total_size += os.path.getsize(os.path.join(root, file))
                file_count += 1

        print(f" Файлов в кэше: {file_count}")
        print(f" Общий размер: {total_size / 1024**3:.2f} GB")
        print("="*60)
        return

    print("\n Модель не найдена в кэше. Начинаем скачивание...")
    print("="*60)

    # =============================================================================
    # ШАГ 1: Загрузка токенизатора
    # =============================================================================
    print("\n [1/3] Загрузка токенизатора...")
    try:
        tokenizer = AutoTokenizer.from_pretrained(
            MODEL_NAME,
            cache_dir=CACHE_DIR,
            token=HF_TOKEN,
            trust_remote_code=True,
        )
        print(" Токенизатор загружен")
    except Exception as e:
        print(f" Ошибка токенизатора: {e}")
        sys.exit(1)

    # =============================================================================
    # ШАГ 2: Загрузка модели
    # =============================================================================
    print("\n [2/3] Скачивание весов модели...")
    try:
        model = AutoModelForCausalLM.from_pretrained(
            MODEL_NAME,
            cache_dir=CACHE_DIR,
            token=HF_TOKEN,
            trust_remote_code=True,
            low_cpu_mem_usage=True,
            device_map=None,
            torch_dtype=torch.float32,
        )
        print(" Веса модели загружены в кэш")

        del model
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    except Exception as e:
        print(f" Ошибка модели: {e}")
        sys.exit(1)

    # =============================================================================
    # ШАГ 3: Проверка кэша
    # =============================================================================
    print("\n [3/3] Проверка кэша...")

    cache_path = os.path.join(CACHE_DIR, f"models--{MODEL_NAME.replace('/', '--')}")

    if os.path.exists(cache_path):
        total_size = 0
        file_count = 0

        for root, dirs, files in os.walk(cache_path):
            for file in files:
                file_path = os.path.join(root, file)
                total_size += os.path.getsize(file_path)
                file_count += 1

        print(f" Файлов в кэше: {file_count}")
        print(f" Общий размер: {total_size / 1024**3:.2f} GB")
    else:
        print(f"⚠ Кэш не найден")

    # =============================================================================
    # ЗАВЕРШЕНИЕ
    # =============================================================================
    print("\n" + "="*60)
    print(" МОДЕЛЬ УСПЕШНО ЗАГРУЖЕНА В КЭШ!")
    print("="*60)


if __name__ == "__main__":
    main()