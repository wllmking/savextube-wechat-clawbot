# SaveXTube WeChat ClawBot

微信 ClawBot 专用的视频下载机器人，改自 `renlixing87/savextube`。这个版本只保留微信入口和常用短视频平台：抖音、快手、微博、头条视频、小红书、B站。

用户在微信里给 ClawBot 发视频链接，机器人下载后通过微信回传视频文件。回传成功后，默认删除容器内的本地下载文件。

## 功能

- 微信 ClawBot 登录、收消息、发文本、上传并回传视频。
- 支持抖音、快手、微博、头条视频、小红书、B站。
- B站链接会规整为普通 BV 视频页，避免 Story/H5 分享参数影响下载源。
- B站优先选择 AVC/MP4 原始流，具体清晰度取决于登录 cookies、账号权限和视频源。
- 回传前会用 `ffmpeg` 整理为微信更稳的 H.264/AAC MP4，尽量让手机微信直接播放。
- 下载成功并完成微信回传后自动清理本地文件。
- Docker 部署，适合飞牛 NAS、群晖、普通 Linux 主机。

## 目录结构

```text
.
├── Dockerfile
├── docker-compose.yml
├── savextube_wechat.py
├── clawbot_wechat.py
├── wechat_downloader.py
├── config_reader.py
├── xiaohongshu_downloader.py
├── requirements.txt
├── savextube.example.toml
└── DEPLOY_FNOS_WECHAT.md
```

运行时目录不会进 Git：

```text
config/
cookies/
downloads/
logs/
db/
```

## 快速部署

```bash
mkdir -p config cookies downloads logs db
cp savextube.example.toml config/savextube.toml
docker compose build
docker compose run --rm savextube-wechat login
docker compose up -d
```

首次 `login` 会输出 ClawBot 登录二维码链接。用微信扫码确认后，会在 `config/wechat_session.json` 保存登录态。

查看日志：

```bash
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

`allowed_user_ids` 留空表示所有能给机器人发消息的微信用户都可以使用。需要限制时，登录后从 `config/wechat_session.json` 或日志中取用户 ID 填入。

## Cookies

Cookies 不是必需，但会影响部分平台的清晰度和成功率。文件放在 `cookies/` 目录，容器内路径为 `/app/cookies/`。

| 平台 | 文件名 | 用途 |
| --- | --- | --- |
| B站 | `bilibili_cookies.txt` | 登录态、1080P/高码率/会员内容 |
| 抖音 | `douyin_cookies.txt` | 提高解析稳定性 |
| 快手 | `kuaishou_cookies.txt` | 提高解析稳定性 |
| 小红书 | `xiaohongshu_cookies.txt` | 登录可见内容、提高解析稳定性 |
| 微博 | `weibo_cookies.txt` | 登录可见内容、提高解析稳定性 |
| 头条 | `toutiao_cookies.txt` | 提高解析稳定性 |

推荐使用浏览器登录平台后导出 Netscape 格式 cookies。例如：

```bash
yt-dlp --cookies-from-browser chrome --cookies cookies/bilibili_cookies.txt --simulate --skip-download "https://www.bilibili.com"
```

也可以用浏览器扩展 `Get cookies.txt LOCALLY` 导出。不要把 cookies、session、`.env`、日志或下载文件提交到 Git。

## B站清晰度说明

B站未登录时经常只能拿到 360P/480P。登录后通常可以拿到 720P/1080P；大会员内容、高码率、4K、HDR 仍取决于账号权限和视频本身是否提供。

如果水印已经压进原视频画面，下载器不能去除。如果水印来自 Story/H5 分享页，本项目会先规整为普通 BV 视频页来避开分享页派生流。

## 微信回传说明

视频会通过 ClawBot CDN 上传后以 `video_item` 发送。发送前默认转为 H.264/AAC MP4，以提高手机微信直接播放概率。

可选环境变量：

```yaml
WECHAT_FORCE_TRANSCODE_VIDEO: "true"
WECHAT_VIDEO_CRF: "20"
WECHAT_VIDEO_TRANSCODE_TIMEOUT: "1800"
```

如果文件过大，可能受 ClawBot、CDN 或微信客户端限制。发送失败时，机器人会保留本地文件并返回路径。

## Docker

本项目默认本机构建镜像，不需要 DockerHub，也不会推送镜像仓库：

```bash
docker compose build
docker compose up -d
```

`docker-compose.yml` 里的 `image: savextube-wechat:local` 只是本机镜像名。

## 飞牛 NAS

飞牛 NAS 详细部署步骤见 [DEPLOY_FNOS_WECHAT.md](DEPLOY_FNOS_WECHAT.md)。

默认镜像使用：

```dockerfile
ARG PYTHON_IMAGE=docker.1ms.run/library/python:3.11-slim-bookworm
```

如你的环境能直接访问 Docker Hub，可以构建时覆盖：

```bash
docker compose build --build-arg PYTHON_IMAGE=python:3.11-slim-bookworm
```

## 安全

- 不要提交 `config/`、`cookies/`、`downloads/`、`logs/`、`db/`。
- 不要把微信 ClawBot token、B站/微博等平台 cookies、NAS 密码写进仓库。
- 发布前执行 `git status --ignored` 和关键字扫描，确认没有敏感文件。

## 致谢

核心下载逻辑基于 [renlixing87/savextube](https://github.com/renlixing87/savextube) 改造。本仓库聚焦微信 ClawBot + NAS 部署场景。
