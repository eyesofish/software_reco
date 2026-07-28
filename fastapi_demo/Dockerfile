FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
COPY software_recommend_system/requirements.txt software_recommend_system/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
