"""수집 단계가 끝난 뒤(시간 초과 포함) 커밋 전에 한 번 돌린다.

1) 시간 초과 등으로 끊긴 출처는 status.json에 '수집 실패(직전 값 표시)'로 남긴다.
   (끊긴 단계는 status.json을 못 쓰고 죽으므로, 그대로 두면 화면에 정상처럼 보인다)
2) 쓰다가 끊겨 깨진 JSON이 있으면 직전 커밋 상태로 되돌린다. 깨진 파일이 배포되면 화면 전체가 안 뜬다.

사용: python collector/finalize.py [시간 초과된 출처 이름 ...]
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "site" / "data"


def restore_broken() -> None:
    for f in sorted([*DATA.glob("*.json"), *(ROOT / "cache").glob("*.json")]):
        try:
            json.loads(f.read_text())
        except Exception as e:  # noqa: BLE001
            print(f"깨진 파일 되돌림: {f.relative_to(ROOT)} ({e})", flush=True)
            subprocess.run(["git", "checkout", "--", str(f.relative_to(ROOT))], cwd=ROOT, check=False)


def mark_timeouts(names: list[str]) -> None:
    if not names:
        return
    st_f = DATA / "status.json"
    st = json.loads(st_f.read_text()) if st_f.exists() else {"sources": {}}
    for k in names:
        prev = st.setdefault("sources", {}).get(k, {})
        st["sources"][k] = {**prev, "ok": False, "error": "이번 수집이 끝까지 진행되지 못함(원출처 응답 지연 등) · 직전 값 표시"}
        print("시간 초과 표시:", k, flush=True)
    st_f.write_text(json.dumps(st, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    restore_broken()
    mark_timeouts([x for x in sys.argv[1:] if x.strip()])
