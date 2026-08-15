/**
 * ARGOS CGV 현황 응답 서버 (Cloudflare Worker)
 * 텔레그램에서 "현황"이라고 보내면 IMAX 오디세이 평일 18시 이전 회차의
 * 잔여석 표를 즉시 답장한다.
 *
 * 시크릿: BOT_TOKEN, ALLOWED_CHAT_ID, WEBHOOK_SECRET
 */

const SITES = { "0013": "용산아이파크몰" };
const MOVIE_KEYWORD = "오디세이";
const SCREEN_KEYWORD = "IMAX";
const CUTOFF_TIME = "1800";
const DAYS_AHEAD = 20;
const WEEKDAY_KO = ["일", "월", "화", "수", "목", "금", "토"];

const HEADERS = {
  "User-Agent":
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
  Accept: "application/json, text/plain, */*",
  "Accept-Language": "ko-KR,ko;q=0.9",
  Referer: "https://cgv.co.kr/cnm/movieBook/cinema",
};

function kstNow() {
  return new Date(Date.now() + 9 * 3600 * 1000); // UTC 기준 +9h, getUTC*로 읽는다
}

function ymd(d) {
  return (
    d.getUTCFullYear().toString() +
    String(d.getUTCMonth() + 1).padStart(2, "0") +
    String(d.getUTCDate()).padStart(2, "0")
  );
}

async function fetchDay(siteNo, dateYmd) {
  const url = `https://cgv.co.kr/api/v1/booking/searchMovScnInfo?coCd=A420&siteNo=${siteNo}&scnYmd=${dateYmd}&rtctlScopCd=08`;
  try {
    const res = await fetch(url, { headers: HEADERS });
    if (!res.ok) return [];
    const j = await res.json();
    return (j.data || []).filter((r) => {
      const mov = (r.movNm || "") + (r.expoProdNm || "");
      const scr = (r.scnsNm || "") + (r.movkndDsplNm || "");
      const start = r.scnsrtTm || "";
      return (
        mov.includes(MOVIE_KEYWORD) &&
        scr.toUpperCase().includes(SCREEN_KEYWORD) &&
        start && start < CUTOFF_TIME
      );
    });
  } catch (e) {
    return [];
  }
}

async function buildReport() {
  const now = kstNow();
  const jobs = [];
  for (const siteNo of Object.keys(SITES)) {
    for (let i = 0; i <= DAYS_AHEAD; i++) {
      const d = new Date(now.getTime() + i * 86400 * 1000);
      const dow = d.getUTCDay();
      if (dow === 0 || dow === 6) continue; // 평일만
      jobs.push(
        fetchDay(siteNo, ymd(d)).then((rows) => ({ siteNo, date: d, rows }))
      );
    }
  }
  const results = await Promise.all(jobs);

  const bySite = {};
  for (const { siteNo, date, rows } of results) {
    if (!rows.length) continue;
    const dayLabel = `${date.getUTCMonth() + 1}/${date.getUTCDate()}(${WEEKDAY_KO[date.getUTCDay()]})`;
    const parts = rows
      .sort((a, b) => (a.scnsrtTm || "").localeCompare(b.scnsrtTm || ""))
      .map((r) => {
        const t = `${r.scnsrtTm.slice(0, 2)}:${r.scnsrtTm.slice(2)}`;
        const free = parseInt(r.frSeatCnt || "0", 10);
        return `${t} ${free === 0 ? "매진" : free + "석"}`;
      });
    (bySite[siteNo] = bySite[siteNo] || []).push(`${dayLabel} ${parts.join(" · ")}`);
  }

  const stamp = `${now.getUTCMonth() + 1}/${now.getUTCDate()} ${String(now.getUTCHours()).padStart(2, "0")}:${String(now.getUTCMinutes()).padStart(2, "0")}`;
  let out = `📊 IMAX 오디세이 현황 (평일 18시 전)\n기준: ${stamp} KST\n`;
  let any = false;
  for (const siteNo of Object.keys(SITES)) {
    out += `\n[${SITES[siteNo]}]\n`;
    if (bySite[siteNo] && bySite[siteNo].length) {
      out += bySite[siteNo].join("\n") + "\n";
      any = true;
    } else {
      out += "예매 가능한 회차 없음\n";
    }
  }
  if (!any) out += "\n(조회 실패였을 수도 있으니 잠시 후 다시 시도해보세요)";
  return out;
}

async function send(env, chatId, text) {
  await fetch(`https://api.telegram.org/bot${env.BOT_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, text, disable_web_page_preview: true }),
  });
}

function splitMessage(text, limit = 3800) {
  const parts = [];
  let buf = "";
  for (const line of text.split("\n")) {
    if ((buf + "\n" + line).length > limit) {
      parts.push(buf);
      buf = line;
    } else {
      buf = buf ? buf + "\n" + line : line;
    }
  }
  if (buf) parts.push(buf);
  return parts;
}

export default {
  async fetch(request, env) {
    if (request.method !== "POST") return new Response("ARGOS CGV status bot");
    if (
      env.WEBHOOK_SECRET &&
      request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET
    ) {
      return new Response("forbidden", { status: 403 });
    }
    const update = await request.json().catch(() => null);
    const msg = update && update.message;
    const chatId = msg && msg.chat && msg.chat.id;
    const text = ((msg && msg.text) || "").trim();
    if (!chatId) return new Response("ok");
    if (String(chatId) !== String(env.ALLOWED_CHAT_ID)) return new Response("ok");

    if (text.includes("현황")) {
      const report = await buildReport();
      for (const part of splitMessage(report)) await send(env, chatId, part);
    } else if (text && !text.startsWith("/")) {
      await send(env, chatId, "「현황」이라고 보내면 IMAX 오디세이 잔여석 표를 보내드려요 🎬");
    }
    return new Response("ok");
  },
};
