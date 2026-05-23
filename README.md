# SaveXTube WeChat ClawBot

微信 ClawBot 专用的视频下载机器人。用户在微信里给 ClawBot 发送视频链接，机器人下载后通过微信回传可播放的视频文件，发送成功后默认删除容器内本地文件。

这个仓库是面向 NAS 自用部署的精简版，只保留微信入口和常用中文视频平台，不包含其他聊天机器人入口。

## 支持平台

| 平台 | 支持内容 | 登录 cookies 建议 |
| --- | --- | --- |
| B站 | BV/av/短链视频 | 建议配置，清晰度和会员内容依赖登录态 |
| 抖音 | 分享链接、短链 | 建议配置，提高成功率 |
| 快手 | 分享链接、短链 | 建议配置，提高成功率 |
| 微博 | 视频微博、分享链接 | 建议配置，登录可见内容依赖登录态 |
| 头条视频 | 头条/西瓜常见链接 | 可选 |
| 小红书 | 视频笔记、图文笔记 | 建议配置，登录可见内容依赖登录态 |

## 功能特点

- 微信 ClawBot 扫码登录、轮询收消息、发送文本进度、上传并回传媒体文件。
- 下载完成后通过 ClawBot CDN 以微信视频消息发送，手机微信更容易直接播放。
- 回传前默认使用 `ffmpeg` 转为 H.264/AAC MP4，避免部分平台下载出来的格式微信不能直接播放。
- B站短链和 H5 分享链接会规整为普通 BV 视频页，减少低清派生流影响。
- B站优先选择 AVC/MP4 原始流；最终清晰度取决于 cookies、账号权限和视频源本身。
- 已发送文件默认清理，容器内不长期保留下载文件。
- Docker Compose 本机构建镜像，不需要 DockerHub 账号，也不需要推送镜像。

## 工作流程

```text
微信消息
  -> ClawBot getupdates
  -> 识别链接和平台
  -> yt-dlp / 小红书备用解析器下载
  -> ffmpeg 整理为微信可播放 MP4
  -> ClawBot CDN 上传
  -> 微信视频消息回传
  -> 成功后清理本地文件
```

## 目录结构

```text
.
├── Dockerfile
├── docker-compose.yml
├── savextube_wechat.py
├── clawbot_wechat.py
├── wechat_downloader.py
├── xiaohongshu_downloader.py
├── config_reader.py
├── requirements.txt
├── savextube.example.toml
└── DEPLOY_FNOS_WECHAT.md
```

运行时目录不进入 Git：

```text
config/
cookies/
downloads/
logs/
db/
```

## 快速开始

```bash
git clone https://github.com/kyfour/savextube-wechat-clawbot.git
cd savextube-wechat-clawbot
mkdir -p config cookies downloads logs db
cp savextube.example.toml config/savextube.toml
docker compose build
docker compose run --rm savextube-wechat login
docker compose up -d
```

首次 `login` 会输出 ClawBot 登录二维码链接。用手机微信扫码确认后，会在 `config/wechat_session.json` 保存登录态。

查看运行状态：

```bash
docker compose ps
docker logs -f savextube-wechat
```

## 配置

复制示例配置：

```bash
cp savextube.example.toml config/savextube.toml
```

常用配置：

```toml
[wechat]
allowed_user_ids = ""
progress_interval = 20
max_send_files = 20
supported_platforms = "douyin,kuaishou,weibo,toutiao,xiaohongshu,bilibili"
cleanup_after_send = true

[proxy]
# proxy_host = "http://192.168.1.2:7890"
```

`allowed_user_ids` 留空表示所有能给机器人发消息的微信用户都可以使用。需要限制时，登录后从日志或 `config/wechat_session.json` 里取用户 ID 填入。

## Cookies

Cookies 不是必需，但会影响清晰度、登录可见内容和解析成功率。文件放在宿主机 `cookies/` 目录，容器内路径为 `/app/cookies/`。

| 平台 | 文件名 |
| --- | --- |
| B站 | `bilibili_cookies.txt` |
| 抖音 | `douyin_cookies.txt` |
| 快手 | `kuaishou_cookies.txt` |
| 微博 | `weibo_cookies.txt` |
| 头条 | `toutiao_cookies.txt` |
| 小红书 | `xiaohongshu_cookies.txt` |

推荐在浏览器登录平台后导出 Netscape 格式 cookies。示例：

```bash
yt-dlp --cookies-from-browser chrome --cookies cookies/bilibili_cookies.txt --simulate --skip-download "https://www.bilibili.com"
```

也可以用浏览器扩展 `Get cookies.txt LOCALLY` 导出。

不要把 cookies、微信 session、`.env`、日志或下载文件提交到 Git。

## B站清晰度

B站未登录时经常只能拿到 360P/480P。配置有效登录 cookies 后，一般可以拿到 720P/1080P；高码率、4K、HDR 和大会员内容仍取决于账号权限和视频本身是否提供。

如果水印已经压进原视频画面，下载器无法无损去除。如果水印来自 Story/H5 分享页，本项目会先规整为普通 BV 视频页来尽量避开分享页派生流。

## 微信回传

视频会通过 ClawBot CDN 上传后以 `video_item` 发送。发送前默认转为 H.264/AAC MP4，以提高手机微信直接播放概率。

可选环境变量：

```yaml
WECHAT_FORCE_TRANSCODE_VIDEO: "true"
WECHAT_VIDEO_CRF: "20"
WECHAT_VIDEO_TRANSCODE_TIMEOUT: "1800"
```

如果文件过大，可能受 ClawBot、CDN 或微信客户端限制。发送失败时，机器人会返回本地保存路径，并保留文件便于人工处理。

## Docker 和镜像

默认使用本机 Docker 构建镜像：

```bash
docker compose build
docker compose up -d
```

`docker-compose.yml` 里的 `image: savextube-wechat:local` 只是本地镜像名，不需要 DockerHub，也不会执行 `docker push`。

默认基础镜像：

```dockerfile
ARG PYTHON_IMAGE=docker.1ms.run/library/python:3.11-slim-bookworm
```

如环境可以直接访问 Docker Hub，可覆盖为官方镜像：

```bash
docker compose build --build-arg PYTHON_IMAGE=python:3.11-slim-bookworm
```

## 飞牛 NAS

飞牛 NAS 部署步骤见 [DEPLOY_FNOS_WECHAT.md](DEPLOY_FNOS_WECHAT.md)。

建议部署目录：

```bash
/vol1/1000/docker/savextube-wechat
```

首次部署可以在 NAS 上直接克隆：

```bash
git clone https://github.com/kyfour/savextube-wechat-clawbot.git /vol1/1000/docker/savextube-wechat
cd /vol1/1000/docker/savextube-wechat
```

## 安全

- 不提交 `config/`、`cookies/`、`downloads/`、`logs/`、`db/`。
- 不提交微信 ClawBot token、平台 cookies、NAS 密码、代理账号或任何 `.env` 文件。
- 发布前执行敏感关键字扫描和 `git status --ignored`。
- GitHub 仓库只保存程序代码、示例配置和部署文档。

## 致谢

下载核心基于 [renlixing87/savextube](https://github.com/renlixing87/savextube) 改造。本仓库聚焦微信 ClawBot + NAS 部署场景。
