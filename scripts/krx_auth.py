#!/usr/bin/env python3
"""KRX 로그인 자격증명 로더.

data.krx.co.kr 은 2026년부터 전면 로그인제다. `MDC01`(메인·쉽게보는통계)을 뺀
모든 통계가 로그인을 요구하고, 비로그인 요청은 400 이나 빈 응답으로 돌아온다.
pykrx 는 `KRX_ID` / `KRX_PW` 환경변수가 있으면 알아서 로그인하므로,
이 모듈은 **자격증명을 환경변수에 넣어주는 일만** 한다.

사용법
------
    import krx_auth
    krx_auth.ensure()            # 없으면 False, 있으면 True
    from pykrx import stock      # 이 다음부터 로그인 필요한 API 가 열린다

찾는 순서
---------
  1. 이미 설정된 환경변수 KRX_ID / KRX_PW      (GitHub Actions 는 여기)
  2. 환경변수 KRX_ENV_FILE 이 가리키는 파일
  3. 아래 CANDIDATES 의 krx.env

자격증명 파일 형식 (KEY=VALUE, `#` 주석·빈 줄 허용)
--------------------------------------------------
    KRX_ID=아이디
    KRX_PW=비밀번호

★ 이 모듈은 값을 절대 출력하지 않는다. 로그에는 성공/실패와 '어느 경로에서
  읽었는지'만 남긴다. 비밀번호가 CI 로그나 대화에 남으면 안 되기 때문이다.
★ 자격증명 파일은 git 에 올리지 않는다. .gitignore 에 `.secrets/`, `*.env` 가
  들어 있는지 반드시 확인할 것.
"""

import glob
import os
import sys

FILENAME = "krx.env"

# 윈도우에서 직접 돌릴 때와 Cowork 리눅스 샌드박스에서 돌릴 때 경로가 다르다.
# 샌드박스는 연결한 폴더를 /sessions/<이름>/mnt/ 아래에 붙이므로 glob 로 훑는다.
CANDIDATES = [
    os.path.join(os.path.dirname(__file__), "..", ".secrets", FILENAME),
    os.path.expanduser(f"~/Desktop/Claude/.secrets/{FILENAME}"),
    f"C:/Users/이상준/Desktop/Claude/.secrets/{FILENAME}",
    f"/sessions/*/mnt/.secrets/{FILENAME}",
    f"/sessions/*/mnt/*/.secrets/{FILENAME}",
    f"/sessions/*/mnt/*/{FILENAME}",
]


def _expand(paths):
    for p in paths:
        if "*" in p:
            yield from sorted(glob.glob(p))
        else:
            yield p


def find_env_file():
    """자격증명 파일 경로를 찾는다. 못 찾으면 None."""
    named = os.getenv("KRX_ENV_FILE")
    if named and os.path.isfile(named):
        return named
    for p in _expand(CANDIDATES):
        if os.path.isfile(p):
            return os.path.normpath(p)
    return None


def load_env_file(path):
    """KEY=VALUE 파일을 읽어 os.environ 에 넣는다. 값은 반환하지 않는다."""
    n = 0
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip("'").strip('"')
            if k and v:
                os.environ.setdefault(k, v)
                n += 1
    return n


def ensure(verbose=True):
    """KRX_ID / KRX_PW 를 환경에 준비한다. 준비되면 True."""
    if os.getenv("KRX_ID") and os.getenv("KRX_PW"):
        if verbose:
            print("KRX 자격증명: 환경변수에서 확인", file=sys.stderr)
        return True

    path = find_env_file()
    if not path:
        if verbose:
            print("KRX 자격증명을 찾지 못했습니다.", file=sys.stderr)
            print(f"  {FILENAME} 을 아래 중 한 곳에 두거나 KRX_ENV_FILE 로 경로를 알려주세요:",
                  file=sys.stderr)
            for p in CANDIDATES[:3]:
                print(f"    {os.path.normpath(p)}", file=sys.stderr)
        return False

    load_env_file(path)
    ok = bool(os.getenv("KRX_ID") and os.getenv("KRX_PW"))
    if verbose:
        # 경로만 남긴다. 값은 절대 찍지 않는다.
        print(f"KRX 자격증명: {'로드 완료' if ok else 'KRX_ID/KRX_PW 없음'} ← {path}",
              file=sys.stderr)
    return ok


def login(verbose=True):
    """자격증명을 준비하고 실제로 로그인까지 시도한다.

    pykrx 의 login_krx 는 CD010(비밀번호 변경 필요)·CD011(중복 로그인)까지
    처리한다. 성공하면 True."""
    if not ensure(verbose):
        return False

    # pykrx 는 로그인 과정에서 아이디를 stdout 에 그대로 찍는다.
    # ★ import 시점에도 자동 로그인이 한 번 돌아 또 찍히므로,
    #   import 까지 통째로 리다이렉트 안에 넣어야 계정이 새지 않는다.
    import io
    import contextlib
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            from pykrx.website.comm.auth import login_krx
            ok = login_krx(os.environ["KRX_ID"], os.environ["KRX_PW"])
    except ImportError:
        if verbose:
            print("pykrx 가 없습니다. pip install pykrx", file=sys.stderr)
        return False
    except Exception as e:
        if verbose:
            print(f"KRX 로그인 중 오류: {type(e).__name__}", file=sys.stderr)
        return False

    out = buf.getvalue()
    if verbose:
        # 아이디·비밀번호가 섞인 줄은 버리고, 조치가 필요한 안내만 남긴다
        for line in out.splitlines():
            if any(k in line for k in ("로그인 ID", "ID:", "PW", "패스워드 변경 필요")):
                continue
            if line.strip() and ("변경" in line or "http" in line or "오류" in line):
                print("  " + line.strip(), file=sys.stderr)
        if not ok and "비밀번호 변경" in out:
            print("KRX 로그인 실패: 비밀번호 변경이 필요합니다 (CD010).", file=sys.stderr)
            print("  https://data.krx.co.kr 에서 비밀번호를 바꾼 뒤 krx.env 도 같이 고쳐 주세요.",
                  file=sys.stderr)
        else:
            print(f"KRX 로그인: {'성공' if ok else '실패'}", file=sys.stderr)
    return ok


if __name__ == "__main__":
    # 점검용. 성공/실패만 찍고 값은 노출하지 않는다.
    sys.exit(0 if login() else 1)
