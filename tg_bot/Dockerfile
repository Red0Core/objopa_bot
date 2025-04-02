FROM python:3.13.2-slim
WORKDIR /app
COPY . .

# Устанавливаем переменную окружения из docker-compose
ARG INSTAGRAM_USERNAME
ENV INSTAGRAM_USERNAME=${INSTAGRAM_USERNAME}

# Создаём папку
RUN mkdir -p /root/.config/instaloader

# Копируем файл в зависимости от имени
COPY session/session-${INSTAGRAM_USERNAME} /root/.config/instaloader/session-${INSTAGRAM_USERNAME}

RUN pip install --no-cache-dir -r requirements.txt uvloop
CMD ["python", "main.py"]
