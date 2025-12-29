#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
XServer GAME 自动登录和续期脚本 - Mailtrap 适配版
"""

import asyncio
import time
import re
import datetime
from datetime import timezone, timedelta
import os
import json
import requests
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async

# =====================================================================
#                          配置区域
# =====================================================================

IS_GITHUB_ACTIONS = os.getenv("GITHUB_ACTIONS") == "true"
USE_HEADLESS = IS_GITHUB_ACTIONS or os.getenv("USE_HEADLESS", "false").lower() == "true"
WAIT_TIMEOUT = 10000     
PAGE_LOAD_DELAY = 3      

# XServer 凭据
LOGIN_EMAIL = os.getenv("XSERVER_EMAIL")
LOGIN_PASSWORD = os.getenv("XSERVER_PASSWORD")
TARGET_URL = "https://secure.xserver.ne.jp/xapanel/login/xmgame"

# Mailtrap 凭据 (从环境变量读取)
MAILTRAP_TOKEN = os.getenv("MAILTRAP_TOKEN")
MAILTRAP_INBOX_ID = os.getenv("MAILTRAP_INBOX_ID")
MAILTRAP_ACCOUNT_ID = os.getenv("MAILTRAP_ACCOUNT_ID")

# =====================================================================
#                        XServer 自动登录类
# =====================================================================

class XServerAutoLogin:
    def __init__(self):
        self.browser = None
        self.context = None
        self.page = None
        self.headless = USE_HEADLESS
        self.email = LOGIN_EMAIL
        self.password = LOGIN_PASSWORD
        self.target_url = TARGET_URL
        self.wait_timeout = WAIT_TIMEOUT
        self.page_load_delay = PAGE_LOAD_DELAY
        self.screenshot_count = 0  
        
        # 续期状态
        self.old_expiry_time = None      
        self.new_expiry_time = None      
        self.renewal_status = "Unknown"  

    async def setup_browser(self):
        try:
            playwright = await async_playwright().start()
            browser_args = [
                '--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu',
                '--window-size=1920,1080', '--lang=ja-JP'
            ]
            self.browser = await playwright.chromium.launch(headless=self.headless, args=browser_args)
            self.context = await self.browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                locale='ja-JP', timezone_id='Asia/Tokyo',
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
            )
            self.page = await self.context.new_page()
            await stealth_async(self.page)
            print("✅ 浏览器初始化成功 (Stealth Enabled)")
            return True
        except Exception as e:
            print(f"❌ 浏览器初始化失败: {e}")
            return False

    async def take_screenshot(self, step_name=""):
        if self.page:
            self.screenshot_count += 1
            beijing_time = datetime.datetime.now(timezone(timedelta(hours=8)))
            timestamp = beijing_time.strftime("%H%M%S")
            filename = f"step_{self.screenshot_count:02d}_{timestamp}_{step_name}.png"
            await self.page.screenshot(path=filename, full_page=True)
            print(f"📸 截图: {filename}")

    async def human_type(self, selector, text):
        for char in text:
            await self.page.type(selector, char, delay=100)
            await asyncio.sleep(0.05)

    async def perform_login(self):
        try:
            print(f"🌐 访问登录页: {self.target_url}")
            await self.page.goto(self.target_url)
            await self.page.wait_for_selector("input[name='memberid']", timeout=self.wait_timeout)
            
            await self.page.fill("input[name='memberid']", "")
            await self.human_type("input[name='memberid']", self.email)
            await self.page.fill("input[name='user_password']", "")
            await self.human_type("input[name='user_password']", self.password)
            
            await asyncio.sleep(1)
            await self.page.click("input[value='ログインする']")
            print("✅ 登录表单已提交")
            return True
        except Exception as e:
            print(f"❌ 登录操作失败: {e}")
            return False

    # -----------------------------------------------------------------
    #                         Mailtrap 邮件处理
    # -----------------------------------------------------------------

    async def get_verification_code_from_cloudmail(self):
        """适配 Mailtrap API 的验证码获取函数"""
        print("📧 等待 20 秒让 Outlook 转发邮件到 Mailtrap...")
        await asyncio.sleep(20)
        
        if not all([MAILTRAP_TOKEN, MAILTRAP_ACCOUNT_ID, MAILTRAP_INBOX_ID]):
            print("❌ 错误: 缺少 Mailtrap 配置参数 (Token/AccountID/InboxID)")
            return None

        url = f"https://mailtrap.io/api/accounts/{MAILTRAP_ACCOUNT_ID}/inboxes/{MAILTRAP_INBOX_ID}/messages"
        headers = {"Api-Token": MAILTRAP_TOKEN}

        try:
            # 1. 获取列表
            resp = requests.get(url, headers=headers, timeout=15)
            messages = resp.json()
            if not messages:
                print("❌ Mailtrap 收件箱是空的")
                return None

            # 2. 找到最新包含“認証コード”的邮件
            target_msg = next((m for m in messages if "認証コード" in m.get("subject", "")), None)
            if not target_msg:
                print("❌ 未在 Mailtrap 中找到验证码邮件")
                return None

            # 3. 获取纯文本正文
            body_url = f"{url}/{target_msg['id']}/body.txt"
            body_resp = requests.get(body_url, headers=headers, timeout=15)
            content = body_resp.text
            
            # 4. 正则匹配验证码
            code_match = re.search(r'【認証コード】[\s　]+[：:]\s*(\d{4,8})', content)
            if code_match:
                code = code_match.group(1)
                print(f"🎉 成功提取验证码: {code}")
                return code
            else:
                print(f"❌ 邮件内容匹配失败。内容摘要: {content[:50]}...")
                return None

        except Exception as e:
            print(f"❌ Mailtrap API 请求失败: {e}")
            return None

    # -----------------------------------------------------------------
    #                        验证码与后续流程
    # -----------------------------------------------------------------

    async def handle_verification_page(self):
        await asyncio.sleep(5)
        current_url = self.page.url
        if "loginauth/index" in current_url:
            print("🔐 检测到二步验证，正在请求发送验证码...")
            await self.page.click("input[value*='送信']")
            await asyncio.sleep(5)
            
            code = await self.get_verification_code_from_cloudmail()
            if code:
                await self.page.fill("input[name='auth_code']", code)
                await self.page.click("input[type='submit'][value='ログイン']")
                await asyncio.sleep(8)
                return True
        return True

    async def handle_login_result(self):
        success_url = "https://secure.xserver.ne.jp/xapanel/xmgame/index"
        if success_url in self.page.url:
            print("✅ 登录成功，进入管理后台")
            await self.page.click("a:has-text('ゲーム管理')")
            await asyncio.sleep(5)
            await self.get_server_time_info()
            return True
        return False

    async def get_server_time_info(self):
        try:
            element = self.page.locator("text=/残り\\d+時間\\d+分/")
            if await element.count() > 0:
                text = await element.first.text_content()
                expiry_match = re.search(r'\((\d{4}-\d{2}-\d{2})まで\)', text)
                if expiry_match:
                    self.old_expiry_time = expiry_match.group(1)
                    print(f"📅 当前到期时间: {self.old_expiry_time}")
            
            # 尝试点击续期按钮
            await self.page.click("a:has-text('アップグレード・期限延長')")
            await asyncio.sleep(3)
            
            # 检查是否有限制
            if await self.page.get_by_text("残り契約時間が24時間を切るまで").count() > 0:
                print("ℹ️ 尚未到续期时间 (剩余 > 24小时)")
                self.renewal_status = "Unexpired"
            else:
                await self.perform_extension()
        except Exception as e:
            print(f"❌ 获取续期信息失败: {e}")

    async def perform_extension(self):
        try:
            await self.page.click("a:has-text('期限を延長する')")
            await asyncio.sleep(2)
            await self.page.click("button:has-text('確認画面に進む')")
            await asyncio.sleep(2)
            
            # 记录新时间
            new_time_el = await self.page.wait_for_selector("tr:has(th:has-text('延長後の期限')) td")
            self.new_expiry_time = (await new_time_el.text_content()).strip()
            
            await self.page.click("button:has-text('期限を延長する')")
            await asyncio.sleep(5)
            if "extend/do" in self.page.url:
                print("🎉 续期成功！")
                self.renewal_status = "Success"
        except Exception as e:
            print(f"❌ 续期操作失败: {e}")
            self.renewal_status = "Failed"

    def generate_readme(self):
        beijing_time = datetime.datetime.now(timezone(timedelta(hours=8)))
        current_time = beijing_time.strftime("%Y-%m-%d %H:%M:%S")
        content = f"**最后运行**: `{current_time}`\n\n"
        content += f"📊 续期结果: `{self.renewal_status}`\n"
        content += f"🕛 旧到期时间: `{self.old_expiry_time or 'Unknown'}`\n"
        if self.new_expiry_time:
            content += f"🕡 新到期时间: `{self.new_expiry_time}`\n"
        
        with open("README.md", "w", encoding="utf-8") as f:
            f.write(content)

    async def run(self):
        if not await self.setup_browser(): return False
        try:
            if not await self.perform_login(): return False
            await self.handle_verification_page()
            await self.handle_login_result()
            self.generate_readme()
            await self.take_screenshot("final_status")
            return True
        finally:
            await self.browser.close()

async def main():
    print("🚀 XServer Auto-Renewal Start...")
    bot = XServerAutoLogin()
    success = await bot.run()
    exit(0 if success else 1)

if __name__ == "__main__":
    asyncio.run(main())
