FROM python:3.13.2-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -r requirements.txt uvloop
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
