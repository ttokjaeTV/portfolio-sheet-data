"""
종목코드·티커 ↔ 파일명 변환 (Windows 예약어 회피)

Windows 는 CON·PRN·AUX·NUL·COM1~9·LPT1~9 를 장치 이름으로 예약해 둬서
확장자를 붙여도(CON.csv, AUX.U.csv) 파일을 만들 수 없다. 대소문자도 가리지 않는다.
GitHub Actions(리눅스)에서는 만들어지지만 Windows 에서 pull 하면
"error: invalid path 'data/history/us/CON.csv'" 로 체크아웃 전체가 멈춘다.

규칙: 첫 점(.) 앞 부분이 예약어면 그 뒤에 '_' 를 붙인다.
    CON    → CON_      → data/history/us/CON_.csv
    AUX.U  → AUX_.U    → data/history/us/AUX_.U.csv
    BRK/B  → BRK_B     (경로 구분자는 기존처럼 '_')
읽을 때는 from_filename() 으로 되돌린다. 실제 티커 중 '예약어_' 형태는 없다.

per-ticker 파일을 새로 쓰거나 읽는 코드는 반드시 이 두 함수를 거친다.
"""

RESERVED = {"CON", "PRN", "AUX", "NUL",
            *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10))}


def to_filename(code):
    """종목코드·티커 → 확장자 뺀 파일명."""
    s = str(code).replace("/", "_")
    base, dot, rest = s.partition(".")
    if base.upper() in RESERVED:
        base += "_"
    return base + dot + rest


def from_filename(stem):
    """확장자 뺀 파일명 → 종목코드·티커 (경로 구분자 '_' 는 복원하지 않는다)."""
    base, dot, rest = stem.partition(".")
    if base.endswith("_") and base[:-1].upper() in RESERVED:
        base = base[:-1]
    return base + dot + rest


if __name__ == "__main__":
    for t in ["CON", "PRN", "con", "AUX.U", "COM1", "COM", "LPT", "NULL", "BRK.B", "BRK/B", "0184E0"]:
        f = to_filename(t)
        assert from_filename(f) == t.replace("/", "_"), t
        assert f.split(".")[0].upper() not in RESERVED, t
        print(f"{t:8} → {f}")
