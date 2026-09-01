# Используем CUDA образ (нужен для LLM и Sentiment)
FROM nvidia/cuda:12.1.0-runtime-ubuntu22.04

WORKDIR /app

ARG HF_TOKEN

# ← ПЕРЕДАЁМ В ENV (чтобы download_model.py мог прочитать)
ENV HF_TOKEN=${HF_TOKEN}

# Установка Python 3.10 (стабильная для ML)
RUN apt-get update && apt-get install -y \
    python3.10 \
    python3.10-dev \
    python3-pip \
    gcc \
    postgresql-client \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install \
         -i https://pypi.tuna.tsinghua.edu.cn/simple \
        --timeout=1000 \
        --retries=10 \
        -r requirements.txt


#-i https://pypi.tuna.tsinghua.edu.cn/simple \
#RUN pip3 install --no-cache-dir -r requirements.txt

# Создаем директорию для моделей
#COPY download_model.py .

RUN mkdir -p /app/models
#RUN python3 download_model.py 2>&1 | tee /var/log/model_download.log

# Копируем весь код проекта
COPY . .


# Открываем все необходимые порты
EXPOSE 8000 8001 8550

# Скрипт запуска (оба сервиса в одном контейнере)
COPY start.sh .
# Принудительно убираем символы возврата каретки (Windows -> Unix)
RUN sed -i 's/\r$//' start.sh && chmod +x start.sh

CMD ["./start.sh"]