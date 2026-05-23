# SaveXTube WeChat ClawBot runtime for:
# Douyin / Kuaishou / Weibo / Toutiao / Xiaohongshu / Bilibili.
ARG PYTHON_IMAGE=docker.1ms.run/library/python:3.11-slim-bookworm
FROM ${PYTHON_IMAGE}

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    DOWNLOAD_PATH=/downloads \
    SAVEXTUBE_WECHAT_ONLY=1 \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i \
            -e 's|http://deb.debian.org/debian-security|http://mirrors.aliyun.com/debian-security|g' \
            -e 's|http://deb.debian.org/debian|http://mirrors.aliyun.com/debian|g' \
            /etc/apt/sources.list.d/debian.sources; \
    elif [ -f /etc/apt/sources.list ]; then \
        sed -i \
            -e 's|http://deb.debian.org/debian-security|http://mirrors.aliyun.com/debian-security|g' \
            -e 's|http://deb.debian.org/debian|http://mirrors.aliyun.com/debian|g' \
            /etc/apt/sources.list; \
    fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
    ca-certificates \
    tzdata \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN python -m pip install --no-cache-dir -r requirements.txt \
    && python -m pip install --no-cache-dir -U yt-dlp

COPY . .

RUN mkdir -p /downloads /app/config /app/cookies /app/logs /app/db \
    && chmod -R 777 /downloads /app/config /app/cookies /app/logs /app/db

ENTRYPOINT ["python", "savextube_wechat.py"]
CMD ["run"]
