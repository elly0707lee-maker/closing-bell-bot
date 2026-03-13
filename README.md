# ClosingBell 마감일지 봇

예니(Money Plus 앵커) 전용 국내 증시 마감일지 자동 정리 텔레그램 봇

---

## 배포 방법 (Railway)

### 1. 텔레그램 봇 토큰 발급
1. 텔레그램에서 `@BotFather` 검색
2. `/newbot` 명령 입력
3. 봇 이름, 유저네임 설정
4. 발급된 **API Token** 복사

### 2. Anthropic API 키 확인
- https://console.anthropic.com 에서 API Key 복사

### 3. GitHub에 코드 올리기
```bash
git init
git add .
git commit -m "init"
git remote add origin https://github.com/YOUR_USERNAME/closing-bell-bot.git
git push -u origin main
```

### 4. Railway 배포
1. https://railway.app 로그인 (GitHub 연동)
2. **New Project** → **Deploy from GitHub repo**
3. 이 저장소 선택
4. **Variables** 탭에서 환경변수 2개 추가:
   - `TELEGRAM_BOT_TOKEN` = BotFather에서 받은 토큰
   - `ANTHROPIC_API_KEY` = Anthropic 콘솔 API 키
5. Deploy → 완료!

---

## 사용법

| 입력 | 동작 |
|------|------|
| `3/13 마감일지 생성` | 새 날짜 마감일지 시작 (기존 초기화) |
| 텍스트 붙여넣기 | 자료 누적 저장 |
| 사진 전송 | 이미지 저장 |
| PDF 전송 | PDF 저장 |
| `정리해줘` | Claude가 완성본 마감일지 출력 |
| `/status` | 현재 누적 자료 현황 확인 |

---

## 자료별 처리 규칙

- **대신전략 장중시황**: `[장 중 시황]` 키워드 자동 감지 → 지수 팩터/특징 업종만 추출, 수급/환율/개별 수치 제외
- **직접 입력**: `지수팩터/긍정\n내용` 형식 → 해당 항목에 자동 배치
- **사진/PDF**: Claude가 내용 파악 후 적절한 항목 배치

---

## 출력 형식

```
✓ 3/13 마감일지
#ClosingBell

원달러: 1,488원 (▲3원)

KOSPI 2,580.00 (-1.2%) / KOSDAQ 720.00 (+0.5%)

📌 지수 팩터
☑️긍정
- 항목

☑️부정
- 항목

📌 수급
...

📌 환율
...

📌 특징 업종
...

📌 내일 일정
...
=====
```
