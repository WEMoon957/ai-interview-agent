# BI 看板镜像：只装 3 个依赖，镜像远小于业务后端
FROM python:3.11-slim

WORKDIR /app

ENV PIP_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple/ \
    PIP_TRUSTED_HOST=mirrors.cloud.tencent.com \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 代码 + 静态资源（含本地 echarts.min.js，不依赖外网 CDN）
COPY . .

EXPOSE 8500

# 单 worker：看板是低频轮询 + 内存缓存，多 worker 反而会让缓存各存一份
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8500", "--workers", "1"]
