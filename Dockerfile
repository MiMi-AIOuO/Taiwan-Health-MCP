FROM python:3.10-slim

# 1. 設定基礎工作目錄為 /app
WORKDIR /app

# 2. 安裝依賴
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 3. 複製執行所需程式碼（Cloud Run 不會有 docker-compose volume mount）
COPY src /app/src

# 4. 可選：若 repo 內有 data，順便複製；沒有也不影響 build
COPY data /app/data

# 5. 切換到程式目錄
WORKDIR /app/src

# 6. 設定環境變數
ENV PYTHONUNBUFFERED=1

# 7. 啟動服務
ENTRYPOINT ["python", "server.py"]