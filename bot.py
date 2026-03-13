"""
ClosingBell 마감일지 텔레그램 봇
예니(Money Plus 앵커) 전용
"""

import os
import json
import logging
import base64
import httpx
from datetime import datetime, timezone, timedelta
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

# ── 세션 저장소 (메모리) ──────────────────────────────────────────────
# { chat_id: { "date": "3/13", "items": [ {"type": ..., "content": ...} ] } }
sessions: dict[int, dict] = {}


def today_label() -> str:
    now = datetime.now(KST)
    return f"{now.month}/{now.day}"


def get_session(chat_id: int) -> dict:
    if chat_id not in sessions:
        sessions[chat_id] = {"date": today_label(), "items": []}
    return sessions[chat_id]


# ── Claude API 호출 ───────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
CLAUDE_MODEL = "claude-sonnet-4-20250514"

SYSTEM_PROMPT = """당신은 예니(이예은, Money Plus 앵커)의 국내 증시 마감일지 작성 전담 AI입니다.

## 역할
사용자가 하루 동안 축적한 시황 자료들을 받아 하나의 완성된 마감일지로 정리합니다.

## 출력 형식 (반드시 이 형식 그대로)
✓ {date} 마감일지
#ClosingBell

원달러: 0,000원 (▲/▼ 00원)

KOSPI 0,000.00 (+0.00%) / KOSDAQ 0,000.00 (+0.00%)

📌 지수 팩터
☑️긍정
- 항목

☑️부정
- 항목

📌 수급
기관: ▲/▼ 000억
외국인: ▲/▼ 000억
개인: ▲/▼ 000억

📌 환율
- 환율 관련 주요 팩터

📌 특징 업종
- 업종명: 배경/이유 한 줄

📌 내일 일정
- 항목
=====

## 자료 처리 규칙

### [장 중 시황] 대신전략 자료
- type이 "daeshin"으로 표시된 자료
- **수급 수치, 환율 수치, 개별 종목 주가 수치 절대 사용 금지** (장중 데이터라 부정확)
- **지수 팩터 배경 설명, 특징 업종/테마 흐름만 추출해서 사용**

### 사용자 직접 입력 자료 (type: "user_text")
- `대분류/소분류\n내용` 형식으로 올 수 있음
  예) "지수팩터/긍정\n유가 안정 국면 진입" → 📌 지수 팩터 > ☑️긍정 에 배치
- 대분류/소분류 없이 올 경우 맥락으로 판단해 적절한 항목에 배치
- 사용자가 직접 입력한 수치는 신뢰하고 사용

### 사진/PDF 자료 (type: "image" / "pdf")
- 내용을 파악해 적절한 항목에 배치

## 작성 원칙
- 정보 정확성 최우선. 없는 정보는 "(정보 없음)" 표기, 절대 창작 금지
- 한 줄에 하나의 팩트, 간결하게
- 항목이 비어있어도 형식은 유지
"""


async def call_claude(session: dict) -> str:
    """누적된 세션 자료를 Claude에게 넘겨 마감일지 완성본 반환"""

    date_label = session["date"]
    items = session["items"]

    # 메시지 content 블록 구성
    content_blocks = []

    intro = f"아래는 {date_label} 마감일지 작성을 위해 수집한 자료들입니다. 완성된 마감일지를 출력해주세요.\n\n"
    text_parts = [intro]

    for i, item in enumerate(items):
        t = item["type"]

        if t in ("user_text", "daeshin"):
            label = "【대신전략 장중시황 — 팩터/업종만 참고】" if t == "daeshin" else f"【자료 {i+1}】"
            text_parts.append(f"{label}\n{item['content']}\n\n")

        elif t == "image":
            # 텍스트 파트를 먼저 flush
            if text_parts:
                content_blocks.append({"type": "text", "text": "".join(text_parts)})
                text_parts = []
            content_blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": item["media_type"],
                    "data": item["data"],
                },
            })
            content_blocks.append({"type": "text", "text": f"(위 이미지는 자료 {i+1}입니다)\n\n"})

        elif t == "pdf":
            if text_parts:
                content_blocks.append({"type": "text", "text": "".join(text_parts)})
                text_parts = []
            content_blocks.append({
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": item["data"],
                },
            })
            content_blocks.append({"type": "text", "text": f"(위 PDF는 자료 {i+1}입니다)\n\n"})

    if text_parts:
        content_blocks.append({"type": "text", "text": "".join(text_parts)})

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": CLAUDE_MODEL,
                "max_tokens": 2000,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": content_blocks}],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["content"][0]["text"]


# ── 핸들러 ────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📊 ClosingBell 봇 시작!\n\n"
        "사용법:\n"
        "• 자료를 그냥 붙여넣으면 누적 저장\n"
        "• `3/13 마감일지 생성` → 새 날짜 시작\n"
        "• `정리해줘` → 완성본 출력\n"
        "• 사진/PDF도 바로 보내세요 🗂"
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text.strip()

    # ── 새 마감일지 생성 명령 ──
    import re
    new_date_match = re.search(r"(\d{1,2}/\d{1,2})\s*마감일지\s*생성", text)
    if new_date_match:
        date_label = new_date_match.group(1)
        sessions[chat_id] = {"date": date_label, "items": []}
        await update.message.reply_text(f"✅ {date_label} 마감일지 새로 시작합니다!")
        return

    # ── 정리 명령 ──
    if any(kw in text for kw in ["정리해줘", "완성해줘", "출력해줘"]):
        session = get_session(chat_id)
        if not session["items"]:
            await update.message.reply_text("⚠️ 아직 입력된 자료가 없어요. 자료를 먼저 붙여넣어 주세요!")
            return
        await update.message.reply_text("⏳ 마감일지 작성 중... 잠깐만요!")
        try:
            result = await call_claude(session)
            await update.message.reply_text(result)
        except Exception as e:
            logger.error(f"Claude API error: {e}")
            await update.message.reply_text(f"❌ 오류 발생: {e}")
        return

    # ── 자료 누적 ──
    session = get_session(chat_id)

    # 대신전략 장중시황 감지
    if "[장 중 시황]" in text and "대신증권" in text or "FICC리서치" in text or "daishinstrategy" in text:
        session["items"].append({"type": "daeshin", "content": text})
        await update.message.reply_text("📥 대신전략 장중시황 저장 (팩터/업종만 반영)")
    else:
        session["items"].append({"type": "user_text", "content": text})
        await update.message.reply_text("📥 자료 저장 완료")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    session = get_session(chat_id)

    # 가장 큰 해상도 선택
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)

    async with httpx.AsyncClient() as client:
        resp = await client.get(file.file_path)
        image_data = base64.b64encode(resp.content).decode()

    session["items"].append({
        "type": "image",
        "media_type": "image/jpeg",
        "data": image_data,
    })
    await update.message.reply_text("🖼 사진 저장 완료")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    session = get_session(chat_id)

    doc = update.message.document
    file = await context.bot.get_file(doc.file_id)

    async with httpx.AsyncClient() as client:
        resp = await client.get(file.file_path)
        file_data = base64.b64encode(resp.content).decode()

    mime = doc.mime_type or ""

    if "pdf" in mime:
        session["items"].append({"type": "pdf", "data": file_data})
        await update.message.reply_text("📄 PDF 저장 완료")
    elif "image" in mime:
        session["items"].append({"type": "image", "media_type": mime, "data": file_data})
        await update.message.reply_text("🖼 이미지 저장 완료")
    else:
        await update.message.reply_text(f"⚠️ 지원하지 않는 파일 형식이에요: {mime}")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """현재 누적 자료 현황 확인"""
    chat_id = update.effective_chat.id
    session = get_session(chat_id)
    count = len(session["items"])
    types = [item["type"] for item in session["items"]]
    type_summary = ", ".join(types) if types else "없음"
    await update.message.reply_text(
        f"📋 [{session['date']} 마감일지]\n"
        f"누적 자료: {count}건\n"
        f"종류: {type_summary}"
    )


# ── 진입점 ────────────────────────────────────────────────────────────

def main():
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("ClosingBell 봇 시작")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
