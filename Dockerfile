FROM python:3.12-slim

# tzdata 给系统时区（TZ=Asia/Shanghai 要读它）
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*

# 挂钟是常驻 worker（不监听端口、不备份），所以不需要 git、不需要 EXPOSE
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY 挂钟.py .

# 钟面用北京时间
ENV TZ=Asia/Shanghai

# worker：常驻运行，到点敲门
CMD ["python", "挂钟.py"]
