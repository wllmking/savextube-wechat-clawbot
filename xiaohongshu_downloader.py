#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小红书图片下载器 - Python 版本
基于用户脚本的轻量级实现思路
"""

import re
import json
import requests
import os
import asyncio
from pathlib import Path
from urllib.parse import urlparse
import time
from typing import List, Dict, Optional

class XiaohongshuDownloader:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        })
        
    def _expand_short_url(self, url: str) -> Optional[str]:
        """展开短链接为完整URL"""
        original_url = url
        
        # 清理URL，移除末尾的无关字符
        url = url.strip().split(' ')[0]  # 移除空格后的内容
        print(f"🔧 清理后的URL: {url}")
        
        # 如果是短链接，先展开
        if 'xhslink.com' in url:
            try:
                print(f"🔄 正在展开短链接: {url}")
                
                # 设置更完整的请求头，模拟真实浏览器
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1',
                    'Sec-Fetch-Dest': 'document',
                    'Sec-Fetch-Mode': 'navigate',
                    'Sec-Fetch-Site': 'none',
                    'Cache-Control': 'max-age=0',
                    'Referer': 'https://www.xiaohongshu.com/',
                    'sec-ch-ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
                    'sec-ch-ua-mobile': '?0',
                    'sec-ch-ua-platform': '"macOS"'
                }
                
                # 尝试多次重定向
                max_redirects = 5
                current_url = url
                
                for redirect_count in range(max_redirects):
                    try:
                        print(f"🔄 第 {redirect_count + 1} 次请求: {current_url}")
                        response = self.session.get(current_url, headers=headers, allow_redirects=False, timeout=15)
                        
                        if response.status_code in [301, 302, 307, 308]:
                            # 处理重定向
                            location = response.headers.get('Location')
                            if location:
                                if location.startswith('/'):
                                    # 相对路径，需要构建完整URL
                                    parsed = urlparse(current_url)
                                    location = f"{parsed.scheme}://{parsed.netloc}{location}"
                                current_url = location
                                print(f"🔄 重定向到: {current_url}")
                                
                                # 检查是否重定向到了通用页面
                                if '/explore' in current_url or current_url == 'https://www.xiaohongshu.com':
                                    print(f"⚠️ 重定向到通用页面，可能短链接已过期")
                                    break
                                continue
                        elif response.status_code == 200:
                            # 成功获取页面
                            break
                        else:
                            print(f"⚠️ 请求状态码: {response.status_code}")
                            break
                            
                    except Exception as e:
                        print(f"⚠️ 第 {redirect_count + 1} 次请求失败: {e}")
                        break
                
                # 返回最终展开的链接
                if current_url != original_url and '/explore' not in current_url and current_url != 'https://www.xiaohongshu.com':
                    print(f"✅ 短链接展开成功: {current_url}")
                    return current_url
                else:
                    print(f"⚠️ 短链接展开失败或重定向到通用页面")
                    print(f"❌ 无法展开短链接，请检查链接是否有效")
                    return None
                    
            except Exception as e:
                print(f"❌ 短链接展开失败: {e}")
                print(f"⚠️ 将使用原始URL继续处理")
                return None
        
        # 如果不是短链接，直接返回原URL
        return url
    


    def extract_note_id(self, url: str) -> Optional[str]:
        """从URL中提取笔记ID"""
        # 先展开短链接
        expanded_url = self._expand_short_url(url)
        
        # 如果短链接展开失败，直接返回None
        if not expanded_url:
            print(f"❌ 短链接展开失败，无法提取笔记ID")
            return None
        
        # 尝试多种模式提取笔记ID
        patterns = [
            r'/explore/([^?]+)',
            r'/discovery/item/([^?]+)',
            r'noteId=([^&]+)',
            r'/item/([^?]+)',
            r'xhslink\.com/m/([^?]+)',  # 短链接模式
        ]
        
        for pattern in patterns:
            match = re.search(pattern, expanded_url)
            if match:
                note_id = match.group(1)
                print(f"✅ 提取到笔记ID: {note_id}")
                return note_id
        
        print(f"❌ 无法从URL提取笔记ID: {expanded_url}")
        return None
    
    def get_page_data(self, url: str) -> Optional[Dict]:
        """获取页面数据，提取 __INITIAL_STATE__"""
        try:
            print(f"🔍 正在获取页面数据: {url}")
            
            # 设置更完整的请求头，模拟真实浏览器
            headers = {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Cache-Control': 'max-age=0',
                'Referer': 'https://www.xiaohongshu.com/',
                'sec-ch-ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
                'sec-ch-ua-mobile': '?0',
                'sec-ch-ua-platform': '"macOS"'
            }
            
            # 直接获取页面，不重试
            response = self.session.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            
            print(f"✅ 页面获取成功: 状态码 {response.status_code}, 大小 {len(response.text)} 字符")

            # 尝试提取 __INITIAL_STATE__ 数据
            patterns = [
                r'window\.__INITIAL_STATE__\s*=\s*({.+?})</script>',
                r'__INITIAL_STATE__\s*=\s*({.+?})</script>',
            ]

            for i, pattern in enumerate(patterns):
                match = re.search(pattern, response.text, re.DOTALL)
                if match:
                    try:
                        print(f"✅ 使用模式 {i+1} 找到数据")
                        json_str = match.group(1).strip()
                        
                        # 简单的JSON清理
                        json_str = json_str.replace('undefined', 'null')
                        
                        # 直接解析
                        data = json.loads(json_str)
                        print(f"✅ JSON解析成功")
                        return data
                        
                    except json.JSONDecodeError as e:
                        print(f"⚠️ 模式 {i+1} JSON解析失败: {e}")
                        continue

            # 如果都没找到
            if '__INITIAL_STATE__' in response.text:
                print("⚠️ 页面包含 __INITIAL_STATE__ 但无法解析")
            else:
                print("❌ 页面不包含 __INITIAL_STATE__ 数据")

            return None

        except Exception as e:
            print(f"❌ 获取页面数据时发生未知错误: {e}")
            return None
    
    def _smart_fix_json(self, json_str: str) -> Optional[str]:
        """智能修复JSON语法错误"""
        try:
            print(f"🔧 开始智能修复JSON，长度: {len(json_str)}")
            
            # 方法1：尝试修复尾随逗号
            fixed = re.sub(r',(\s*[}\]])', r'\1', json_str)
            
            # 方法2：尝试修复 undefined
            fixed = fixed.replace('undefined', 'null')
            
            # 方法3：尝试修复 null,
            fixed = fixed.replace('null,', '')
            
            # 方法4：尝试修复多余的逗号
            fixed = re.sub(r',+', ',', fixed)
            
            # 方法5：尝试修复引号问题
            fixed = fixed.replace('\\u002F', '/')
            fixed = fixed.replace('\\"', '"')
            
            # 方法6：尝试修复可能的语法错误
            # 查找并修复常见的语法错误
            try:
                # 先尝试直接解析
                json.loads(fixed)
                print(f"✅ 智能修复成功")
                return fixed
            except json.JSONDecodeError as e:
                print(f"⚠️ 智能修复后仍有错误: {e}")
                
                # 尝试更激进的修复
                # 查找错误位置附近的内容
                error_pos = e.pos
                print(f"🔍 错误位置: {error_pos}")
                
                # 显示错误位置附近的内容
                start = max(0, error_pos - 100)
                end = min(len(fixed), error_pos + 100)
                print(f"🔍 错误附近内容: {fixed[start:end]}")
                
                # 尝试修复常见的语法错误
                # 1. 修复缺少的引号
                fixed = re.sub(r'([{,])\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1"\2":', fixed)
                
                # 2. 修复缺少的逗号
                fixed = re.sub(r'(["\d])\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1,\2":', fixed)
                
                # 3. 尝试解析修复后的JSON
                try:
                    json.loads(fixed)
                    print(f"✅ 激进修复成功")
                    return fixed
                except:
                    print(f"❌ 激进修复也失败了")
                    return None
                    
        except Exception as e:
            print(f"❌ 智能修复过程中发生错误: {e}")
            return None
    
    def extract_note_info(self, data: Dict, note_id: str) -> Optional[Dict]:
        """从页面数据中提取笔记信息"""
        try:
            print(f"🔍 开始提取笔记信息，笔记ID: {note_id}")
            print(f"🔍 数据顶层键: {list(data.keys())}")
            
            # 尝试不同的数据路径
            paths = [
                f"note.noteDetailMap.{note_id}.note",
                f"note.noteDetailMap.{note_id}",
                f"feed.feeds",
                f"note.noteDetailMap",
                f"feed",
            ]
            
            for path in paths:
                print(f"🔍 尝试路径: {path}")
                current = data
                for key in path.split('.'):
                    if key in current:
                        current = current[key]
                        print(f"🔍 找到键 {key}，类型: {type(current)}")
                    else:
                        print(f"⚠️ 键 {key} 不存在")
                        current = None
                        break
                
                if current:
                    print(f"🔍 路径 {path} 数据: {type(current)}")
                    
                    # 如果current是列表，尝试找到包含note_id的项
                    if isinstance(current, list):
                        print(f"🔍 找到列表数据，长度: {len(current)}")
                        if len(current) > 0:
                            print(f"🔍 第一个列表项键: {list(current[0].keys()) if isinstance(current[0], dict) else '非字典'}")
                        
                        for i, item in enumerate(current):
                            if isinstance(item, dict):
                                item_id = item.get('id') or item.get('noteId') or item.get('note_id')
                                print(f"🔍 检查列表项 {i}: id={item_id}, 类型={type(item_id)}")
                                if str(item_id) == str(note_id):
                                    print(f"✅ 在列表中找到匹配的笔记: {note_id}")
                                    # 如果找到匹配的项，尝试提取noteCard
                                    if 'noteCard' in item:
                                        print(f"🔍 找到noteCard字段")
                                        return item['noteCard']
                                    return item
                        # 如果没有找到匹配的，继续尝试下一个路径
                        print(f"⚠️ 在路径 {path} 中未找到匹配的笔记ID: {note_id}")
                        continue
                    else:
                        print(f"🔍 返回非列表数据: {type(current)}")
                        return current
            
            print(f"❌ 在所有数据路径中都未找到笔记ID: {note_id}")
            return None
            
        except Exception as e:
            print(f"提取笔记信息失败: {e}")
            return None
    
    def generate_image_urls(self, note: Dict) -> List[str]:
        """生成无水印图片链接"""
        urls = []
        
        try:
            images = note.get('imageList', [])
            
            for item in images:
                url_default = item.get('urlDefault', '')
                
                # 使用正则提取图片ID（模仿用户脚本的逻辑）
                pattern = r'http://sns-webpic-qc\.xhscdn\.com/\d+/[0-9a-z]+/(\S+)!'
                match = re.search(pattern, url_default)
                
                if match:
                    image_id = match.group(1)
                    # 构造无水印链接
                    clean_url = f"https://ci.xiaohongshu.com/{image_id}?imageView2/format/png"
                    urls.append(clean_url)
                else:
                    # 备用方案：尝试其他可能的链接格式
                    for key in ['urlDefault', 'url', 'picUrl']:
                        if key in item and item[key]:
                            urls.append(item[key])
                            break
            
            return urls
            
        except Exception as e:
            print(f"生成图片链接失败: {e}")
            return []
    
    def generate_video_url(self, note: Dict) -> List[str]:
        """生成视频链接"""
        try:
            video_key = note.get('video', {}).get('consumer', {}).get('originVideoKey')
            if video_key:
                return [f"https://sns-video-bd.xhscdn.com/{video_key}"]
            return []
        except Exception as e:
            print(f"生成视频链接失败: {e}")
            return []
    
    def download_file(self, url: str, filepath: str, retries: int = 3, progress_callback=None) -> bool:
        """下载文件，支持进度回调"""
        # 初始化进度更新时间
        if not hasattr(self, '_last_progress_update'):
            self._last_progress_update = 0
            
        for attempt in range(retries):
            try:
                print(f"正在下载: {url}")
                
                # 在开始下载前，先发送开始下载的消息
                if progress_callback:
                    filename = os.path.basename(filepath)
                    start_text = (
                        f"🚀 开始下载: `{filename}`\n"
                        f"📥 正在获取文件信息..."
                    )
                    try:
                        if asyncio.iscoroutinefunction(progress_callback):
                            try:
                                loop = asyncio.get_running_loop()
                                asyncio.create_task(progress_callback(start_text))
                            except RuntimeError:
                                print(f"警告: 无法在当前线程中调用异步进度回调，跳过开始消息")
                        else:
                            progress_callback(start_text)
                    except Exception as e:
                        print(f"开始下载消息回调失败: {e}")
                
                response = self.session.get(url, timeout=30, stream=True)
                response.raise_for_status()
                
                # 获取文件大小
                total_size = int(response.headers.get('content-length', 0))
                downloaded_size = 0
                start_time = time.time()
                
                # 确保目录存在
                os.makedirs(os.path.dirname(filepath), exist_ok=True)
                
                with open(filepath, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            downloaded_size += len(chunk)
                            
                            # 计算进度和速度
                            if total_size > 0:
                                progress = (downloaded_size / total_size) * 100
                                elapsed_time = time.time() - start_time
                                if elapsed_time > 0:
                                    speed = downloaded_size / elapsed_time / (1024 * 1024)  # MB/s
                                    
                                    # 计算预计剩余时间
                                    if speed > 0:
                                        remaining_bytes = total_size - downloaded_size
                                        eta_seconds = remaining_bytes / (speed * 1024 * 1024)
                                        mins, secs = divmod(int(eta_seconds), 60)
                                        if mins > 0:
                                            eta_str = f"{mins:02d}:{secs:02d}"
                                        else:
                                            eta_str = f"00:{secs:02d}"
                                    else:
                                        eta_str = "未知"
                                    
                                    # 创建进度文本（参考X图片下载格式）
                                    downloaded_mb = downloaded_size / (1024 * 1024)
                                    total_mb = total_size / (1024 * 1024)
                                    filename = os.path.basename(filepath)
                                    
                                    progress_text = (
                                        f"📝 文件: `{filename}`\n"
                                        f"💾 大小: `{downloaded_mb:.2f}MB / {total_mb:.2f}MB`\n"
                                        f"⚡ 速度: `{speed:.2f}MB/s`\n"
                                        f"⏳ 预计剩余: `{eta_str}`\n"
                                        f"📊 进度: {self._create_progress_bar(progress)} `{progress:.1f}%`"
                                    )
                                    
                                    # 智能进度更新策略
                                    should_update = False
                                    current_time = time.time()
                                    
                                    # 1. 强制显示关键进度点（25%, 50%, 75%, 100%）
                                    if progress >= 25 and not hasattr(self, '_shown_25'):
                                        should_update = True
                                        self._shown_25 = True
                                    elif progress >= 50 and not hasattr(self, '_shown_50'):
                                        should_update = True
                                        self._shown_50 = True
                                    elif progress >= 75 and not hasattr(self, '_shown_75'):
                                        should_update = True
                                        self._shown_75 = True
                                    elif progress >= 99 and not hasattr(self, '_shown_99'):
                                        should_update = True
                                        self._shown_99 = True
                                    
                                    # 2. 时间间隔更新（如果下载很快，减少间隔）
                                    if not should_update and hasattr(self, '_last_progress_update'):
                                        # 根据下载速度动态调整更新频率
                                        if speed > 10:  # 如果速度超过10MB/s，认为是快速下载
                                            update_interval = 0.1  # 100毫秒更新一次
                                        elif speed > 5:  # 如果速度超过5MB/s
                                            update_interval = 0.2  # 200毫秒更新一次
                                        else:
                                            update_interval = 0.5  # 500毫秒更新一次
                                        
                                        if current_time - self._last_progress_update >= update_interval:
                                            should_update = True
                                    
                                    # 3. 第一次更新
                                    elif not hasattr(self, '_last_progress_update'):
                                        should_update = True
                                    
                                    # 执行进度更新
                                    if should_update and progress_callback:
                                        try:
                                            if asyncio.iscoroutinefunction(progress_callback):
                                                # 异步回调 - 创建新任务而不是使用run_coroutine_threadsafe
                                                try:
                                                    loop = asyncio.get_running_loop()
                                                    asyncio.create_task(progress_callback(progress_text))
                                                except RuntimeError:
                                                    # 如果没有运行的事件循环，尝试同步调用
                                                    print(f"警告: 无法在当前线程中调用异步进度回调，跳过更新")
                                            else:
                                                # 同步回调
                                                progress_callback(progress_text)
                                            self._last_progress_update = current_time
                                        except Exception as e:
                                            print(f"进度回调失败: {e}")
                
                # 清理进度标记
                for attr in ['_shown_25', '_shown_50', '_shown_75', '_shown_99']:
                    if hasattr(self, attr):
                        delattr(self, attr)
                
                # 确保最后发送100%进度（如果下载很快可能跳过了最后的进度更新）
                if progress_callback and total_size > 0:
                    try:
                        final_size_mb = os.path.getsize(filepath) / (1024 * 1024)
                        filename = os.path.basename(filepath)

                        # 只发送一次最终的100%进度消息
                        final_progress_text = (
                            f"📝 文件: `{filename}`\n"
                            f"💾 大小: `{final_size_mb:.2f}MB / {final_size_mb:.2f}MB`\n"
                            f"⚡ 速度: `完成`\n"
                            f"⏳ 预计剩余: `00:00`\n"
                            f"📊 进度: {self._create_progress_bar(100)} `100.0%`"
                        )

                        print(f"🎯 发送最终进度消息: {filename}")

                        if asyncio.iscoroutinefunction(progress_callback):
                            try:
                                loop = asyncio.get_running_loop()
                                # 等待最终进度消息发送完成
                                asyncio.create_task(progress_callback(final_progress_text))
                                self._last_progress_update = 0
                            except RuntimeError:
                                print(f"警告: 无法在当前线程中调用异步进度回调，跳过最终进度消息")
                        else:
                            progress_callback(final_progress_text)
                            self._last_progress_update = 0

                    except Exception as e:
                        print(f"最终进度回调失败: {e}")

                # 增加延迟，确保所有进度消息都被处理完毕
                print(f"⏳ 等待进度消息处理完成...")
                time.sleep(1.0)  # 增加延迟到1秒，确保进度消息被处理
                
                print(f"下载成功: {filepath}")
                return True
                
            except Exception as e:
                print(f"下载失败 (尝试 {attempt + 1}/{retries}): {e}")
                if attempt < retries - 1:
                    time.sleep(2)
        
        return False
    
    def _create_progress_bar(self, percent: float, length: int = 20) -> str:
        """创建进度条"""
        filled_length = int(length * percent / 100)
        return "█" * filled_length + "░" * (length - filled_length)
    
    def clean_filename(self, filename: str) -> str:
        """清理文件名"""
        # 移除非法字符
        filename = re.sub(r'[<>:"/\\|?*]', '', filename)
        # 限制长度
        if len(filename) > 100:
            filename = filename[:100]
        return filename or "untitled"
    
    def download_note(self, url: str, download_dir: str = "./downloads", progress_callback=None) -> dict:
        """下载笔记内容，支持进度回调"""
        try:
            print(f"开始处理: {url}")
            
            # 提取笔记ID
            note_id = self.extract_note_id(url)
            if not note_id:
                print("无法提取笔记ID")
                return {"success": False, "error": "无法提取笔记ID"}
            
            # 获取页面数据（使用展开后的长链接以确保命中目标笔记）
            expanded_for_page = self._expand_short_url(url) or url
            print(f"🔗 使用URL获取页面数据: {expanded_for_page}")
            data = self.get_page_data(expanded_for_page)
            if not data:
                print("获取页面数据失败")
                return {"success": False, "error": "获取页面数据失败"}
            
            # 提取笔记信息
            note = self.extract_note_info(data, note_id)
            if not note:
                print("提取笔记信息失败")
                return {"success": False, "error": "提取笔记信息失败"}
            
            # 获取标题和作者
            title = note.get('displayTitle', note.get('title', note.get('desc', 'untitled')))
            title = self.clean_filename(title)
            author = note.get('user', {}).get('nickname', '未知作者')
            
            # 判断内容类型
            note_type = note.get('type', 'normal')
            
            print(f"🔍 提取的标题: {title}")
            print(f"🔍 提取的作者: {author}")
            print(f"🔍 笔记类型: {note_type}")
            print(f"🔍 笔记键: {list(note.keys())}")
            
            # 根据内容类型生成下载链接
            files = []
            if note_type == 'video':
                urls = self.generate_video_url(note)
                media_type = 'video'
            else:
                urls = self.generate_image_urls(note)
                media_type = 'image'
            
            # 下载目录
            safe_title = self.clean_filename(title)
            base_dir = os.path.join(download_dir, f"{note_id}_{safe_title}")
            os.makedirs(base_dir, exist_ok=True)
            
            total_size = 0
            for idx, media_url in enumerate(urls, start=1):
                ext = '.mp4' if media_type == 'video' else '.png'
                # 使用标题作为文件名，多个文件时添加序号
                if len(urls) == 1:
                    filename = f"{safe_title}{ext}"
                else:
                    filename = f"{safe_title}_{idx}{ext}"
                filepath = os.path.join(base_dir, filename)
                
                # 下载文件（带进度回调）
                success = self.download_file(media_url, filepath, progress_callback=progress_callback)
                if success:
                    file_size = os.path.getsize(filepath)
                    total_size += file_size
                    files.append({
                        'path': filepath,
                        'size': file_size,
                        'type': media_type
                    })
            
            # 等待所有进度消息处理完毕，确保汇总信息在进度消息之后显示
            if progress_callback:
                print("⏳ 等待所有进度消息处理完成...")
                time.sleep(2.0)  # 等待2秒，确保所有进度消息都被处理
            
            # 完成消息由微信入口统一发送。
            
            # 返回下载结果
            return {
                "success": True,
                "title": title,
                "author": author,
                "note_id": note_id,
                "media_type": media_type,
                "files": files,
                "total_size": total_size,
                "save_dir": base_dir
            }
            
        except Exception as e:
            print(f"下载笔记失败: {e}")
            return {"success": False, "error": str(e)}

def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description='小红书内容下载器')
    parser.add_argument('urls', nargs='*', help='要下载的小红书链接')
    parser.add_argument('-d', '--dir', default='./downloads', help='下载目录')
    parser.add_argument('-c', '--cookie', help='小红书 Cookie（可选）')

    args = parser.parse_args()

    downloader = XiaohongshuDownloader()

    # 如果提供了 Cookie，添加到请求头
    if args.cookie:
        downloader.session.headers['Cookie'] = args.cookie

    # 如果没有通过命令行提供URL，使用交互式输入
    urls = args.urls
    if not urls:
        print("请输入小红书链接（输入 'quit' 退出）:")
        while True:
            url = input("URL: ").strip()
            if url.lower() == 'quit':
                break
            if url:
                urls.append(url)

    if not urls:
        print("没有提供任何链接")
        return

    print(f"准备下载 {len(urls)} 个链接到目录: {args.dir}")

    success_count = 0
    for i, url in enumerate(urls, 1):
        try:
            print(f"\n[{i}/{len(urls)}] 处理链接: {url}")
            
            # 创建简单的进度回调函数
            def progress_callback(text):
                print(f"📱 进度: {text}")
            
            result = downloader.download_note(url, args.dir, progress_callback)
            if result.get("success"):
                success_count += 1
                print(f"✅ 下载成功: {result.get('title', '未知标题')}")
            else:
                print(f"❌ 下载失败: {result.get('error', '未知错误')}")
            
            time.sleep(2)  # 避免请求过快
        except Exception as e:
            print(f"处理链接失败 {url}: {e}")

    print(f"\n完成！成功下载 {success_count}/{len(urls)} 个链接")

if __name__ == "__main__":
    main()
