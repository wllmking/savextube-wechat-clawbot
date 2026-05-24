#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Minimal WeChat ClawBot client for SaveXTube.

This module talks to Tencent's iLink ClawBot JSON API directly:
getupdates -> sendmessage -> getuploadurl -> CDN upload.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import mimetypes
import os
import random
import re
import secrets
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote, urljoin

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

logger = logging.getLogger("savextube.wechat")

DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
DEFAULT_CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"
DEFAULT_BOT_TYPE = "3"
ILINK_APP_ID = "bot"
CHANNEL_VERSION = "2.4.3"
BOT_AGENT = "SaveXTubeWeixin/1.0.0"

MESSAGE_TYPE_BOT = 2
MESSAGE_STATE_FINISH = 2
ITEM_TEXT = 1
ITEM_FILE = 4
ITEM_VIDEO = 5
UPLOAD_MEDIA_VIDEO = 2
UPLOAD_MEDIA_FILE = 3
FINDER_FEED_CARD_MARKER = "__WECHAT_CHANNELS_CARD_WITHOUT_SPH__"


class ClawBotError(RuntimeError):
    pass


@dataclass
class WeChatSession:
    token: str
    base_url: str = DEFAULT_BASE_URL
    cdn_base_url: str = DEFAULT_CDN_BASE_URL
    account_id: str = ""
    user_id: str = ""


@dataclass
class WeChatInboundMessage:
    message_id: str
    from_user_id: str
    to_user_id: str
    text: str
    context_token: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None


def _client_version(version: str) -> int:
    parts = [int(p) if p.isdigit() else 0 for p in version.split(".")]
    major = parts[0] if len(parts) > 0 else 0
    minor = parts[1] if len(parts) > 1 else 0
    patch = parts[2] if len(parts) > 2 else 0
    return ((major & 0xFF) << 16) | ((minor & 0xFF) << 8) | (patch & 0xFF)


def _random_wechat_uin() -> str:
    value = str(random.SystemRandom().getrandbits(32)).encode("utf-8")
    return base64.b64encode(value).decode("ascii")


def _aes_ecb_padded_size(plaintext_size: int) -> int:
    return ((plaintext_size + 1 + 15) // 16) * 16


def _file_md5_and_size(path: Path) -> tuple[str, int]:
    digest = hashlib.md5()
    size = 0
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _encrypt_file_aes_ecb_to_temp(path: Path, aes_key: bytes) -> Path:
    cipher = AES.new(aes_key, AES.MODE_ECB)
    tmp = tempfile.NamedTemporaryFile(prefix="savextube-wechat-", suffix=".enc", delete=False)
    tmp_path = Path(tmp.name)
    pending = b""
    try:
        with tmp, path.open("rb") as src:
            for chunk in iter(lambda: src.read(1024 * 1024), b""):
                data = pending + chunk
                full_len = (len(data) // 16) * 16
                if full_len:
                    tmp.write(cipher.encrypt(data[:full_len]))
                pending = data[full_len:]
            tmp.write(cipher.encrypt(pad(pending, 16)))
        return tmp_path
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def _chunks(text: str, max_len: int = 1800) -> Iterable[str]:
    text = text or ""
    while text:
        yield text[:max_len]
        text = text[max_len:]


def _walk_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _walk_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_strings(child)


def _finder_card_summary(raw_text: str) -> str:
    object_id = _xml_field(raw_text, "objectId")
    nonce_id = _xml_field(raw_text, "objectNonceId")
    desc = _xml_field(raw_text, "desc")
    parts = [FINDER_FEED_CARD_MARKER]
    if desc:
        parts.append(f"title={desc[:80]}")
    if object_id:
        parts.append(f"object_id={object_id}")
    if nonce_id:
        parts.append(f"nonce_id={nonce_id}")
    return "\n".join(parts)


def _xml_field(text: str, name: str) -> str:
    match = re.search(rf"<{re.escape(name)}><!\[CDATA\[(.*?)\]\]></{re.escape(name)}>", text, re.DOTALL)
    if not match:
        match = re.search(rf"<{re.escape(name)}>(.*?)</{re.escape(name)}>", text, re.DOTALL)
    return match.group(1).strip() if match else ""


class ClawBotClient:
    def __init__(
        self,
        session_path: str,
        base_url: str = DEFAULT_BASE_URL,
        cdn_base_url: str = DEFAULT_CDN_BASE_URL,
        token: str = "",
        timeout: int = 30,
        long_poll_timeout: int = 40,
        bot_agent: str = BOT_AGENT,
        request_retries: int = 2,
        retry_backoff: float = 1.0,
    ):
        self.session_path = Path(session_path)
        self.base_url = base_url.rstrip("/")
        self.cdn_base_url = cdn_base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.long_poll_timeout = long_poll_timeout
        self.bot_agent = bot_agent
        self.get_updates_buf = ""
        self.request_retries = max(0, request_retries)
        self.retry_backoff = max(0.1, retry_backoff)
        self.http = requests.Session()

    @property
    def configured(self) -> bool:
        return bool(self.token)

    def load_session(self) -> Optional[WeChatSession]:
        if not self.session_path.exists():
            return None
        data = json.loads(self.session_path.read_text(encoding="utf-8"))
        session = WeChatSession(
            token=data.get("token", ""),
            base_url=data.get("base_url") or DEFAULT_BASE_URL,
            cdn_base_url=data.get("cdn_base_url") or DEFAULT_CDN_BASE_URL,
            account_id=data.get("account_id", ""),
            user_id=data.get("user_id", ""),
        )
        self.token = session.token
        self.base_url = session.base_url.rstrip("/")
        self.cdn_base_url = session.cdn_base_url.rstrip("/")
        return session

    def save_session(self, session: WeChatSession) -> None:
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "token": session.token,
            "base_url": session.base_url,
            "cdn_base_url": session.cdn_base_url,
            "account_id": session.account_id,
            "user_id": session.user_id,
        }
        self.session_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            self.session_path.chmod(0o600)
        except Exception:
            pass
        self.token = session.token
        self.base_url = session.base_url.rstrip("/")
        self.cdn_base_url = session.cdn_base_url.rstrip("/")

    def _base_info(self) -> Dict[str, Any]:
        return {
            "channel_version": CHANNEL_VERSION,
            "bot_agent": self.bot_agent,
        }

    def _headers(self, auth: bool = True) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "X-WECHAT-UIN": _random_wechat_uin(),
            "iLink-App-Id": ILINK_APP_ID,
            "iLink-App-ClientVersion": str(_client_version(CHANNEL_VERSION)),
        }
        if auth:
            if not self.token:
                raise ClawBotError("WeChat ClawBot token is missing; run login first")
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _url(self, endpoint: str, base_url: Optional[str] = None) -> str:
        base = (base_url or self.base_url).rstrip("/") + "/"
        return urljoin(base, endpoint)

    def _request(
        self,
        method: str,
        url: str,
        *,
        retry: bool = True,
        **kwargs: Any,
    ) -> requests.Response:
        attempts = self.request_retries + 1 if retry else 1
        last_exc: Optional[BaseException] = None
        for attempt in range(attempts):
            try:
                response = self.http.request(method, url, **kwargs)
                if response.status_code not in {408, 429} and response.status_code < 500:
                    return response
                if attempt >= attempts - 1:
                    return response
                logger.warning("%s %s returned %s, retrying", method.upper(), url, response.status_code)
            except requests.RequestException as exc:
                last_exc = exc
                if attempt >= attempts - 1:
                    raise
                logger.warning("%s %s failed: %s; retrying", method.upper(), url, exc)
            time.sleep(self.retry_backoff * (2 ** attempt))
        if last_exc:
            raise last_exc
        raise ClawBotError(f"{method.upper()} {url} failed without response")

    def _json_response(self, endpoint: str, response: requests.Response) -> Dict[str, Any]:
        if not response.text:
            return {}
        try:
            data = response.json()
        except ValueError as exc:
            raise ClawBotError(f"{endpoint} returned non-JSON response: {response.text[:500]}") from exc
        if not isinstance(data, dict):
            raise ClawBotError(f"{endpoint} returned unexpected JSON: {data}")

        for key in ("ret", "errcode", "error_code"):
            if key in data and data.get(key) not in (None, "", 0, "0"):
                raise ClawBotError(f"{endpoint} returned error: {data}")
        return data

    def post(
        self,
        endpoint: str,
        body: Dict[str, Any],
        *,
        auth: bool = True,
        timeout: Optional[int] = None,
        base_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        response = self._request(
            "post",
            self._url(endpoint, base_url),
            headers=self._headers(auth=auth),
            json=body,
            timeout=timeout or self.timeout,
            retry=endpoint != "ilink/bot/getupdates",
        )
        if response.status_code >= 400:
            raise ClawBotError(f"POST {endpoint} failed: {response.status_code} {response.text[:500]}")
        return self._json_response(endpoint, response)

    def get(
        self,
        endpoint: str,
        *,
        auth: bool = True,
        timeout: Optional[int] = None,
        base_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        response = self._request(
            "get",
            self._url(endpoint, base_url),
            headers=self._headers(auth=auth),
            timeout=timeout or self.timeout,
        )
        if response.status_code >= 400:
            raise ClawBotError(f"GET {endpoint} failed: {response.status_code} {response.text[:500]}")
        return self._json_response(endpoint, response)

    def login_with_qr(self, timeout_seconds: int = 480, bot_type: str = DEFAULT_BOT_TYPE) -> WeChatSession:
        qr_resp = self.post(
            f"ilink/bot/get_bot_qrcode?bot_type={quote(bot_type)}",
            {"local_token_list": []},
            auth=False,
            timeout=30,
            base_url=DEFAULT_BASE_URL,
        )
        qrcode = qr_resp.get("qrcode")
        qrcode_url = qr_resp.get("qrcode_img_content")
        if not qrcode or not qrcode_url:
            raise ClawBotError(f"failed to get QR code: {qr_resp}")

        print("\n用手机微信扫描以下二维码链接完成 ClawBot 登录：")
        print(qrcode_url)
        print("\n等待扫码确认中...\n")

        poll_base_url = DEFAULT_BASE_URL
        deadline = time.time() + timeout_seconds
        pending_verify_code: Optional[str] = None
        scanned_printed = False

        while time.time() < deadline:
            endpoint = f"ilink/bot/get_qrcode_status?qrcode={quote(qrcode)}"
            if pending_verify_code:
                endpoint += f"&verify_code={quote(pending_verify_code)}"
            status_resp = self.get(endpoint, auth=False, timeout=40, base_url=poll_base_url)
            status = status_resp.get("status")

            if status == "wait":
                time.sleep(1)
                continue
            if status == "scaned":
                if not scanned_printed:
                    print("已扫码，等待手机确认...")
                    scanned_printed = True
                pending_verify_code = None
                time.sleep(1)
                continue
            if status == "need_verifycode":
                pending_verify_code = input("输入手机微信显示的数字验证码：").strip()
                continue
            if status == "scaned_but_redirect":
                redirect_host = status_resp.get("redirect_host")
                if redirect_host:
                    poll_base_url = f"https://{redirect_host}"
                time.sleep(1)
                continue
            if status == "binded_redirect":
                raise ClawBotError("这个微信账号已经绑定过当前 ClawBot，请直接使用现有 session")
            if status == "expired":
                raise ClawBotError("二维码已过期，请重新执行登录")
            if status == "verify_code_blocked":
                raise ClawBotError("验证码多次错误，请稍后重试")
            if status == "confirmed":
                token = status_resp.get("bot_token")
                account_id = status_resp.get("ilink_bot_id", "")
                base_url = status_resp.get("baseurl") or poll_base_url
                user_id = status_resp.get("ilink_user_id", "")
                if not token:
                    raise ClawBotError(f"login confirmed but bot_token missing: {status_resp}")
                session = WeChatSession(
                    token=token,
                    base_url=base_url,
                    cdn_base_url=self.cdn_base_url,
                    account_id=account_id,
                    user_id=user_id,
                )
                self.save_session(session)
                print(f"微信 ClawBot 登录完成，session 已保存到 {self.session_path}")
                return session

            raise ClawBotError(f"unexpected login status: {status_resp}")

        raise ClawBotError("登录超时，请重新执行登录")

    def notify_start(self) -> None:
        try:
            self.post("ilink/bot/msg/notifystart", {"base_info": self._base_info()})
        except Exception as exc:
            logger.warning("notify_start failed: %s", exc)

    def notify_stop(self) -> None:
        try:
            self.post("ilink/bot/msg/notifystop", {"base_info": self._base_info()})
        except Exception as exc:
            logger.warning("notify_stop failed: %s", exc)

    def get_updates(self) -> List[Dict[str, Any]]:
        body = {
            "get_updates_buf": self.get_updates_buf,
            "base_info": self._base_info(),
        }
        resp = self.post(
            "ilink/bot/getupdates",
            body,
            timeout=self.long_poll_timeout,
        )
        if resp.get("ret") not in (None, 0):
            raise ClawBotError(f"getupdates returned error: {resp}")
        self.get_updates_buf = resp.get("get_updates_buf") or self.get_updates_buf
        return resp.get("msgs") or []

    @staticmethod
    def parse_text_message(raw: Dict[str, Any]) -> Optional[WeChatInboundMessage]:
        item_list = raw.get("item_list") or []
        text_parts: List[str] = []
        for item in item_list:
            if item.get("type") == ITEM_TEXT:
                text = (item.get("text_item") or {}).get("text")
                if text:
                    text_parts.append(text)
        raw_strings = [part for part in _walk_strings(raw) if part]
        combined_text = "\n".join(text_parts)
        if "weixin.qq.com/sph/" not in combined_text:
            sph_match = next(
                (
                    match.group(0)
                    for part in raw_strings
                    for match in re.finditer(r"https?://weixin\.qq\.com/sph/[A-Za-z0-9_-]+[^\s<>'\"，。；、)）\]]*", part)
                ),
                "",
            )
            if sph_match:
                text_parts.append(sph_match.rstrip(".,;，。；"))
        if "weixin.qq.com/sph/" not in "\n".join(text_parts):
            finder_part = next((part for part in raw_strings if "<finderFeed>" in part and "</finderFeed>" in part), "")
            if finder_part:
                text_parts.append(_finder_card_summary(finder_part))
        text = "\n".join(text_parts).strip()
        if not text:
            return None

        message_id = str(raw.get("message_id") or raw.get("client_id") or raw.get("seq") or uuid.uuid4())
        return WeChatInboundMessage(
            message_id=message_id,
            from_user_id=raw.get("from_user_id", ""),
            to_user_id=raw.get("to_user_id", ""),
            text=text,
            context_token=raw.get("context_token"),
            raw=raw,
        )

    def send_text(self, to_user_id: str, text: str, context_token: Optional[str] = None) -> str:
        last_client_id = ""
        for part in _chunks(text):
            last_client_id = f"savextube-wechat-{uuid.uuid4().hex}"
            body = {
                "msg": {
                    "from_user_id": "",
                    "to_user_id": to_user_id,
                    "client_id": last_client_id,
                    "message_type": MESSAGE_TYPE_BOT,
                    "message_state": MESSAGE_STATE_FINISH,
                    "context_token": context_token,
                    "item_list": [
                        {
                            "type": ITEM_TEXT,
                            "text_item": {"text": part},
                        }
                    ],
                },
                "base_info": self._base_info(),
            }
            self.post("ilink/bot/sendmessage", body)
        return last_client_id

    def upload_media(self, to_user_id: str, file_path: str, media_type: int = UPLOAD_MEDIA_FILE) -> Dict[str, Any]:
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            raise ClawBotError(f"file not found: {path}")

        raw_md5, raw_size = _file_md5_and_size(path)
        cipher_size = _aes_ecb_padded_size(raw_size)
        filekey = secrets.token_hex(16)
        aes_key = secrets.token_bytes(16)
        aes_hex = aes_key.hex()

        upload_resp = self.post(
            "ilink/bot/getuploadurl",
            {
                "filekey": filekey,
                "media_type": media_type,
                "to_user_id": to_user_id,
                "rawsize": raw_size,
                "rawfilemd5": raw_md5,
                "filesize": cipher_size,
                "no_need_thumb": True,
                "aeskey": aes_hex,
                "base_info": self._base_info(),
            },
        )
        upload_full_url = (upload_resp.get("upload_full_url") or "").strip()
        upload_param = upload_resp.get("upload_param")
        if upload_full_url:
            upload_url = upload_full_url
        elif upload_param:
            upload_url = (
                f"{self.cdn_base_url}/upload?"
                f"encrypted_query_param={quote(upload_param)}&filekey={quote(filekey)}"
            )
        else:
            raise ClawBotError(f"getuploadurl did not return upload URL: {upload_resp}")

        encrypted_path = _encrypt_file_aes_ecb_to_temp(path, aes_key)
        try:
            upload_result = None
            for attempt in range(self.request_retries + 1):
                try:
                    with encrypted_path.open("rb") as f:
                        upload_result = self.http.post(
                            upload_url,
                            headers={"Content-Type": "application/octet-stream"},
                            data=f,
                            timeout=max(self.timeout, 120),
                        )
                except requests.RequestException as exc:
                    if attempt >= self.request_retries:
                        raise
                    logger.warning("CDN upload failed: %s; retrying", exc)
                    time.sleep(self.retry_backoff * (2 ** attempt))
                    continue
                if upload_result.status_code == 200:
                    break
                if upload_result.status_code not in {408, 429} and upload_result.status_code < 500:
                    break
                if attempt < self.request_retries:
                    logger.warning("CDN upload returned %s, retrying", upload_result.status_code)
                    time.sleep(self.retry_backoff * (2 ** attempt))
            if upload_result is None:
                raise ClawBotError("CDN upload failed without response")
            if upload_result.status_code != 200:
                raise ClawBotError(
                    f"CDN upload failed: {upload_result.status_code} "
                    f"{upload_result.headers.get('x-error-message') or upload_result.text[:500]}"
                )
            download_param = upload_result.headers.get("x-encrypted-param")
            if not download_param:
                raise ClawBotError("CDN upload response missing x-encrypted-param")
        finally:
            try:
                encrypted_path.unlink(missing_ok=True)
            except Exception:
                pass

        return {
            "filekey": filekey,
            "download_encrypted_query_param": download_param,
            "aes_key_for_message": base64.b64encode(aes_hex.encode("utf-8")).decode("ascii"),
            "raw_size": raw_size,
            "cipher_size": cipher_size,
        }

    def upload_file(self, to_user_id: str, file_path: str) -> Dict[str, Any]:
        return self.upload_media(to_user_id, file_path, UPLOAD_MEDIA_FILE)

    def upload_video(self, to_user_id: str, file_path: str) -> Dict[str, Any]:
        return self.upload_media(to_user_id, file_path, UPLOAD_MEDIA_VIDEO)

    def send_file(
        self,
        to_user_id: str,
        file_path: str,
        text: str = "",
        context_token: Optional[str] = None,
    ) -> str:
        if text:
            self.send_text(to_user_id, text, context_token=context_token)

        path = Path(file_path)
        uploaded = self.upload_file(to_user_id, str(path))
        client_id = f"savextube-wechat-{uuid.uuid4().hex}"
        body = {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": client_id,
                "message_type": MESSAGE_TYPE_BOT,
                "message_state": MESSAGE_STATE_FINISH,
                "context_token": context_token,
                "item_list": [
                    {
                        "type": ITEM_FILE,
                        "file_item": {
                            "media": {
                                "encrypt_query_param": uploaded["download_encrypted_query_param"],
                                "aes_key": uploaded["aes_key_for_message"],
                                "encrypt_type": 1,
                            },
                            "file_name": path.name,
                            "len": str(uploaded["raw_size"]),
                        },
                    }
                ],
            },
            "base_info": self._base_info(),
        }
        self.post("ilink/bot/sendmessage", body)
        return client_id

    def send_video(
        self,
        to_user_id: str,
        file_path: str,
        text: str = "",
        context_token: Optional[str] = None,
    ) -> str:
        if text:
            self.send_text(to_user_id, text, context_token=context_token)

        uploaded = self.upload_video(to_user_id, file_path)
        client_id = f"savextube-wechat-{uuid.uuid4().hex}"
        body = {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": client_id,
                "message_type": MESSAGE_TYPE_BOT,
                "message_state": MESSAGE_STATE_FINISH,
                "context_token": context_token,
                "item_list": [
                    {
                        "type": ITEM_VIDEO,
                        "video_item": {
                            "media": {
                                "encrypt_query_param": uploaded["download_encrypted_query_param"],
                                "aes_key": uploaded["aes_key_for_message"],
                                "encrypt_type": 1,
                            },
                            "video_size": uploaded["cipher_size"],
                        },
                    }
                ],
            },
            "base_info": self._base_info(),
        }
        self.post("ilink/bot/sendmessage", body)
        return client_id

    def send_media(
        self,
        to_user_id: str,
        file_path: str,
        text: str = "",
        context_token: Optional[str] = None,
    ) -> str:
        mime_type = mimetypes.guess_type(file_path)[0] or ""
        if mime_type.startswith("video/"):
            return self.send_video(to_user_id, file_path, text, context_token)
        return self.send_file(to_user_id, file_path, text, context_token)
