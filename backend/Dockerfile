FROM python:3.11-slim
WORKDIR /app

# Render 上不需要国内源；本地测试可加 -i 参数
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

# Render 会自动注入 $PORT；数据库放 /tmp（免费版无持久磁盘）
ENV NOTICEFLOAT_DB=/tmp/noticefloat.db

EXPOSE 8787

CMD ["python", "main.py"]
