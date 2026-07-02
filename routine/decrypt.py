"""1단계: index.html의 암호화 블록을 복호화해 _state.json으로 저장.
PASSWORD는 환경변수 DASHBOARD_PASSWORD로 주입(레포에 비밀번호 저장 안 함)."""
import json, base64, pathlib, os
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
print("decrypted signals:", len(state["analyzed"]["signals"]))
