import asyncio
import json
import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

from clawbot_wechat import ClawBotClient, ClawBotError
from douyin_note_downloader import DouyinNoteDownloader, is_douyin_aweme_url, is_douyin_note_url
from savextube_wechat import _bot_profiles, _collect_result_files, _parse_bool, _resolve_bot_profile

sys.modules.setdefault("yt_dlp", types.SimpleNamespace(YoutubeDL=object))

from wechat_downloader import WeChatVideoDownloader
from xiaohongshu_downloader import XiaohongshuDownloader


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self):
        return self.payload


class CoreTests(unittest.TestCase):
    def test_platform_detection_and_url_extract(self):
        with tempfile.TemporaryDirectory() as tmp:
            downloader = WeChatVideoDownloader(str(Path(tmp) / "downloads"), str(Path(tmp) / "cookies"))
            urls = downloader.extract_urls_from_text("看看这个 https://b23.tv/abc123 。")

        self.assertEqual(urls, ["https://b23.tv/abc123"])
        self.assertEqual(downloader.get_platform_name(urls[0]), "bilibili")
        self.assertEqual(downloader.get_platform_name("https://www.xiaohongshu.com/explore/abc"), "xiaohongshu")
        self.assertEqual(
            downloader.normalize_url("https://www.douyin.com/note/7642929016320557382?previous_page=app_code_link", "douyin"),
            "https://www.douyin.com/note/7642929016320557382?previous_page=app_code_link",
        )
        self.assertTrue(is_douyin_note_url("https://www.douyin.com/note/7642929016320557382"))
        self.assertTrue(is_douyin_aweme_url("https://www.douyin.com/video/7642580361180200934"))

    def test_collect_result_files_fallback_filters_old_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            platform_dir = root / "bilibili"
            platform_dir.mkdir()
            old_file = platform_dir / "old.mp4"
            old_file.write_bytes(b"old")
            os.utime(old_file, (time.time() - 60, time.time() - 60))

            start_time = time.time()
            new_file = platform_dir / "new.mp4"
            new_file.write_bytes(b"new")

            files = _collect_result_files({"download_path": str(platform_dir)}, start_time, root)

        self.assertEqual(files, [new_file])

    def test_parse_bool_handles_false_values(self):
        self.assertFalse(_parse_bool(False, default=True))
        self.assertFalse(_parse_bool("false", default=True))
        self.assertTrue(_parse_bool("", default=True))

    def test_multi_clawbot_profiles_do_not_share_session_file(self):
        config = {
            "wechat": {
                "session_file": "/app/config/wechat_session.json",
                "progress_interval": 5,
                "bots": [
                    {"name": "me", "session_file": "/app/config/wechat_me.json"},
                    {"name": "wife", "enabled": False},
                ],
            }
        }

        active_profiles = _bot_profiles(config)
        all_profiles = _bot_profiles(config, include_disabled=True)

        self.assertEqual([profile["name"] for profile in active_profiles], ["me"])
        self.assertEqual(all_profiles[0]["session_file"], "/app/config/wechat_me.json")
        self.assertEqual(all_profiles[1]["session_file"], "/app/config/wechat_wife.json")
        self.assertEqual(all_profiles[1]["progress_interval"], 5)
        self.assertEqual(_resolve_bot_profile(config, "wife")["name"], "wife")

    def test_clawbot_json_response_rejects_business_error(self):
        client = ClawBotClient(session_path="/tmp/unused.json", token="token")

        with self.assertRaises(ClawBotError):
            client._json_response("ilink/bot/sendmessage", FakeResponse({"ret": 1, "errmsg": "bad"}))

        self.assertEqual(client._json_response("ilink/bot/sendmessage", FakeResponse({"ret": 0})), {"ret": 0})

    def test_xiaohongshu_extract_note_info_from_detail_map(self):
        downloader = XiaohongshuDownloader()
        note = downloader.extract_note_info(
            {
                "note": {
                    "noteDetailMap": {
                        "abc": {
                            "note": {
                                "id": "abc",
                                "displayTitle": "title",
                                "type": "video",
                            }
                        }
                    }
                }
            },
            "abc",
        )

        self.assertEqual(note["displayTitle"], "title")
        self.assertEqual(downloader.generate_video_url({"video": {"consumer": {"originVideoKey": "key.mp4"}}}), ["https://sns-video-bd.xhscdn.com/key.mp4"])

    def test_douyin_mobile_router_data_and_candidates(self):
        downloader = DouyinNoteDownloader()
        html = """
        <script>window._ROUTER_DATA = {"loaderData":{"video_(id)/page":{"videoInfoRes":{"item_list":[{
          "aweme_id":"123",
          "desc":"note title",
          "images":[{"url_list":["https://img/a.webp","https://img/a.jpeg"],"download_url_list":["https://img/watermark.jpeg"]}],
          "video":{"play_addr":{"uri":"https://cdn/audio.m4a","url_list":["https://aweme.snssdk.com/aweme/v1/playwm/?video_id=abc&ratio=720p"]}},
          "music":{"play_url":{"url_list":["https://cdn/music.mp3"]}}
        }]}}}}</script>
        """

        item = downloader.parse_router_data(html, "123")

        self.assertEqual(item["desc"], "note title")
        self.assertEqual(downloader._image_candidates(item["images"][0])[:2], ["https://img/a.jpeg", "https://img/a.webp"])
        self.assertEqual(
            downloader._media_candidates(item)[:3],
            [
                "https://cdn/audio.m4a",
                "https://aweme.snssdk.com/aweme/v1/play/?video_id=abc&ratio=1080p",
                "https://aweme.snssdk.com/aweme/v1/play/?video_id=abc&ratio=720p",
            ],
        )

    def test_douyin_aweme_uses_direct_downloader(self):
        with tempfile.TemporaryDirectory() as tmp:
            downloader = WeChatVideoDownloader(str(Path(tmp) / "downloads"), str(Path(tmp) / "cookies"))

            async def fake_douyin(url, progress_callback):
                return {"success": True, "url": url, "files": []}

            def fail_ytdlp(*_args, **_kwargs):
                raise AssertionError("yt-dlp should not handle Douyin note URLs")

            downloader._try_douyin_direct = fake_douyin
            downloader._download_with_ytdlp = fail_ytdlp
            note_result = asyncio.run(downloader.download_video("https://www.douyin.com/note/123"))
            video_result = asyncio.run(downloader.download_video("https://www.douyin.com/video/456"))

        self.assertTrue(note_result["success"])
        self.assertTrue(video_result["success"])
        self.assertEqual(note_result["url"], "https://www.douyin.com/note/123")
        self.assertEqual(video_result["url"], "https://www.douyin.com/video/456")


if __name__ == "__main__":
    unittest.main()
