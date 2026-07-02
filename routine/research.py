"""GitHub Action용 조사 스크립트.
_state.json(기존 시그널)을 읽고, Claude API + 웹검색으로 지난 24~48시간
저축은행 시장 변화를 조사해 새 시그널 후보를 _new.json에 저장한다.
필요 환경변수: ANTHROPIC_API_KEY.
조사 실패/결과 없음이어도 빈 배열을 저장(절대 예외로 죽지 않음) → update.py가 timestamp만 갱신해 push."""
import os, json, pathlib, datetime, re
import anthropic

MODEL = "claude-sonnet-4-6"
today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

state = json.load(open("_state.json", encoding="utf-8"))
existing_titles = [x.get("title", "") for x in state["analyzed"]["signals"]]

prompt = f"""오늘은 {today}. 너는 OK저축은행 채널기획팀의 시장분석 담당이다.
지난 24~48시간(최대 최근 3일) 국내 동종 저축은행 8개사(SBI·OK·웰컴·한국투자·페퍼·애큐온·상상인·OSB) 및 저축은행 업권 전반의 시장 변화를 web_search로 조사하라.
조사 범위: 저축은행 예·적금 금리와 신상품, 금융위/금감원 정책·중금리대출·햇살론·건전성 규제, 부동산PF·M&A·분기실적, 앱/제휴/디지털.
각 항목은 반드시 원문 기사/공시 URL을 확보하라(확인 불가하면 그 항목은 버려라).

아래는 이미 대시보드에 있는 기존 시그널 제목이다. 이와 중복되거나 같은 사건이면 제외하라:
{json.dumps(existing_titles, ensure_ascii=False)}

기존과 겹치지 않는 '새로운' 시그널만 0~5건 선별하라. 마땅한 새 소식이 없으면 빈 배열을 반환하라.
반드시 출력은 JSON 배열 하나만. 앞뒤 설명·마크다운 코드펜스 없이 순수 JSON만 출력하라. 각 원소 스키마:
{{"date":"YYYY-MM-DD(기사에 나온 실제 발생/보도일)","source":"매체명","company":"SBI저축은행|OK저축은행|웰컴저축은행|한국투자저축은행|페퍼저축은행|애큐온저축은행|상상인저축은행|OSB저축은행|공통","category":"리텐션|심사|유입|전환|실행|정책|트렌드","title":"헤드라인(수치 포함 권장)","summary":"2-3문장 한국어 요약","url":"원문 URL","importance_score":정수 1-10,"tags":["키워드","..."]}}
category는 위 7종 중 하나만 사용. importance_score: 정책 변화 9-10 / 대형사 신상품 7-8 / 일반 뉴스 4-6."""

new = []
try:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    resp = client.messages.create(
        model=MODEL,
        max_tokens=3500,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 12}],
        messages=[{"role": "user", "content": prompt}],
    )
    texts = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
    raw = texts[-1] if texts else "[]"
    m = re.search(r"\[.*\]", raw, re.S)
    parsed = json.loads(m.group(0) if m else "[]")
    if isinstance(parsed, list):
        new = parsed
except Exception as e:
    print("research WARN:", repr(e)[:300])
    new = []

# 방어적 정리(스키마 강제)
clean = []
for x in new:
    if not isinstance(x, dict) or not str(x.get("title", "")).strip():
        continue
    try:
        score = int(x.get("importance_score", 5))
    except Exception:
        score = 5
    clean.append({
        "date": str(x.get("date", today))[:10],
        "source": str(x.get("source", "")),
        "company": str(x.get("company", "공통")),
        "category": str(x.get("category", "트렌드")),
        "title": str(x.get("title", "")),
        "summary": str(x.get("summary", "")),
        "url": str(x.get("url", "")),
        "importance_score": max(1, min(10, score)),
        "tags": x.get("tags", []) if isinstance(x.get("tags"), list) else [],
    })

pathlib.Path("_new.json").write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
print("research: %d new signals" % len(clean))
for c in clean:
    print(" -", c["date"], c["category"], c["title"][:60])
