"""3단계: _state.json + _new.json 을 머지→검증→재암호화→커밋→push→push검증.
- PASSWORD는 환경변수 DASHBOARD_PASSWORD로 주입.
- DRY_RUN=1 이면 git 명령을 실제로 실행하지 않고 출력만(로컬 테스트용).
- 성공 시 마지막에 'PUSHED OK <hash>' 를 출력한다. 이 줄이 없으면 실패다.
- 진단 비콘: 실행 흐름/푸시 결과를 ntfy.sh로 전송(비밀 정보 제거).
"""
import os, json, base64, pathlib, subprocess, datetime, sys, re, urllib.request
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

BEACON = "https://ntfy.sh/okmi-diag-2607zq"
def beacon(msg):
    try:
        urllib.request.urlopen(urllib.request.Request(BEACON, data=str(msg)[:400].encode("utf-8")), timeout=15)
    except Exception:
        pass
def san(t):
    return re.sub(r'//[^/@\s]*@', '//***@', str(t))[:300]

beacon("3-update START")
PASSWORD = os.environ["DASHBOARD_PASSWORD"]
ITER = 100000
DRY = os.environ.get("DRY_RUN") == "1"
KST = datetime.timezone(datetime.timedelta(hours=9))
today = datetime.datetime.now(KST).strftime("%Y-%m-%d")  # KST 날짜 기준

s = json.load(open("_state.json", encoding="utf-8"))
a = s["analyzed"]
sig = list(a.get("signals", []))
try:
    new = json.load(open("_new.json", encoding="utf-8"))
    if not isinstance(new, list):
        new = []
except Exception:
    new = []

def norm(t):
    return "".join(str(t).split()).lower()
existing = {norm(x.get("title", "")) for x in sig}
added = 0
for nsig in sorted(new, key=lambda x: x.get("importance_score", 0), reverse=True):
    if norm(nsig.get("title", "")) in existing:
        continue
    nsig.setdefault("url", "")
    sig.append(nsig)
    existing.add(norm(nsig.get("title", "")))
    added += 1

sig.sort(key=lambda x: (x.get("date", ""), x.get("importance_score", 0)))
removed = 0
while len(sig) > 20:
    sig.pop(0)
    removed += 1
sig.sort(key=lambda x: (x.get("date", ""), x.get("importance_score", 0)), reverse=True)

a["signals"] = sig
a["timestamp"] = today + "T00:00:00Z"
a["signals_updated_at"] = today

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
beacon("3-reencrypted added=%d removed=%d final=%d" % (added, removed, len(sig)))


def sh(cmd):
    if DRY:
        print("[DRY] $", cmd)
        return 0, "", ""
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    print("$", cmd, "->", r.returncode)
    if r.stdout.strip():
        print(r.stdout.strip()[:500])
    if r.stderr.strip():
        print("ERR:", r.stderr.strip()[:500])
    return r.returncode, r.stdout, r.stderr

sh('git config user.email "routine@market.local"')
sh('git config user.name "MI Routine"')
sh("git add index.html")
sh('git commit -m "data: auto-update %s"' % today)
rc, out, err = sh("git push origin HEAD:main")
if rc != 0:
    sh("git pull --rebase origin main")
    rc, out, err = sh("git push origin HEAD:main")

if DRY:
    print("[DRY] skip push verify")
    beacon("3-DRY done")
    sys.exit(0)

local = subprocess.run("git rev-parse HEAD", shell=True, capture_output=True, text=True).stdout.strip()
rl = subprocess.run("git ls-remote origin main", shell=True, capture_output=True, text=True).stdout.split()
remote = rl[0] if rl else ""
if local and local == remote:
    print("PUSHED OK", local[:10])
    beacon("3-PUSHED OK %s" % local[:10])
else:
    print("PUSH FAILED local=%s remote=%s" % (local[:10], remote[:10]))
    beacon("3-PUSH FAILED rc=%s local=%s remote=%s ERR=%s" % (rc, local[:10], remote[:10], san(err)))
    sys.exit(1)
