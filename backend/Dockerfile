# 后端镜像：依赖在构建阶段安装，启动只做迁移 + 启动服务
#
# 为什么这样改：
#   原实现用 `python:3.11-slim` 基础镜像 + compose 启动命令里 `pip install`，
#   导致**每次部署/重启都要重装全部依赖**（3 分钟以上）。
#   现在把依赖安装固化到镜像层：只要 requirements.txt 不变，该层命中缓存，
#   代码改动只重建 COPY 层 → 部署从 3 分钟降到数秒。
#
# 注意：compose 仍对 ./backend 做 bind mount（本地开发热更新用）。
#   bind mount 覆盖的是 /app（代码），不影响已装到 site-packages 的依赖，
#   所以「镜像装依赖 + 挂载代码」可以并存。

FROM python:3.11-slim

WORKDIR /app

# 国内 pip 镜像加速（避免构建时拉包超时）
ENV PIP_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple/ \
    PIP_TRUSTED_HOST=mirrors.cloud.tencent.com \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# ① 依赖层：仅 requirements.txt 变化才会重装（关键提速点）
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# ② 代码层：代码改动只重建这一层（秒级）
COPY . .

EXPOSE 8000

# 启动只做「迁移 + 起服务」，不再装依赖
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
