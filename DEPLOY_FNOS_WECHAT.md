# SaveXTube 微信 ClawBot 版飞牛 NAS 部署

## 目标

这个部署只启动微信 ClawBot 入口。

用户给微信机器人发链接后，容器会下载到 `/downloads`，再通过 ClawBot 的 `getuploadurl` + CDN 上传 + `sendmessage` 以微信视频消息回传。文件成功回传后，默认会删除容器里的本地下载文件。

默认支持平台只保留：抖音、快手、微博、头条视频、小红书、B站。

## 目录

建议在飞牛 NAS 上放到：

```bash
/vol1/1000/docker/savextube-wechat
```

如果是首次部署，可以直接克隆仓库：

```bash
git clone https://github.com/kyfour/savextube-wechat-clawbot.git /vol1/1000/docker/savextube-wechat
cd /vol1/1000/docker/savextube-wechat
```

目录内需要这些子目录：

```bash
config
cookies
downloads
logs
db
```

## 配置

复制示例配置：

```bash
cp savextube.example.toml config/savextube.toml
```

按需编辑 `config/savextube.toml`：

```toml
[wechat]
allowed_user_ids = ""
progress_interval = 20
max_send_files = 20
supported_platforms = "douyin,kuaishou,weibo,toutiao,xiaohongshu,bilibili"
cleanup_after_send = true
```

`allowed_user_ids` 留空表示任何给机器人发消息的人都可用。登录成功后，`config/wechat_session.json` 中会出现 `user_id`，可以把它填入 `allowed_user_ids` 做白名单。

## 首次登录

构建镜像：

```bash
docker compose build
```

扫码登录微信 ClawBot：

```bash
docker compose run --rm savextube-wechat login
```

终端会输出二维码链接。用手机微信扫码确认后，会生成：

```bash
config/wechat_session.json
```

## 启动

```bash
docker compose up -d
```

查看日志：

```bash
docker logs -f savextube-wechat
```

这里使用的是 NAS 本机镜像 `savextube-wechat:local`，不需要 DockerHub 账号，也不需要 `docker push`。

## 使用

在微信里给 ClawBot 发抖音、快手、微博、头条视频、小红书或 B站视频链接：

```text
https://www.bilibili.com/video/BVxxxxxxxxxx
```

机器人会先回文字进度，下载完成后以微信文件形式发送本地下载文件。

视频发送前会用 `ffmpeg` 整理成 H.264/AAC MP4，尽量让手机微信直接播放。

## Cookies

如需更高清晰度或登录可见内容，把 Netscape 格式 cookies 放到 `cookies/` 目录：

```text
cookies/bilibili_cookies.txt
cookies/douyin_cookies.txt
cookies/kuaishou_cookies.txt
cookies/xiaohongshu_cookies.txt
cookies/weibo_cookies.txt
cookies/toutiao_cookies.txt
```

B站未登录时经常只能拿到 360P/480P。普通登录通常可拿 720P/1080P；高码率、4K、HDR 仍取决于大会员和视频源本身。

## 注意

微信文件回传受 ClawBot/CDN/微信端限制影响，超大文件可能上传或接收失败。失败时机器人会回传本地保存路径，并保留本地文件便于人工处理。

不要把 `config/`、`cookies/`、`downloads/`、`logs/`、`db/` 提交到 Git。
