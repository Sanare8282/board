# -*- coding: utf-8 -*-
"""
market-board (single file)

실행:  python board.py            실제 수집
       python board.py --mock     네트워크 없이 형태 확인 (출력물에 MOCK 배너 강제)

설계 원칙 — 데이터 레이어에서 강제하는 것:
  1. 값과 전일대비는 반드시 같은 API 응답에서 뽑는다. 서로 다른 호출을 합치지 않는다.
  2. 소스 응답의 원본 기준시각(source_asof)을 그대로 싣는다. 역산하지 않는다.
  3. 실패 행에는 값을 절대 노출하지 않는다. 마지막 성공 '시각'만 남긴다.
     (캐시값이 '미확인'을 '확인'으로 둔갑시키는 경로를 원천 차단)
  4. 목업 데이터는 모든 출력물 최상단에 배너가 박힌다.
"""

import argparse, csv, io, json, os, sys
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

# ══════════════════════════════════════════════════════════════
# 1. 지표 정의
# ══════════════════════════════════════════════════════════════
KRX = ("Asia/Seoul", 900, 1530)
US = ("America/New_York", 930, 1600)
FUT = ("America/New_York", 1800, 1700)
JP = ("Asia/Tokyo", 900, 1530)
HK = ("Asia/Hong_Kong", 930, 1600)
CN = ("Asia/Shanghai", 930, 1500)
TW = ("Asia/Taipei", 900, 1330)
CME = ("America/Chicago", 1700, 1600)
DLY = ("America/New_York", 0, 0)      # 일별 확정 시계열 (장중 개념 없음)

I = lambda **k: k
INDICATORS = [
    # ── 국내지수 및 환율 (4)
    I(key="kospi",  g="국내지수 및 환율", ko="코스피 지수",  en="KOSPI",        src="yf", t="^KS11",    u="pt", dp=2, s=KRX),
    I(key="kosdaq", g="국내지수 및 환율", ko="코스닥 지수",  en="KOSDAQ",       src="yf", t="^KQ11",    u="pt", dp=2, s=KRX),
    I(key="dxy",    g="국내지수 및 환율", ko="달러 인덱스",  en="DOLLAR INDEX", src="yf", t="DX-Y.NYB", u="pt", dp=2, s=FUT),
    I(key="usdkrw", g="국내지수 및 환율", ko="원/달러 환율", en="USD/KRW",      src="yf", t="KRW=X",    u="원", dp=2, s=FUT),

    # ── 미국지수 (8)
    I(key="ixic", g="미국지수", ko="나스닥 지수",       en="NASDAQ COMPOSITE",     src="yf", t="^IXIC", u="pt", dp=2, s=US),
    I(key="nq",   g="미국지수", ko="나스닥 100 선물",   en="NASDAQ 100 FUTURES",   src="yf", t="NQ=F",  u="pt", dp=2, s=CME),
    I(key="spx",  g="미국지수", ko="S&P 500",           en="S&P 500",              src="yf", t="^GSPC", u="pt", dp=2, s=US),
    I(key="es",   g="미국지수", ko="S&P 500 선물",      en="S&P 500 FUTURES",      src="yf", t="ES=F",  u="pt", dp=2, s=CME),
    I(key="rty",  g="미국지수", ko="러셀 2000 선물",    en="RUSSELL 2000 FUTURES", src="yf", t="RTY=F", u="pt", dp=2, s=CME),
    I(key="dji",  g="미국지수", ko="다우존스",          en="DOW JONES",            src="yf", t="^DJI",  u="pt", dp=2, s=US),
    I(key="sox",  g="미국지수", ko="필라델피아 반도체", en="PHLX SEMICONDUCTOR",   src="yf", t="^SOX",  u="pt", dp=2, s=US),
    I(key="vix",  g="미국지수", ko="VIX 변동성 지수",   en="VIX INDEX",            src="yf", t="^VIX",  u="pt", dp=2, s=US),

    # ── 아시아지수 (4)
    I(key="n225", g="아시아지수", ko="니케이 225 지수", en="NIKKEI 225",         src="yf", t="^N225",     u="pt", dp=2, s=JP),
    I(key="hsi",  g="아시아지수", ko="항셍 지수",       en="HANG SENG",          src="yf", t="^HSI",      u="pt", dp=2, s=HK),
    I(key="sse",  g="아시아지수", ko="상해종합 지수",   en="SHANGHAI COMPOSITE", src="yf", t="000001.SS", u="pt", dp=2, s=CN),
    I(key="twii", g="아시아지수", ko="대만 가권 지수",  en="TAIWAN WEIGHTED",    src="yf", t="^TWII",     u="pt", dp=2, s=TW),

    # ── 한국 국채금리 (6) · ECOS_KEY 필요
    I(key="kr2y",  g="한국 국채금리", ko="한국 국채 2년",  en="KR BOND 2Y",  src="ecos", t="010200000", u="%", dp=2, s=DLY),
    I(key="kr3y",  g="한국 국채금리", ko="한국 국채 3년",  en="KR BOND 3Y",  src="ecos", t="010200001", u="%", dp=2, s=DLY),
    I(key="kr5y",  g="한국 국채금리", ko="한국 국채 5년",  en="KR BOND 5Y",  src="ecos", t="010200002", u="%", dp=2, s=DLY),
    I(key="kr10y", g="한국 국채금리", ko="한국 국채 10년", en="KR BOND 10Y", src="ecos", t="010210000", u="%", dp=2, s=DLY),
    I(key="kr20y", g="한국 국채금리", ko="한국 국채 20년", en="KR BOND 20Y", src="ecos", t="010220000", u="%", dp=2, s=DLY),
    I(key="kr30y", g="한국 국채금리", ko="한국 국채 30년", en="KR BOND 30Y", src="ecos", t="010230000", u="%", dp=2, s=DLY),

    # ── 미국 국채금리 (4)
    I(key="us2y",  g="미국 국채금리", ko="미국 국채 2년",  en="US TREASURY 2Y",  src="fred", t="DGS2", u="%", dp=2, s=DLY),
    I(key="us5y",  g="미국 국채금리", ko="미국 국채 5년",  en="US TREASURY 5Y",  src="yf", t="^FVX", u="%", dp=2, s=US),
    I(key="us10y", g="미국 국채금리", ko="미국 국채 10년", en="US TREASURY 10Y", src="yf", t="^TNX", u="%", dp=2, s=US),
    I(key="us30y", g="미국 국채금리", ko="미국 국채 30년", en="US TREASURY 30Y", src="yf", t="^TYX", u="%", dp=2, s=US),

    # ── 실물원자재 (7) · 브렌트 추가
    I(key="brent",  g="실물원자재", ko="브렌트유",  en="BRENT CRUDE",    src="yf", t="BZ=F", u="$/bbl",   dp=2, s=CME),
    I(key="wti",    g="실물원자재", ko="WTI",       en="WTI CRUDE OIL",  src="yf", t="CL=F", u="$/bbl",   dp=2, s=CME),
    I(key="gold",   g="실물원자재", ko="금",        en="GOLD",           src="yf", t="GC=F", u="$/oz",    dp=2, s=CME),
    I(key="silver", g="실물원자재", ko="은",        en="SILVER",         src="yf", t="SI=F", u="$/oz",    dp=2, s=CME),
    I(key="natgas", g="실물원자재", ko="천연가스",  en="NATURAL GAS",    src="yf", t="NG=F", u="$/MMBtu", dp=3, s=CME),
    I(key="copper", g="실물원자재", ko="구리",      en="COPPER",         src="yf", t="HG=F", u="$/lb",    dp=3, s=CME),
    I(key="wheat",  g="실물원자재", ko="밀",        en="WHEAT",          src="yf", t="ZW=F", u="$/bu",    dp=2, s=CME),

    # ── 야간선물 (2) · 무료 소스 없음. 임의 대체하지 않는다.
    I(key="k200n",  g="야간선물", ko="코스피200 야간",  en="KOSPI 200 NIGHT",  src="none", t="", u="pt", dp=2, s=KRX,
      note="무료 공개 소스 없음. 증권사 API 연결 시에만 채워짐"),
    I(key="kq150n", g="야간선물", ko="코스닥150 야간",  en="KOSDAQ 150 NIGHT", src="none", t="", u="pt", dp=2, s=KRX,
      note="무료 공개 소스 없음. 증권사 API 연결 시에만 채워짐"),

    # ── 킬스위치 원지표 (5)
    I(key="move",   g="킬스위치 원지표", ko="MOVE 채권변동성", en="MOVE INDEX",   src="yf",   t="^MOVE",          u="pt", dp=2, s=US,
      note="ICE 독점 지수. 무료 경로가 불안정해 실패 가능. 실패 시 '미확인'이며 '미점등' 아님"),
    I(key="hyoas",  g="킬스위치 원지표", ko="HY 스프레드(OAS)", en="ICE BofA HY OAS", src="fred", t="BAMLH0A0HYM2", u="%p", dp=2, s=DLY),
    I(key="igoas",  g="킬스위치 원지표", ko="IG 스프레드(OAS)", en="ICE BofA IG OAS", src="fred", t="BAMLC0A0CM",   u="%p", dp=2, s=DLY),
    I(key="sofr",   g="킬스위치 원지표", ko="SOFR",             en="SOFR",            src="fred", t="SOFR",         u="%",  dp=2, s=DLY),
    I(key="iorb",   g="킬스위치 원지표", ko="IORB",             en="IORB",            src="fred", t="IORB",         u="%",  dp=2, s=DLY),

    # ── 신용 온도계 대리지표 (3)
    I(key="hyg", g="신용 온도계(대리)", ko="하이일드 회사채 ETF", en="HYG", src="yf", t="HYG", u="$", dp=2, s=US),
    I(key="ief", g="신용 온도계(대리)", ko="미국채 7-10년 ETF",   en="IEF", src="yf", t="IEF", u="$", dp=2, s=US),
    I(key="kre", g="신용 온도계(대리)", ko="지역은행 ETF",        en="KRE", src="yf", t="KRE", u="$", dp=2, s=US),
]

GROUP_ORDER = ["국내지수 및 환율", "미국지수", "아시아지수", "한국 국채금리",
               "미국 국채금리", "실물원자재", "야간선물", "킬스위치 원지표", "신용 온도계(대리)"]
SRC_LABEL = {"yf": "Yahoo Finance", "fred": "FRED (St. Louis Fed)",
             "ecos": "한국은행 ECOS", "none": "-"}


# ══════════════════════════════════════════════════════════════
# 2. 수집
# ══════════════════════════════════════════════════════════════
def judge_basis(session, bar_date):
    """마지막 봉 날짜가 시장 현지 '오늘'이고 거래시간 안이면 장중.
    휴장일 정보가 없으므로 이 판정은 추정이다."""
    tzname, o, c = session
    if o == 0 and c == 0:
        return "일별확정"
    if ZoneInfo is None or not bar_date:
        return "종가확정"
    now = datetime.now(ZoneInfo(tzname))
    if str(bar_date) != now.strftime("%Y-%m-%d"):
        return "종가확정"
    hhmm = now.hour * 100 + now.minute
    inside = (o <= hhmm <= c) if o <= c else (hhmm >= o or hhmm <= c)
    return "장중" if inside else "종가확정"


def fetch_yf(spec):
    """단일 응답에서만 값·전일대비를 뽑는다. fast_info 등 별도 호출을 섞지 않는다."""
    import yfinance as yf
    hist = yf.Ticker(spec["t"]).history(period="10d", interval="1d", auto_adjust=False)
    if hist is None or len(hist) < 2:
        raise RuntimeError("일봉 2개 미만")
    rows = [(ix, float(v)) for ix, v in zip(hist.index, hist["Close"]) if v == v]
    if len(rows) < 2:
        raise RuntimeError("유효 종가 2개 미만")
    (ts, val), (_, prev) = rows[-1], rows[-2]
    return val, prev, ts.strftime("%Y-%m-%d"), ts.isoformat()


def fetch_fred(spec):
    import urllib.request
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={spec['t']}"
    req = urllib.request.Request(url, headers={"User-Agent": "market-board/2.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        rows = list(csv.reader(io.StringIO(r.read().decode("utf-8"))))
    body = [x for x in rows[1:] if len(x) >= 2 and x[1] not in (".", "")]
    if len(body) < 2:
        raise RuntimeError("유효 관측치 2개 미만")
    return float(body[-1][1]), float(body[-2][1]), body[-1][0], body[-1][0]


def fetch_ecos(spec):
    import urllib.request
    key = os.environ.get("ECOS_KEY", "").strip()
    if not key:
        raise RuntimeError("ECOS_KEY 미설정")
    end = datetime.now(KST); start = end - timedelta(days=25)
    url = (f"https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/50/817Y002/D/"
           f"{start:%Y%m%d}/{end:%Y%m%d}/{spec['t']}")
    req = urllib.request.Request(url, headers={"User-Agent": "market-board/2.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode("utf-8"))
    if "StatisticSearch" not in data:
        raise RuntimeError(f"ECOS 응답 이상: {str(data)[:100]}")
    rows = [x for x in data["StatisticSearch"]["row"] if x.get("DATA_VALUE")]
    if len(rows) < 2:
        raise RuntimeError("유효 관측치 2개 미만")
    d = rows[-1]["TIME"]; iso = f"{d[0:4]}-{d[4:6]}-{d[6:8]}"
    return float(rows[-1]["DATA_VALUE"]), float(rows[-2]["DATA_VALUE"]), iso, iso


def fetch_none(spec):
    raise RuntimeError(spec.get("note", "지원 소스 없음"))


FETCH = {"yf": fetch_yf, "fred": fetch_fred, "ecos": fetch_ecos, "none": fetch_none}

# 목업: 시장별로 물리적으로 가능한 날짜/변화율만 생성한다.
# (v1의 목업이 전 종목에 오늘 날짜 + 랜덤 등락률을 박아 '불가능한 데이터'를 만들었던 것을 교정)
MOCK = {"kospi": 5663.24, "kosdaq": 662.68, "dxy": 101.33, "usdkrw": 1446.98,
        "ixic": 24876.91, "nq": 27818.0, "spx": 7428.78, "es": 7462.25, "rty": 2963.5,
        "dji": 52747.32, "sox": 11035.68, "vix": 18.21, "n225": 61429.62, "hsi": 25694.07,
        "sse": 3820.64, "twii": 40039.18, "kr2y": 3.71, "kr3y": 3.83, "kr5y": 4.07,
        "kr10y": 4.29, "kr20y": 4.53, "kr30y": 4.54, "us2y": 4.25, "us5y": 4.36,
        "us10y": 4.60, "us30y": 5.10, "brent": 86.10, "wti": 82.35, "gold": 4039.20,
        "silver": 58.12, "natgas": 2.70, "copper": 6.33, "wheat": 6.70, "move": 118.4,
        "hyoas": 3.42, "igoas": 1.05, "sofr": 4.33, "iorb": 4.40,
        "hyg": 78.90, "ief": 94.10, "kre": 72.30}


def fetch_mock(spec):
    if spec["src"] == "none":
        raise RuntimeError(spec.get("note", "지원 소스 없음"))
    if spec["src"] == "ecos" and not os.environ.get("ECOS_KEY"):
        raise RuntimeError("ECOS_KEY 미설정")
    tzname, o, c = spec["s"]
    now = datetime.now(ZoneInfo(tzname)) if ZoneInfo else datetime.now(KST)
    hhmm = now.hour * 100 + now.minute
    d = now
    if o == 0 and c == 0:          # 일별 확정 시계열은 항상 전 영업일
        d -= timedelta(days=1)
    elif o <= c and hhmm < o:      # 개장 전이면 직전 영업일 봉이 최신
        d -= timedelta(days=1)
    while d.weekday() >= 5:        # 주말 보정
        d -= timedelta(days=1)
    base = MOCK.get(spec["key"], 100.0)
    return base, round(base / 1.004, 6), d.strftime("%Y-%m-%d"), d.strftime("%Y-%m-%d")


def collect(mock, prev_state):
    now = datetime.now(KST); out = []
    for sp in INDICATORS:
        old = prev_state.get(sp["key"], {})
        r = dict(key=sp["key"], group=sp["g"], ko=sp["ko"], en=sp["en"], unit=sp["u"],
                 dp=sp["dp"], source=SRC_LABEL[sp["src"]], ticker=sp["t"],
                 fetched_at=now.isoformat(timespec="seconds"))
        if sp.get("note"):
            r["note"] = sp["note"]
        try:
            val, prev, bar, asof = (fetch_mock if mock else FETCH[sp["src"]])(sp)
            r.update(status="정상", value=round(val, sp["dp"]), prev_close=round(prev, sp["dp"]),
                     change=round(val - prev, sp["dp"]),
                     change_pct=round((val - prev) / prev * 100, 2) if prev else None,
                     basis=judge_basis(sp["s"], bar), basis_date=bar, source_asof=asof)
        except Exception as e:
            # 실패 행에는 값을 절대 넣지 않는다. 마지막 성공 '시각'만 남긴다.
            r.update(status="수집실패", value=None, prev_close=None, change=None,
                     change_pct=None, basis=None, basis_date=None, source_asof=None,
                     fail_reason=str(e)[:180],
                     last_success_at=(old.get("fetched_at") if old.get("status") == "정상"
                                      else old.get("last_success_at")))
        out.append(r)
    return out


def derive(rows):
    """파생 지표. 원본이 아니므로 basis='파생'으로 못박는다."""
    by = {r["key"]: r for r in rows}; out = []

    def mk(key, ko, en, unit, dp, a, b, fn, note):
        base = dict(key=key, group="킬스위치 원지표" if "SOFR" in en else "신용 온도계(대리)",
                    ko=ko, en=en, unit=unit, dp=dp, ticker="", source="파생 계산 (원본 지표 아님)",
                    fetched_at=datetime.now(KST).isoformat(timespec="seconds"), note=note)
        x, y = by.get(a), by.get(b)
        if x and y and x["status"] == "정상" and y["status"] == "정상":
            c, p = fn(x["value"], y["value"]), fn(x["prev_close"], y["prev_close"])
            base.update(status="정상(파생)", value=round(c, dp), prev_close=round(p, dp),
                        change=round(c - p, dp),
                        change_pct=round((c - p) / p * 100, 2) if p else None,
                        basis="파생", basis_date=x["basis_date"], source_asof=x.get("source_asof"))
        else:
            base.update(status="수집실패", value=None, prev_close=None, change=None,
                        change_pct=None, basis=None, basis_date=None, source_asof=None,
                        fail_reason=f"원본 {a.upper()} 또는 {b.upper()} 수집 실패")
        return base

    out.append(mk("sofr_iorb", "SOFR−IORB 스프레드", "SOFR MINUS IORB", "bp", 1,
                  "sofr", "iorb", lambda a, b: (a - b) * 100,
                  "자금시장 경색 대리. 확대 = 달러 조달 압박"))
    out.append(mk("hyg_ief", "HYG/IEF 비율", "HYG/IEF RATIO", "배", 4,
                  "hyg", "ief", lambda a, b: a / b,
                  "실제 스프레드가 아닌 ETF 가격비. 원지표 HY OAS가 있으면 그쪽이 우선"))
    return out


# ══════════════════════════════════════════════════════════════
# 3. 렌더
# ══════════════════════════════════════════════════════════════
def fmt(r):
    return "—" if r["value"] is None else f"{r['value']:,.{r['dp']}f}"


def fmt_chg(r):
    if r["change"] is None:
        return "—"
    p = r["change_pct"]
    ps = f"{'+' if p and p > 0 else ''}{p:.2f}%" if p is not None else "—"
    return f"{'+' if r['change'] > 0 else ''}{r['change']:,.{r['dp']}f} ({ps})"


MOCK_BANNER = ("데이터종류: **모의값(MOCK)** — 실제 시세가 아닙니다. "
               "형태 확인 전용이며 매매 판단에 사용 금지.")


def render_txt(rows, gen, mock):
    ok = [r for r in rows if r["status"].startswith("정상")]
    bad = [r for r in rows if r["status"] == "수집실패"]
    L = []
    if mock:
        L += ["=" * 60, MOCK_BANNER, "=" * 60, ""]
    L += ["# 시장 지표 스냅샷", "",
          f"- 생성시각: **{gen.isoformat(timespec='seconds')}** (Asia/Seoul, KST)",
          f"- 데이터종류: {'모의값(MOCK)' if mock else '실수집'}",
          f"- 성공 {len(ok)} / 실패 {len(bad)} / 전체 {len(rows)}", "",
          "## 이 문서를 읽는 규칙", "",
          "1. **생성시각이 이 데이터의 기준시각이다.** 읽는 시점 날짜로 대체하지 말 것. "
          "오래됐으면 그 사실을 먼저 밝힐 것.",
          "2. `기준`이 **장중**이면 확정치가 아니다. 이 값 위의 판단은 잠정으로 표시할 것.",
          "3. **값이 비어 있으면 '확인 못 함'이다.** 0도 아니고 무변동도 아니다. "
          "빈 항목을 다른 항목으로 메우지 말 것.",
          "4. 실패 항목에는 마지막 성공 '시각'만 있고 값은 없다. 의도된 설계다.",
          "5. `기준일`과 `소스기준시각`이 다르면 소스기준시각이 원본이다. 역산값을 믿지 말 것.",
          "6. 장중/종가 판정은 거래시간 기준 **추정**이며 휴장일을 반영하지 않는다.", "",
          "## 수집 실패 목록", ""]
    if not bad:
        L.append("없음.")
    else:
        L += ["| 지표 | 사유 | 마지막 성공 시각 |", "|---|---|---|"]
        for r in bad:
            L.append(f"| {r['ko']} | {r.get('fail_reason','-')} | {r.get('last_success_at') or '기록 없음'} |")
    L.append("")
    for g in GROUP_ORDER:
        grp = [r for r in rows if r["group"] == g]
        if not grp:
            continue
        L += [f"## {g}", "",
              "| 지표 | 값 | 전일대비 | 기준 | 기준일 | 소스기준시각 | 출처 | 상태 |",
              "|---|---|---|---|---|---|---|---|"]
        for r in grp:
            L.append(f"| {r['ko']} ({r['en']}) | {fmt(r)} {r['unit'] if r['value'] is not None else ''} "
                     f"| {fmt_chg(r)} | {r['basis'] or '—'} | {r['basis_date'] or '—'} "
                     f"| {r.get('source_asof') or '—'} | {r['source']} | {r['status']} |")
        L.append("")
        for r in grp:
            if r.get("note"):
                L.append(f"> {r['ko']}: {r['note']}")
        L.append("")
    L += ["---", "", "자동 수집 · 사람 검수 없음. 매매 판단 전 원본 시세를 다시 확인할 것."]
    return "\n".join(L)


CSS = """
:root{--bg:#0b1020;--panel:#121a30;--line:#1e2a47;--ink:#e8edf8;--dim:#7f8db0;
--up:#ff5f6d;--dn:#4c9bff;--stamp:#f0b429}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);padding:20px 14px 60px;
font-family:"Pretendard","Apple SD Gothic Neo","Noto Sans KR",system-ui,sans-serif}
.wrap{max-width:1180px;margin:0 auto}
h1{font-size:19px;margin:0 0 10px}
.meta{font:12px/1.7 ui-monospace,Menlo,monospace;color:var(--dim)}
.meta b{color:var(--stamp)}
header{border-bottom:1px solid var(--line);padding-bottom:14px}
.mock{background:#3a1d0a;border:1px solid #a55a12;color:#ffcf8a;border-radius:10px;
padding:12px 14px;margin-bottom:16px;font:13px/1.6 ui-monospace,monospace}
.links{margin:14px 0;display:flex;gap:8px;flex-wrap:wrap}
.links a{background:var(--panel);border:1px solid var(--line);border-radius:8px;
padding:9px 13px;font:12px ui-monospace,monospace;color:var(--stamp);text-decoration:none}
.alert{margin:16px 0;border:1px solid #5c2b2b;background:#1d1013;border-radius:10px;padding:12px 14px}
.alert h2{font-size:13px;margin:0 0 8px;color:#ff8f8f}
.alert li{font:12px/1.8 ui-monospace,monospace;color:#d9b0b0}
h2.grp{font-size:12px;letter-spacing:.14em;color:var(--dim);margin:32px 0 11px;font-weight:600}
.grid{display:grid;gap:10px;grid-template-columns:repeat(auto-fill,minmax(262px,1fr))}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 15px 11px}
.card.fail{background:repeating-linear-gradient(135deg,#141826,#141826 8px,#101422 8px,#101422 16px);
border-style:dashed;border-color:#3a2733}
.nm{font-size:13px;font-weight:600}
.tk{font:10px ui-monospace,monospace;color:var(--dim);letter-spacing:.08em;margin-top:2px}
.val{font:600 25px/1.15 ui-monospace,Menlo,monospace;margin:12px 0 2px;font-variant-numeric:tabular-nums}
.val .u{font-size:12px;color:var(--dim);font-weight:400;margin-left:4px}
.chg{font:12px ui-monospace,monospace;font-variant-numeric:tabular-nums}
.up{color:var(--up)}.dn{color:var(--dn)}.flat{color:var(--dim)}
.stamp{margin-top:11px;padding-top:9px;border-top:1px dashed var(--line);
font:10px/1.6 ui-monospace,monospace;color:var(--dim);display:flex;justify-content:space-between;
gap:8px;flex-wrap:wrap}
.badge{border:1px solid currentColor;border-radius:4px;padding:0 5px;font-size:9px}
.b-live{color:var(--stamp)}.b-fix{color:#4fb286}.b-drv{color:#9b8cff}
.failmsg{font:11px/1.6 ui-monospace,monospace;color:#c78b8b;margin-top:12px}
.failmsg .lbl{color:#ff8f8f}
footer{margin-top:38px;padding-top:14px;border-top:1px solid var(--line);
font:11px/1.8 ui-monospace,monospace;color:var(--dim)}
@media(max-width:520px){.grid{grid-template-columns:1fr}.val{font-size:23px}}
"""


def render_html(rows, gen, mock):
    bad = [r for r in rows if r["status"] == "수집실패"]
    H = ['<!doctype html><html lang="ko"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         f'<title>시장 지표 · {gen:%m-%d %H:%M} KST</title><style>{CSS}</style>',
         "</head><body><div class='wrap'>"]
    if mock:
        H.append(f"<div class='mock'>{MOCK_BANNER.replace('**','')}</div>")
    H.append("<header><h1>시장 지표 보드</h1><div class='meta'>")
    H.append(f"기준시각 <b>{gen:%Y-%m-%d %H:%M:%S}</b> KST · ")
    H.append(f"데이터 {'모의값(MOCK)' if mock else '실수집'} · ")
    H.append(f"성공 {len(rows)-len(bad)} / 실패 {len(bad)}<br>")
    H.append("모든 숫자에 출처·기준·소스기준시각이 붙습니다. 빈 카드는 확인하지 못한 항목입니다.")
    H.append("</div><div class='links'><a href='./now.txt'>LLM용 now.txt</a>"
             "<a href='./now.json'>now.json</a></div></header>")
    if bad:
        H.append("<div class='alert'><h2>확인하지 못한 항목</h2><ul>")
        for r in bad:
            H.append(f"<li>{r['ko']} — {r.get('fail_reason','사유 미상')}</li>")
        H.append("</ul></div>")
    for g in GROUP_ORDER:
        grp = [r for r in rows if r["group"] == g]
        if not grp:
            continue
        H.append(f"<h2 class='grp'>{g}</h2><div class='grid'>")
        for r in grp:
            if r["value"] is None:
                H.append(f"<div class='card fail'><div class='nm'>{r['ko']}</div>"
                         f"<div class='tk'>{r['en']}</div><div class='failmsg'>"
                         f"<span class='lbl'>확인 불가</span> — {r.get('fail_reason','사유 미상')}")
                if r.get("last_success_at"):
                    H.append(f"<br>마지막 성공 {r['last_success_at']}")
                H.append("</div></div>")
                continue
            c = "flat" if r["change"] == 0 else ("up" if r["change"] > 0 else "dn")
            ar = "▲" if r["change"] > 0 else ("▼" if r["change"] < 0 else "―")
            bc = {"장중": "b-live", "종가확정": "b-fix", "일별확정": "b-fix"}.get(r["basis"], "b-drv")
            H.append(f"<div class='card'><div class='nm'>{r['ko']}</div><div class='tk'>{r['en']}</div>"
                     f"<div class='val'>{fmt(r)}<span class='u'>{r['unit']}</span></div>"
                     f"<div class='chg {c}'>{ar} {fmt_chg(r)}</div>"
                     f"<div class='stamp'><span class='badge {bc}'>{r['basis']}</span>"
                     f"<span>{r['source']} · {r.get('source_asof') or r['basis_date']}</span></div></div>")
        H.append("</div>")
    H.append("<footer>자동 수집 · 사람 검수 없음. 매매 판단 전 원본 시세를 다시 확인하세요.</footer>")
    H.append("</div></body></html>")
    return "".join(H)


# ══════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--out", default="docs")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    prev = {}
    p = os.path.join(a.out, "now.json")
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            # 목업 결과는 승계하지 않는다.
            if not d.get("mock"):
                prev = {r["key"]: r for r in d["indicators"]}
        except Exception as e:
            print(f"[warn] 이전 상태 무시: {e}", file=sys.stderr)

    gen = datetime.now(KST)
    rows = collect(a.mock, prev)
    rows += derive(rows)

    payload = json.dumps({
        "generated_at": gen.isoformat(timespec="seconds"), "timezone": "Asia/Seoul",
        "mock": a.mock,
        "reading_rules": [
            "generated_at이 이 데이터의 기준시각이다. 읽는 시점 날짜로 대체하지 말 것.",
            "basis가 '장중'이면 확정치가 아니다.",
            "value가 null이면 '확인 못 함'이며 0이나 무변동이 아니다.",
            "실패 행에는 값이 없다. last_success_at은 고장 지속기간 참고용일 뿐 현재값이 아니다.",
        ],
        "count_total": len(rows),
        "count_ok": len([r for r in rows if r["status"].startswith("정상")]),
        "count_failed": len([r for r in rows if r["status"] == "수집실패"]),
        "indicators": rows,
    }, ensure_ascii=False, indent=1)

    for n, t in (("now.txt", render_txt(rows, gen, a.mock)),
                 ("now.json", payload),
                 ("index.html", render_html(rows, gen, a.mock))):
        with open(os.path.join(a.out, n), "w", encoding="utf-8") as f:
            f.write(t)
    open(os.path.join(a.out, ".nojekyll"), "w").close()

    ok = len([r for r in rows if r["status"].startswith("정상")])
    print(f"[done] {gen:%Y-%m-%d %H:%M:%S} KST · {'MOCK · ' if a.mock else ''}"
          f"성공 {ok} · 실패 {len(rows)-ok} · -> {a.out}/")
    for r in rows:
        if r["status"] == "수집실패":
            print(f"  - 실패 {r['ko']}: {r.get('fail_reason')}")


if __name__ == "__main__":
    main()
