"""GitHub Action용 조사 스크립트 (이중 모드).
_state.json(기존 시그널)을 읽고 새 시그널 후보를 _new.json에 저장한다.

- ANTHROPIC_API_KEY 가 있으면: Claude API + 웹검색으로 고품질 조사.
- 없으면: 구글 뉴스 RSS(무료, 키 불필요)에서 최근 저축은행 기사를 긁어와 시그널화.
어느 경우든 실패해도 예외로 죽지 않고 []를 저장 → update.py가 timestamp만 갱신해 push."""
import os, json, pathlib, datetime, re

today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
state = json.load(open("_state.json", encoding="utf-8"))
existing = state["analyzed"]["signals"]


def norm(t):
    return "".join(str(t).split()).lower()


existing_norm = {norm(x.get("title", "")) for x in existing}


def clean_signal(x):
    """스키마 강제 + 값 정리."""
    try:
        score = int(x.get("importance_score", 5))
    except Exception:
        score = 5
    return {
        "date": str(x.get("date", today))[:10],
        "source": str(x.get("source", "")),
        "company": str(x.get("company", "공통")),
        "category": str(x.get("category", "트렌드")),
        "title": str(x.get("title", "")).strip(),
        "summary": str(x.get("summary", "")).strip(),
        "url": str(x.get("url", "")),
        "importance_score": max(1, min(10, score)),
        "tags": x.get("tags", []) if isinstance(x.get("tags"), list) else [],
    }


# ---------------- 모드 A: Claude API + 웹검색 ----------------
def research_api():
    import anthropic
    titles = [x.get("title", "") for x in existing]
    prompt = f"""오늘은 {today}. 너는 OK저축은행 채널기획팀의 시장분석 담당이다.
지난 24~48시간(최대 최근 3일) 국내 동종 저축은행 8개사(SBI·OK·웰컴·한국투자·페퍼·애큐온·상상인·OSB) 및 저축은행 업권 전반의 시장 변화를 web_search로 조사하라.
조사 범위: 저축은행 예·적금 금리와 신상품, 금융위/금감원 정책·중금리대출·햇살론·건전성 규제, 부동산PF·M&A·분기실적, 앱/제휴/디지털.
각 항목은 반드시 원문 기사/공시 URL을 확보하라(확인 불가하면 버려라).
아래는 이미 대시보드에 있는 기존 시그널 제목이다. 중복/동일사건 제외하라:
{json.dumps(titles, ensure_ascii=False)}
기존과 겹치지 않는 새 시그널만 0~5건 선별. 없으면 빈 배열.
출력은 오직 JSON 배열 하나(설명·코드펜스 없이). 각 원소:
{{"date":"YYYY-MM-DD","source":"매체명","company":"SBI저축은행|...|공통","category":"리텐션|심사|유입|전환|실행|정책|트렌드","title":"헤드라인","summary":"2-3문장 한국어","url":"원문 URL","importance_score":1-10,"tags":["키워드"]}}
importance_score: 정책 9-10 / 대형사 신상품 7-8 / 일반 4-6."""
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    resp = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=3500,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 12}],
        messages=[{"role": "user", "content": prompt}],
    )
    texts = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
    m = re.search(r"\[.*\]", texts[-1] if texts else "[]", re.S)
    parsed = json.loads(m.group(0) if m else "[]")
    return parsed if isinstance(parsed, list) else []


# ---------------- 모드 B: 구글 뉴스 RSS (무료) ----------------
CO_MAP = {"SBI": "SBI저축은행", "OK저축": "OK저축은행", "웰컴": "웰컴저축은행",
          "한국투자저축": "한국투자저축은행", "페퍼": "페퍼저축은행", "애큐온": "애큐온저축은행",
          "상상인": "상상인저축은행", "OSB": "OSB저축은행"}
CAT_RULES = [
    (("금융위", "금감원", "정책", "규제", "법", "햇살론", "입법", "감독"), "정책", 8),
    (("PF", "M&A", "매각", "인수", "실적", "순익", "BIS", "신용등급", "적자", "흑자"), "트렌드", 7),
    (("예금", "적금", "금리", "수신", "특판"), "유입", 7),
    (("대출", "중금리", "여신", "심사", "연체", "부실"), "심사", 6),
    (("앱", "제휴", "디지털", "간편", "페이", "마이데이터"), "실행", 6),
]


def classify(title):
    for kws, cat, score in CAT_RULES:
        if any(k in title for k in kws):
            return cat, score
    return "트렌드", 5


def toks(t):
    return set(re.findall(r"[가-힣A-Za-z0-9]{2,}", t))


def jaccard(a, b):
    return len(a & b) / len(a | b) if (a or b) else 0.0


def research_rss():
    import urllib.request, xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime
    url = ("https://news.google.com/rss/search?q="
           "%EC%A0%80%EC%B6%95%EC%9D%80%ED%96%89%20when:3d&hl=ko&gl=KR&ceid=KR:ko")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    data = urllib.request.urlopen(req, timeout=30).read()
    root = ET.fromstring(data)
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=60)
    cand, seen = [], set()
    for it in root.findall(".//item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        pub = it.findtext("pubDate") or ""
        src_el = it.find("{*}source")
        source = (src_el.text if src_el is not None else "").strip()
        if source and title.endswith("- " + source):
            title = title[: -(len(source) + 2)].strip()
        if not title:
            continue
        try:
            dt = parsedate_to_datetime(pub)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
        except Exception:
            dt = datetime.datetime.now(datetime.timezone.utc)
        if dt < cutoff:
            continue
        nt = norm(title)
        if nt in existing_norm or nt in seen:
            continue
        seen.add(nt)
        cat, score = classify(title)
        company = "공통"
        for k, v in CO_MAP.items():
            if k in title:
                company = v
                break
        cand.append({
            "date": dt.strftime("%Y-%m-%d"), "source": source or "구글뉴스",
            "company": company, "category": cat, "title": title,
            "summary": title, "url": link, "importance_score": score,
            "tags": ["자동수집"], "_t": toks(title),
        })
    # 유사 기사(같은 사건, 매체만 다름) 제거하며 다양한 상위 4건 선별
    cand.sort(key=lambda x: (x["date"], x["importance_score"]), reverse=True)
    picked, used_group = [], set()
    for c in cand:
        # 기존 시그널과 유사하면 스킵
        if any(jaccard(c["_t"], toks(e.get("title", ""))) > 0.5 for e in existing):
            continue
        # 같은 (회사, 카테고리)는 1건만 — 같은 사건 여러 매체 스팸 방지
        grp = (c["company"], c["category"])
        if grp in used_group:
            continue
        # 이미 뽑은 것과 유사해도 스킵
        if any(jaccard(c["_t"], p["_t"]) > 0.4 for p in picked):
            continue
        used_group.add(grp)
        picked.append(c)
        if len(picked) >= 4:
            break
    for c in picked:
        c.pop("_t", None)
    return picked


# ---------------- 실행 ----------------
mode = "API" if os.environ.get("ANTHROPIC_API_KEY") else "RSS"
new = []
try:
    new = research_api() if mode == "API" else research_rss()
except Exception as e:
    print("research WARN (%s): %s" % (mode, repr(e)[:300]))
    new = []

clean = [clean_signal(x) for x in new if isinstance(x, dict) and str(x.get("title", "")).strip()]
pathlib.Path("_new.json").write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
print("research[%s]: %d new signals" % (mode, len(clean)))
for c in clean:
    print(" -", c["date"], c["category"], c["title"][:60])
