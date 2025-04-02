FROM python:3.13.2-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt uvloop

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
