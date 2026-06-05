FROM python:3.11-slim

WORKDIR /app

# ติดตั้ง dependencies ก่อน copy code (cache layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# สร้าง logs directory
RUN mkdir -p logs

# Port dashboard
EXPOSE 8000

CMD ["python3", "main.py"]
