"""2.5단계: 새 시그널(_new.json)에 대해 OK저축은행 관점의 아이디어를 Claude로 생성해
_state.json의 ideas 앞쪽에 주입한다. (decrypt → research → [ideate] → update 순서)

- ANTHROPIC_API_KEY 없으면 아무것도 안 하고 종료(파이프라인 유지).
- 어떤 실패든 예외로 죽지 않는다(_state 그대로 두고 정상 종료) → update.py가 이어서 push.
- 생성 아이디어의 based_on_insight = '날짜|제목' (대시보드 시그널 id와 동일)으로 저장 →
  대시보드가 해당 기사와 정확히 연결해 보여준다.
"""
import os, json, pathlib, datetime, re, hashlib, urllib.request

BEACON = "https://ntfy.sh/okmi-diag-2607zq"
def beacon(msg):
    try:
        urllib.request.urlopen(urllib.request.Request(BEACON, data=str(msg)[:400].encode("utf-8")), timeout=15)
    except Exception:
        pass

KST = datetime.timezone(datetime.timedelta(hours=9))
today = datetime.datetime.now(KST).strftime("%Y-%m-%d")
MAX_NEW_IDEAS = 3       # 하루 새 기사 아이디어 생성 상한
MAX_BACKFILL = 20       # 백필(수동) 1회 최대 생성 개수 — 기존 기사 일괄 채우기
MAX_DAILY_BACKFILL = 6  # 매일(자동) 아이디어 없는 기존 기사 보충 개수 → 커버리지 자가 수복
MAX_AUTO_KEEP = 30      # 자동 생성 아이디어 누적 상한(시드 아이디어는 항상 보존)
PACE_SECONDS = 5        # 호출 간격(무료 Gemini 분당 한도 회피)


def norm(t):
    return "".join(str(t).split()).lower()


def sig_id(s):
    return "%s|%s" % (str(s.get("date", "")), str(s.get("title", "")))


def idea_id_for(s):
    # 기사별 안정적·고유 id (같은 기사는 항상 같은 id → 재실행해도 중복 생성 안 함)
    return "auto-" + hashlib.md5(sig_id(s).encode("utf-8")).hexdigest()[:8]


def clean_idea(x, signal, idea_id):
    """스키마 강제 — 대시보드가 읽는 모든 필드를 채워 UI 크래시 방지."""
    tc = x.get("target_customer") or {}
    ps = x.get("partnership_structure") or {}
    try:
        conf = float(x.get("confidence_score", 0.7))
    except Exception:
        conf = 0.7
    conf = max(0.0, min(1.0, conf))
    reg = x.get("regulatory_checkpoints")
    reg = reg if isinstance(reg, list) else []
    risks = x.get("risks")
    risks = risks if isinstance(risks, list) else []
    return {
        "idea_id": idea_id,
        "gap": str(x.get("gap", "트렌드")).strip()[:20] or "트렌드",
        "service_name": str(x.get("service_name", "(제목 미정)")).strip() or "(제목 미정)",
        "problem_definition": str(x.get("problem_definition", "")).strip(),
        "target_customer": {
            "primary": str(tc.get("primary", "")).strip(),
            "secondary": str(tc.get("secondary", "")).strip(),
        },
        "solution_approach": str(x.get("solution_approach", "")).strip(),
        "partnership_structure": {
            "자사": str(ps.get("자사", "")).strip(),
            "파트너": str(ps.get("파트너", "")).strip(),
            "제휴 구조": str(ps.get("제휴 구조", "")).strip(),
        },
        "revenue_model": str(x.get("revenue_model", "")).strip(),
        "regulatory_checkpoints": [str(r).strip() for r in reg][:5],
        "mvp_scope": str(x.get("mvp_scope", "")).strip(),
        "risks": [str(r).strip() for r in risks][:5],
        "confidence_score": round(conf, 2),
        "based_on_insight": sig_id(signal),  # 대시보드 매칭용(= 날짜|제목)
        "created_at": today,
        "auto": True,
    }


GEMINI_MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro"]


def _extract_json(text):
    text = re.sub(r"^```[a-z]*|```$", "", text.strip(), flags=re.MULTILINE).strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    return json.loads(m.group(0)) if m else None


def generate_one(s):
    import time
    import google.generativeai as genai  # configure()는 main()에서 1회 수행
    tags = ", ".join([str(t) for t in (s.get("tags") or [])])
    prompt = f"""너는 OK저축은행 채널기획팀의 전략 담당이다. 아래 '시장 시그널(기사)'을 읽고,
OK저축은행 관점에서 실행 가능한 제휴/서비스 아이디어를 정확히 1개 제안하라.

[시그널]
제목: {s.get('title','')}
회사: {s.get('company','')}
카테고리: {s.get('category','')}
요약: {s.get('summary','')}
키워드: {tags}

지침:
- 반드시 OK저축은행이 '자사 관점'에서 대응·선점할 수 있는 액션이어야 한다(경쟁사 소개가 아니라 OK가 할 일).
- 금소법·개인정보보호법·중금리 규제 등 현실적 제약을 고려하라.
- 구체적이고 실행 가능해야 한다(막연한 구호 금지).
- 출력은 오직 JSON 객체 1개. 설명·코드펜스 금지. 스키마:
{{"gap":"유입|전환|심사|실행|리텐션 중 가장 가까운 단계 한 단어",
"service_name":"서비스명(한글, 'OK'로 시작 권장)",
"problem_definition":"이 시그널이 OK에 주는 위협 또는 기회 2-3문장",
"target_customer":{{"primary":"핵심 타깃 + 규모 추정","secondary":"보조 타깃"}},
"solution_approach":"구체적 해결/실행 방식 3-4문장",
"partnership_structure":{{"자사":"OK가 제공하는 것","파트너":"제휴 대상","제휴 구조":"협업 방식"}},
"revenue_model":"수익 모델 또는 KPI",
"regulatory_checkpoints":["규제 체크포인트 2-3개"],
"mvp_scope":"3개월 파일럿 범위",
"risks":["리스크 2-3개"],
"confidence_score":0.0~1.0}}"""
    # 뉴스 대시보드와 동일: gemini-2.5-flash 우선, 실패 시 flash-lite→pro 폴백 + 재시도
    last_err = None
    for model_name in GEMINI_MODELS:
        for attempt in range(3):
            try:
                model = genai.GenerativeModel(
                    model_name=model_name,
                    generation_config={"response_mime_type": "application/json",
                                       "max_output_tokens": 4096},
                )
                resp = model.generate_content(prompt)
                text = (getattr(resp, "text", "") or "").strip()
                if text:
                    parsed = _extract_json(text)
                    if parsed:
                        return parsed
            except Exception as e:
                last_err = e
                msg = str(e).lower()
                is_rate = any(c in msg for c in
                              ["429", "resource has been exhausted", "exhausted",
                               "quota", "rate limit", "resource_exhausted"])
                transient = is_rate or any(c in msg for c in
                              ["503", "500", "502", "504", "econnreset",
                               "etimedout", "timeout", "deadline"])
                if not transient:
                    break  # 다음 모델로 폴백(모델별 무료 한도가 분리돼 있어 회피 가능)
                time.sleep((10 if is_rate else 1.5) * (attempt + 1))
    if last_err:
        raise last_err
    return None


def main():
    beacon("2.5-ideate START")
    if not os.environ.get("GEMINI_API_KEY"):
        print("ideate: no GEMINI_API_KEY — skip")
        beacon("2.5-ideate skip(no key)")
        return
    try:
        state = json.load(open("_state.json", encoding="utf-8"))
    except Exception as e:
        print("ideate: _state.json 없음 — skip", e)
        return
    try:
        new = json.load(open("_new.json", encoding="utf-8"))
        if not isinstance(new, list):
            new = []
    except Exception:
        new = []

    backfill = os.environ.get("IDEATE_BACKFILL", "").strip().lower() in ("1", "true", "yes")
    ideas_obj = state.setdefault("ideas", {})
    ideas = ideas_obj.setdefault("ideas", [])
    state_signals = state.get("analyzed", {}).get("signals", [])
    existing_titles = {norm(s.get("title", "")) for s in state_signals}
    already = {i.get("based_on_insight", "") for i in ideas}

    # 1) 새 시그널(오늘 조사분): 기존과 중복 아니고 아직 아이디어 없는 것
    new_cands = [s for s in new
                 if norm(s.get("title", "")) not in existing_titles
                 and sig_id(s) not in already]
    new_cands.sort(key=lambda s: s.get("importance_score", 0), reverse=True)
    new_cands = new_cands[:MAX_NEW_IDEAS]

    # 2) 대시보드에 이미 있는 기사 중 아이디어 없는 것 보충
    #    - 백필 모드: 최대 MAX_BACKFILL개 (수동, 대량)
    #    - 매일 모드: 최대 MAX_DAILY_BACKFILL개 (자동, 소량) → 커버리지가 스스로 채워짐
    uncovered = [s for s in state_signals if sig_id(s) not in already]
    uncovered.sort(key=lambda s: s.get("importance_score", 0), reverse=True)
    back_cands = uncovered[:(MAX_BACKFILL if backfill else MAX_DAILY_BACKFILL)]

    todo = new_cands + back_cands
    if not todo:
        print("ideate: 후보 시그널 없음 — skip (backfill=%s)" % backfill)
        beacon("2.5-ideate skip(no cand) backfill=%s" % backfill)
        return

    import time
    import google.generativeai as genai
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    made = 0
    for idx, s in enumerate(todo):
        if sig_id(s) in already:   # 이번 실행 중 중복 방지
            continue
        if idx > 0:
            time.sleep(PACE_SECONDS)   # 무료 Gemini 분당 한도 회피(호출 간격 유지)
        try:
            raw = generate_one(s)
        except Exception as e:
            print("ideate: 생성 실패", str(s.get("title", ""))[:30], e)
            continue
        if not raw:
            print("ideate: JSON 파싱 실패", str(s.get("title", ""))[:30])
            continue
        ideas.insert(0, clean_idea(raw, s, idea_id_for(s)))  # 최신이 앞 → '최근 생성 아이디어'에 노출
        already.add(sig_id(s))
        made += 1

    # 자동 생성 아이디어 누적 상한 (created_at 오래된 것부터 제거, 시드는 보존)
    autos = [i for i in ideas if i.get("auto")]
    if len(autos) > MAX_AUTO_KEEP:
        keep = set(id(i) for i in sorted(autos, key=lambda i: i.get("created_at", ""), reverse=True)[:MAX_AUTO_KEEP])
        ideas = [i for i in ideas if (not i.get("auto")) or id(i) in keep]
        ideas_obj["ideas"] = ideas

    ideas_obj["timestamp"] = today
    pathlib.Path("_state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print("ideate: generated=%d total_ideas=%d" % (made, len(ideas_obj["ideas"])))
    beacon("2.5-ideate OK generated=%d total=%d" % (made, len(ideas_obj["ideas"])))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # 어떤 경우에도 파이프라인을 막지 않는다
        print("ideate: 예기치 못한 오류 — 무시하고 계속", e)
        beacon("2.5-ideate ERR %s" % str(e)[:120])
