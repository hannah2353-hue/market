"""1단계: index.html의 암호화 블록을 복호화해 _state.json으로 저장.
PASSWORD는 환경변수 DASHBOARD_PASSWORD로 주입(레포에 비밀번호 저장 안 함).
진단 비콘: 실행 흐름을 ntfy.sh로 전송(비밀 정보 없음)."""
import json, base64, pathlib, os, urllib.request

BEACON = "https://ntfy.sh/okmi-diag-2607zq"
def beacon(msg):
    try:
        urllib.request.urlopen(urllib.request.Request(BEACON, data=str(msg)[:400].encode("utf-8")), timeout=15)
    except Exception:
        pass

beacon("1-decrypt START")
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

PASSWORD = os.environ["DASHBOARD_PASSWORD"]
html = pathlib.Path("index.html").read_text(encoding="utf-8")
open_tag = '<script id="encrypted-blob" type="application/json">'
i = html.index(open_tag) + len(open_tag)
j = html.index("</script>", i)
blob = json.loads(html[i:j])
key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                 salt=base64.b64decode(blob["salt"]),
                 iterations=blob["iterations"]).derive(PASSWORD.encode())
state = json.loads(AESGCM(key).decrypt(base64.b64decode(blob["iv"]),
                                        base64.b64decode(blob["ciphertext"]), None).decode())
pathlib.Path("_state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
n = len(state["analyzed"]["signals"])
beacon("1-decrypt OK signals=%d" % n)
print("decrypted signals:", n)
