"""3단계: _state.json + _new.json 을 머지→검증→재암호화→커밋→push→push검증.
- PASSWORD는 환경변수 DASHBOARD_PASSWORD로 주입.
- DRY_RUN=1 이면 git 명령을 실제로 실행하지 않고 출력만(로컬 테스트용).
- 성공 시 마지막에 'PUSHED OK <hash>' 를 출력한다. 이 줄이 없으면 실패다.
"""
import os, json, base64, pathlib, subprocess, datetime, sys
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

PASSWORD = os.environ["DASHBOARD_PASSWORD"]
ITER = 100000
DRY = os.environ.get("DRY_RUN") == "1"
today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

s = json.load(open("_state.json", encoding="utf-8"))
a = s["analyzed"]
sig = list(a.get("signals", []))
try:
    new = json.load(open("_new.json", encoding="utf-8"))
    if not isinstance(new, list):
        new = []
except Exception:
    new = []

# --- 머지: 공백 무시 소문자 제목으로 중복 스킵, 새것만 추가 ---
def norm(t):
    return "".join(str(t).split()).lower()
existing = {norm(x.get("title", "")) for x in sig}
added = 0
for n in sorted(new, key=lambda x: x.get("importance_score", 0), reverse=True):
    if norm(n.get("title", "")) in existing:
        continue
    n.setdefault("url", "")
    sig.append(n)
    existing.add(norm(n.get("title", "")))
    added += 1

# --- 상한 20: 오래된(날짜 빠른, 동률이면 낮은 점수) 것부터 제거 ---
sig.sort(key=lambda x: (x.get("date", ""), x.get("importance_score", 0)))
removed = 0
while len(sig) > 20:
    sig.pop(0)
    removed += 1
sig.sort(key=lambda x: (x.get("date", ""), x.get("importance_score", 0)), reverse=True)

# --- 날짜 매일 무조건 갱신 ---
a["signals"] = sig
a["timestamp"] = today + "T00:00:00Z"
a["signals_updated_at"] = today

# --- 검증: 치명적 손상만 중단, 개수는 clamp 했으므로 abort 안 함 ---
assert len(s["competitor"]["companies"]) == 8, "competitor 8개사 손상 — 중단"
assert len(s["ideas"]["ideas"]) > 0, "ideas 소실 — 중단"
for x in sig:
    x.setdefault("url", "")
    for f in ["date", "source", "company", "category", "title", "summary"]:
        x.setdefault(f, "")
    x.setdefault("importance_score", 5)
    x.setdefault("tags", [])
if len(sig) < 15:
    print("WARN: signals %d개(<15) — 그래도 push 진행" % len(sig))

# --- 재암호화 & 주입 ---
pt = json.dumps(s, ensure_ascii=False).encode("utf-8")
salt = os.urandom(16)
iv = os.urandom(12)
key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITER).derive(PASSWORD.encode())
ct = AESGCM(key).encrypt(iv, pt, None)
blob = {"algorithm": "AES-GCM", "kdf": "PBKDF2-SHA256", "iterations": ITER,
        "salt": base64.b64encode(salt).decode(),
        "iv": base64.b64encode(iv).decode(),
        "ciphertext": base64.b64encode(ct).decode()}
html = pathlib.Path("index.html").read_text(encoding="utf-8")
open_tag = '<script id="encrypted-blob" type="application/json">'
i = html.index(open_tag) + len(open_tag)
j = html.index("</script>", i)
new_html = html[:i] + json.dumps(blob) + html[j:]
assert new_html != html, "blob 교체 실패 — 중단"
pathlib.Path("index.html").write_text(new_html, encoding="utf-8")
print("reencrypted / added=%d removed=%d final=%d date=%s" % (added, removed, len(sig), today))


def sh(cmd):
    if DRY:
        print("[DRY] $", cmd)
        return 0
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    print("$", cmd, "->", r.returncode)
    if r.stdout.strip():
        print(r.stdout.strip()[:500])
    if r.stderr.strip():
        print("ERR:", r.stderr.strip()[:500])
    return r.returncode


# --- 커밋 & PUSH (실패 시 pull --rebase 후 1회 재시도) ---
sh('git config user.email "routine@market.local"')
sh('git config user.name "MI Routine"')
sh("git add index.html")
sh('git commit -m "data: auto-update %s"' % today)
if sh("git push origin main") != 0:
    sh("git pull --rebase origin main")
    sh("git push origin main")

# --- PUSH 검증: 로컬 HEAD == origin/main 인지 확인 ---
if DRY:
    print("[DRY] skip push verify")
    sys.exit(0)
local = subprocess.run("git rev-parse HEAD", shell=True, capture_output=True, text=True).stdout.strip()
rl = subprocess.run("git ls-remote origin main", shell=True, capture_output=True, text=True).stdout.split()
remote = rl[0] if rl else ""
if local and local == remote:
    print("PUSHED OK", local[:10])
else:
    print("PUSH FAILED local=%s remote=%s" % (local[:10], remote[:10]))
    sys.exit(1)
