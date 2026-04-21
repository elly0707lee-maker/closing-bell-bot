"""
ClosingBell 마감일지 텔레그램 봇
예니(Money Plus 앵커) 전용
"""

import os
import io
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
sessions: dict[int, dict] = {}

def today_label() -> str:
    now = datetime.now(KST)
    return f"{now.month}/{now.day}"

def get_session(chat_id: int) -> dict:
    if chat_id not in sessions:
        sessions[chat_id] = {"date": today_label(), "items": []}
    return sessions[chat_id]

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
CLAUDE_MODEL = "claude-sonnet-4-20250514"

SYSTEM_PROMPT = """당신은 예니(이예은, Money Plus 앵커)의 국내 증시 마감일지 작성 전담 AI입니다.

## 역할
사용자가 하루 동안 축적한 시황 자료들을 받아 하나의 완성된 마감일지로 정리합니다.

## 출력 형식 (반드시 이 형식 그대로)
✓ {date} 마감일지
#ClosingBell

📌 마감수치
☑️ 코스피
- (마감수치/코스피 입력값)

☑️ 코스닥
- (마감수치/코스닥 입력값)

📌 지수 팩터
☑️긍정
- 항목

☑️부정
- 항목

📌 수급
☑️ 코스피
- (수급/코스피 입력값)

☑️ 코스닥
- (수급/코스닥 입력값)

📌 환율
- 환율 관련 주요 팩터

📌 특징 업종
- 업종명: 배경/이유 한 줄

📌 특징주
- 종목명 (종목코드): 개별 이슈 한 줄 요약

📌 내일 일정
- 항목
=====

## 자료 처리 규칙

### 📌 마감수치 / 📌 수급 — 철칙
- 오직 사용자가 직접 입력한 것만 사용
- 다른 자료에서 수치를 가져오거나 추정하는 것 절대 금지
- 입력이 없으면 반드시 "(정보 없음)" 표기

### 대신전략 자료 (type: "daeshin")
- 지수 팩터 배경 설명, 특징 업종/테마 흐름만 추출
- 수급·환율·마감수치 수치 절대 사용 금지

### 독학주식 자료 (type: "dokhagjushik")
- 📌 특징 업종에만 반영

### 특징주 자료 (type: "teukjingju")
- 📌 특징주에만 반영
- **핵심 필터링 규칙**: 아래 두 기준으로 반드시 구분할 것
  - ✅ 포함: 종목 고유의 개별 이슈가 상승 이유인 종목
    예) 실적 발표, 신규 상장, 최대주주 변경, 계약 체결, 신제품 출시, 어닝 서프라이즈 등
  - ❌ 제외: "테마 상승 속", "테마 상승에" 등 테마 편승이 주된 이유인 종목
    즉 상승 이유 설명에 "테마" 키워드가 포함된 종목은 무조건 제외
- 형식: `- 종목명 (등락률): 이유 한 줄`
- 등락률 있으면 반드시 표기

### 사용자 직접 입력 (type: "user_text")
태그 인식:
- 마감수치/코스피 → 📌 마감수치 > ☑️ 코스피
- 마감수치/코스닥 → 📌 마감수치 > ☑️ 코스닥
- 수급/코스피 → 📌 수급 > ☑️ 코스피
- 수급/코스닥 → 📌 수급 > ☑️ 코스닥
- 지수팩터/긍정 → 📌 지수 팩터 > ☑️긍정
- 지수팩터/부정 → 📌 지수 팩터 > ☑️부정
- 환율 → 📌 환율
- 특징업종 → 📌 특징 업종
- 특징주 → 📌 특징주
- 내일일정 → 📌 내일 일정
- 태그 없으면 내용 보고 자동 배치

## 작성 원칙
- 정보 정확성 최우선. 없는 정보는 "(정보 없음)" 표기, 절대 창작 금지
- 입력된 자료의 내용을 최대한 살릴 것. 자료가 많으면 출력도 풍부하게
- 각 항목은 불릿 개수 제한 없이 입력된 팩트를 빠짐없이 반영
- 중복 내용만 한 번으로 통합
- 항목이 비어있어도 형식은 유지
"""

async def call_claude(session: dict) -> str:
    date_label = session["date"]
    items = session["items"]
    content_blocks = []
    text_parts = [f"아래는 {date_label} 마감일지 작성을 위해 수집한 자료들입니다. 완성된 마감일지를 출력해주세요.\n\n"]

    for i, item in enumerate(items):
        t = item["type"]
        if t in ("user_text", "daeshin", "dokhagjushik", "teukjingju", "revised_base"):
            if t == "daeshin":
                label = "【대신전략 장중시황 — 팩터/업종만 참고】"
            elif t == "dokhagjushik":
                label = "【독학주식 자료 — 특징 업종에만 반영】"
            elif t == "teukjingju":
                label = "【특징주 자료 — 개별 이슈 종목만 📌 특징주에 반영, 테마 편승 종목 제외】"
            elif t == "revised_base":
                label = "【전체수정 기준본 — 이 내용을 베이스로 하고, 이후 자료를 추가 반영】"
            else:
                label = f"【자료 {i+1}】"
            text_parts.append(f"{label}\n{item['content']}\n\n")
        elif t == "image":
            if text_parts:
                content_blocks.append({"type": "text", "text": "".join(text_parts)})
                text_parts = []
            content_blocks.append({"type": "image", "source": {"type": "base64", "media_type": item["media_type"], "data": item["data"]}})
            content_blocks.append({"type": "text", "text": f"(위 이미지는 자료 {i+1}입니다)\n\n"})
        elif t == "pdf":
            if text_parts:
                content_blocks.append({"type": "text", "text": "".join(text_parts)})
                text_parts = []
            content_blocks.append({"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": item["data"]}})
            content_blocks.append({"type": "text", "text": f"(위 PDF는 자료 {i+1}입니다)\n\n"})

    if text_parts:
        content_blocks.append({"type": "text", "text": "".join(text_parts)})

    async with httpx.AsyncClient(timeout=90) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": CLAUDE_MODEL, "max_tokens": 4000, "system": SYSTEM_PROMPT, "messages": [{"role": "user", "content": content_blocks}]},
        )
        resp.raise_for_status()
        return resp.json()["content"][0]["text"]

def detect_media_type(image_bytes: bytes) -> str:
    if image_bytes[:8] == b'\x89PNG\r\n\x1a\n':
        return "image/png"
    elif image_bytes[:3] == b'\xff\xd8\xff':
        return "image/jpeg"
    return "image/jpeg"

PHOTO_PARSE_PROMPT = """이 이미지가 코스피/코스닥 마감 수치와 수급이 나온 화면인지 먼저 판단하세요.

【마감수치+수급 화면인 경우】
아래 형식으로만 출력하세요. 이미지에 보이는 수치 그대로만 사용, 추정 절대 금지.

📌 마감수치
☑️ 코스피
- 000.00pt (▲/▼ 0.00%)

☑️ 코스닥
- 000.00pt (▲/▼ 0.00%)

📌 수급
☑️ 코스피
개인 00,000 외인 -00,000 기관 -00,000

☑️ 코스닥
개인 -0,000 외인 -000 기관 0,000

규칙:
- 상승은 ▲, 하락은 ▼
- 보이지 않는 수치는 (정보 없음) 표기
- 이 형식 외 다른 말 일절 추가하지 말 것

【마감수치+수급 화면이 아닌 경우】
"📎 사진 저장 완료 (마감수치/수급 화면 아님 — 정리해줘 할 때 반영)" 이 문장만 출력하세요."""

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📊 ClosingBell 봇 시작!\n\n"
        "• 자료 붙여넣기 → 누적 저장\n"
        "• 3/13 마감일지 생성 → 새 날짜 시작\n"
        "• 정리해줘 → 완성본 출력\n"
        "• 사진/PDF 바로 전송 가능"
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import re
    chat_id = update.effective_chat.id
    text = update.message.text.strip()

    # 새 마감일지 생성
    new_date_match = re.search(r"(\d{1,2}/\d{1,2})\s*마감일지\s*생성", text)
    if new_date_match:
        sessions[chat_id] = {"date": new_date_match.group(1), "items": []}
        await update.message.reply_text(f"✅ {new_date_match.group(1)} 마감일지 새로 시작합니다!")
        return

    # 전체수정 명령 (띄어쓰기 무관)
    if re.match(r"^전\s*체\s*수\s*정\s*/", text):
        revised = re.sub(r"^전\s*체\s*수\s*정\s*/\s*", "", text).strip()
        if not revised:
            await update.message.reply_text("⚠️ 수정본 내용을 전체수정/ 아래에 붙여넣어 주세요!")
            return
        session = get_session(chat_id)
        # 기존 전체수정 항목 교체
        session["items"] = [i for i in session["items"] if i["type"] != "revised_base"]
        session["items"].insert(0, {"type": "revised_base", "content": revised})
        await update.message.reply_text(f"✅ 수정본 저장 완료! 이후 추가 자료는 이 위에 누적됩니다. (누적 {len(session['items'])}건)")
        return

    # 정리 명령
    if any(kw in text for kw in ["정리해줘", "완성해줘", "출력해줘"]):
        session = get_session(chat_id)
        if not session["items"]:
            await update.message.reply_text("⚠️ 아직 입력된 자료가 없어요!")
            return
        await update.message.reply_text(f"⏳ 마감일지 작성 중... (누적 {len(session['items'])}건)")
        try:
            result = await call_claude(session)
            await update.message.reply_text(result)
        except Exception as e:
            logger.error(f"Claude API error: {e}")
            await update.message.reply_text(f"❌ 오류: {e}")
        return

    session = get_session(chat_id)

    # 자료 유형 감지
    if "[장 중 시황]" in text and ("대신증권" in text or "FICC리서치" in text or "daishinstrategy" in text):
        session["items"].append({"type": "daeshin", "content": text})
        await update.message.reply_text(f"📥 대신전략 저장 (누적 {len(session['items'])}건)")
    elif "독학주식" in text or "selfstudyview" in text:
        session["items"].append({"type": "dokhagjushik", "content": text})
        await update.message.reply_text(f"📥 독학주식 저장 (누적 {len(session['items'])}건)")
    elif "상한가 및 급등주" in text or "특징주" in text.split("\n")[0]:
        # 첫 줄에 "상한가 및 급등주" 또는 "특징주" 포함 시 특징주 자료로 분류
        session["items"].append({"type": "teukjingju", "content": text})
        await update.message.reply_text(f"📥 특징주 저장 (개별 이슈 종목만 반영, 누적 {len(session['items'])}건)")
    else:
        session["items"].append({"type": "user_text", "content": text})
        await update.message.reply_text(f"📥 자료 저장 완료 (누적 {len(session['items'])}건)")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    session = get_session(chat_id)
    await update.message.reply_text("🔍 사진 분석 중...")
    try:
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        buf = io.BytesIO()
        await file.download_to_memory(buf)
        image_bytes = buf.getvalue()
        media_type = detect_media_type(image_bytes)
        image_data = base64.b64encode(image_bytes).decode()

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": CLAUDE_MODEL, "max_tokens": 500, "messages": [{"role": "user", "content": [{"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_data}}, {"type": "text", "text": PHOTO_PARSE_PROMPT}]}]},
            )
            resp.raise_for_status()
            parsed = resp.json()["content"][0]["text"]

        if "사진 저장 완료" in parsed:
            session["items"].append({"type": "image", "media_type": media_type, "data": image_data})
        else:
            session["items"].append({"type": "user_text", "content": f"[사진 자동파싱 — 마감수치/수급]\n{parsed}"})

        await update.message.reply_text(f"{parsed}\n\n(누적 {len(session['items'])}건)")

    except Exception as e:
        logger.error(f"Photo error: {e}")
        await update.message.reply_text(f"⚠️ 사진 처리 실패: {e}")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    session = get_session(chat_id)
    doc = update.message.document
    file = await context.bot.get_file(doc.file_id)
    buf = io.BytesIO()
    await file.download_to_memory(buf)
    file_data = base64.b64encode(buf.getvalue()).decode()
    mime = doc.mime_type or ""
    if "pdf" in mime:
        session["items"].append({"type": "pdf", "data": file_data})
        await update.message.reply_text(f"📄 PDF 저장 완료 (누적 {len(session['items'])}건)")
    elif "image" in mime:
        session["items"].append({"type": "image", "media_type": mime, "data": file_data})
        await update.message.reply_text(f"🖼 이미지 저장 완료 (누적 {len(session['items'])}건)")
    else:
        await update.message.reply_text(f"⚠️ 지원하지 않는 파일 형식: {mime}")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    session = get_session(chat_id)
    types = [item["type"] for item in session["items"]]
    await update.message.reply_text(
        f"📋 [{session['date']} 마감일지]\n"
        f"누적 자료: {len(session['items'])}건\n"
        f"종류: {', '.join(types) if types else '없음'}"
    )

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
