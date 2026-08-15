# -*- coding: utf-8 -*-
"""
ARGOS CGV IMAX 감시봇
- 용산아이파크몰 / 영등포타임스퀘어 IMAX관의 '오디세이' 상영을 감시한다.
- 대상: 평일(월~금) 18:00 이전 시작 회차
- 알림 조건:
  신규 회차 오픈 : 새로운 회차가 예매 목록에 등장 (취소표 알림은 사용자 요청으로 제거)
- 오픈 이력은 openings.log 에 기록해 두었다가 오픈 요일/시각 패턴 분석에 사용한다.
- 알림 채널: 텔레그램 봇 (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 환경변수)
  환경변수가 없으면 전송 없이 로그만 출력한다(테스트 모드).
"""

import json
import os
import sys
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

MOVIE_KEYWORD = "오디세이"
SCREEN_KEYWORD = "IMAX"
CUTOFF_TIME = "1800"          # 이 시각 '이전' 시작 회차만 (HHMM)
DAYS_AHEAD = 20               # 오늘부터 며칠치 스케줄을 훑을지
                              # CGV 오픈 범위(~2주)보다 넉넉히 잡아야
                              # '감시 범위에 새로 들어온 날'을 신규 오픈으로 오인하지 않는다
WEEKDAYS_ONLY = True          # 평일(월~금)만

SITES = {
    "0013": "용산아이파크몰"
}

API = "https://cgv.co.kr/api/v1/booking/searchMovScnInfo?coCd=A420&siteNo={site}&scnYmd={ymd}&rtctlScopCd=08"
BOOKING_URL = "https://cgv.co.kr/cnm/movieBook/cinema"

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
OPENINGS_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "openings.log")

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Referer": "https://cgv.co.kr/cnm/movieBook/cinema",
}

WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]


def http_get_json(url: str):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read().decode("utf-8"))


def fetch_showtimes():
    """감시 대상 회차 목록을 {key: info} 로 반환."""
    today = datetime.now(KST).date()
    found = {}
    errors = []
    for site_no, site_name in SITES.items():
        for d in range(DAYS_AHEAD + 1):
            day = today + timedelta(days=d)
            if WEEKDAYS_ONLY and day.weekday() >= 5:
                continue
            ymd = day.strftime("%Y%m%d")
            url = API.format(site=site_no, ymd=ymd)
            try:
                data = http_get_json(url)
            except Exception as e:
                errors.append(f"{site_name} {ymd}: {e}")
                continue
            for row in (data.get("data") or []):
                mov = (row.get("movNm") or "") + (row.get("expoProdNm") or "")
                screen = (row.get("scnsNm") or "") + (row.get("movkndDsplNm") or "")
                start = row.get("scnsrtTm") or ""
                if MOVIE_KEYWORD not in mov:
                    continue
                if SCREEN_KEYWORD.lower() not in screen.lower():
                    continue
                if not start or start >= CUTOFF_TIME:
                    continue
                key = "|".join([site_no, ymd, row.get("scnsNo") or "", row.get("scnSseq") or ""])
                found[key] = {
                    "site": site_name,
                    "ymd": ymd,
                    "weekday": WEEKDAY_KO[day.weekday()],
                    "start": f"{start[:2]}:{start[2:]}",
                    "screen": row.get("scnsNm") or "IMAX관",
                    "free": int(row.get("frSeatCnt") or 0),
                    "total": int(row.get("stcnt") or 0),
                }
    return found, errors


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)


def fmt_date(ymd, weekday):
    return f"{int(ymd[4:6])}/{int(ymd[6:8])}({weekday})"


def build_alerts(prev, curr):
    """이전 상태와 비교해 '신규 회차 오픈' 알림 문구 목록을 만든다."""
    alerts = []
    first_run = not prev
    now = datetime.now(KST)
    new_keys = [k for k in sorted(curr) if k not in prev]
    for key in new_keys:
        info = curr[key]
        # 오픈 이력 기록 (패턴 분석용): 감지시각 TAB 상영일 TAB 시작시각 TAB 극장 TAB 잔여/전체
        with open(OPENINGS_LOG, "a", encoding="utf-8") as f:
            f.write("\t".join([
                now.strftime("%Y-%m-%d %H:%M"),
                info["ymd"], info["start"], info["site"],
                f"{info['free']}/{info['total']}",
            ]) + "\n")
        if not first_run:
            show_day = datetime.strptime(info["ymd"], "%Y%m%d").date()
            d_day = (show_day - now.date()).days
            head = f"{info['site']} {info['screen']} · {fmt_date(info['ymd'], info['weekday'])} {info['start']}"
            alerts.append(f"🆕 신규 회차 오픈! (상영 D-{d_day})\n{head}\n잔여 {info['free']}/{info['total']}석")
    return alerts


def send_telegram(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("[테스트 모드] 텔레그램 미설정 — 전송 생략:")
        print(text)
        return
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as res:
        res.read()


def main():
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    if os.environ.get("ARGOS_PING") == "1":
        send_telegram(f"✅ ARGOS CGV 봇 설정 확인 완료 ({now} KST)\n"
                      "이제 신규 회차 오픈·취소표 발생 시 이 방으로 알림이 옵니다.")
        print("설정 확인 핑 전송")
    curr, errors = fetch_showtimes()
    prev = load_state()

    print(f"[{now} KST] 감시 대상 회차 {len(curr)}건 확인")
    for key, info in sorted(curr.items()):
        print(f"  - {info['site']} {fmt_date(info['ymd'], info['weekday'])} "
              f"{info['start']} {info['screen']} 잔여 {info['free']}/{info['total']}")

    if errors:
        print(f"조회 오류 {len(errors)}건:", file=sys.stderr)
        for e in errors:
            print("  " + e, file=sys.stderr)

    # 스케줄 조회가 전부 실패하면 상태를 덮어쓰지 않고 종료(오탐 방지)
    if not curr and errors:
        print("전 회차 조회 실패 — 상태 유지 후 종료", file=sys.stderr)
        sys.exit(1)

    alerts = build_alerts(prev, curr)
    if alerts:
        header = "🤖 ARGOS · CGV IMAX 오디세이\n" + "─" * 22
        footer = f"\n예매 바로가기: {BOOKING_URL}"
        message = header + "\n" + "\n\n".join(alerts) + footer
        send_telegram(message)
        print(f"알림 {len(alerts)}건 전송 완료")
    else:
        print("변동 없음")

    save_state(curr)


if __name__ == "__main__":
    main()
