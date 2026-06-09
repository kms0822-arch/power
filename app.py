import streamlit as st
import requests
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET
import math

# ─────────────────────────────────────────────
# 페이지 설정
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="⚡ 전력 수급 모니터",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ─────────────────────────────────────────────
# API 설정
# ─────────────────────────────────────────────
try:
    API_KEY = st.secrets["api_key"]
except Exception:
    API_KEY = "dc4386cd56458b5e4c304d0175fccf6b35fe8b201c18496e29643a4531d62df7"

NX, NY = 60, 127  # 서울 격자 좌표

KPX_CURRENT_URL = (
    "https://openapi.kpx.or.kr/openapi/sukub5mMaxDatetime/getSukub5mMaxDatetime"
)
KMA_FCST_URL = (
    "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
)

# ─────────────────────────────────────────────
# 경보 등급 정의
# ─────────────────────────────────────────────
ALERT_LEVELS = [
    (15, "안전",  "#059669", "#ECFDF5", "#D1FAE5"),
    (10, "관심",  "#2563EB", "#EFF6FF", "#BFDBFE"),
    ( 7, "주의",  "#D97706", "#FFFBEB", "#FDE68A"),
    ( 5, "경계",  "#EA580C", "#FFF7ED", "#FDBA74"),
    ( 0, "심각",  "#DC2626", "#FEF2F2", "#FECACA"),
]
ALERT_ICON = {"안전": "✅", "관심": "🔵", "주의": "⚠️", "경계": "🔶", "심각": "🚨"}


def get_alert(rate: float):
    """예비율(%)로 경보 등급 반환 → (label, text_color, bg_color, border_color)"""
    for threshold, label, color, bg, border in ALERT_LEVELS:
        if rate >= threshold:
            return label, color, bg, border
    return "심각", "#DC2626", "#FEF2F2", "#FECACA"


# ─────────────────────────────────────────────
# CSS
# ─────────────────────────────────────────────
st.markdown("""
<style>
    .block-container { padding: 1.5rem 2rem 1rem; }
    div[data-testid="metric-container"] {
        background: #F8FAFC;
        border: 1px solid #E2E8F0;
        border-radius: 10px;
        padding: 12px 16px;
    }
    .alert-banner { padding: 14px 18px; border-radius: 10px; margin-bottom: 16px; }
    .pred-box     { padding: 16px; border-radius: 10px; margin: 8px 0; }
    .mini-card    { padding: 10px 12px; border-radius: 8px; text-align: center; }
    .sec-title    { font-size: 15px; font-weight: 600; margin: 14px 0 6px; }
    .caption-sm   { font-size: 12px; color: #6B7280; }
</style>
""", unsafe_allow_html=True)


# ═════════════════════════════════════════════
# 데이터 함수
# ═════════════════════════════════════════════

@st.cache_data(ttl=300)
def fetch_current_power():
    """KPX 현재전력수급현황 조회 (5분 캐시)"""
    try:
        resp = requests.get(
            KPX_CURRENT_URL,
            params={"serviceKey": API_KEY},
            timeout=8,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.text)

        def gv(tag):
            el = root.find(f".//{tag}")
            return float(el.text) if (el is not None and el.text) else None

        supply = gv("suppAbility")
        demand = gv("currPwrTot")
        if not supply or not demand:
            raise ValueError("필드 없음")

        reserve     = gv("suppReservePwr") or (supply - demand)
        reserve_rate = gv("suppReserveRate") or round(reserve / supply * 100, 1)
        return {
            "supply": supply, "demand": demand,
            "forecast": gv("forecastLoad") or demand,
            "reserve": reserve, "reserve_rate": reserve_rate,
            "dt": root.findtext(".//baseDatetime", ""),
            "source": "실시간",
        }
    except Exception:
        pass

    # ── 시뮬레이션 폴백 ──
    rng = np.random.default_rng(int(datetime.now().strftime("%Y%m%d%H")))
    supply  = int(109000 + rng.integers(-2000, 3000))
    demand  = int(73000  + rng.integers(-4000, 5000))
    reserve = supply - demand
    return {
        "supply": supply, "demand": demand,
        "forecast": int(demand + rng.integers(-500, 1000)),
        "reserve": reserve,
        "reserve_rate": round(reserve / supply * 100, 1),
        "dt": datetime.now().strftime("%Y%m%d%H%M"),
        "source": "시뮬레이션",
    }


@st.cache_data(ttl=3600)
def fetch_weather_forecast():
    """기상청 단기예보 조회 (1시간 캐시) → DataFrame(datetime, temp, humidity)"""
    try:
        now = datetime.now()
        valid_times = [2, 5, 8, 11, 14, 17, 20, 23]
        bt = max((t for t in valid_times if t <= now.hour), default=23)
        base_date = now.strftime("%Y%m%d")
        if bt == 23 and now.hour < 2:
            base_date = (now - timedelta(days=1)).strftime("%Y%m%d")

        resp = requests.get(
            KMA_FCST_URL,
            params={
                "serviceKey": API_KEY,
                "pageNo": 1, "numOfRows": 1000,
                "dataType": "JSON",
                "base_date": base_date,
                "base_time": f"{bt:02d}00",
                "nx": NX, "ny": NY,
            },
            timeout=10,
        )
        items = resp.json()["response"]["body"]["items"]["item"]
        df = pd.DataFrame(items)

        tmp = df[df["category"] == "TMP"][["fcstDate", "fcstTime", "fcstValue"]].copy()
        reh = df[df["category"] == "REH"][["fcstDate", "fcstTime", "fcstValue"]].copy()
        tmp["temp"]     = tmp["fcstValue"].astype(float)
        reh["humidity"] = reh["fcstValue"].astype(float)

        merged = pd.merge(
            tmp[["fcstDate", "fcstTime", "temp"]],
            reh[["fcstDate", "fcstTime", "humidity"]],
            on=["fcstDate", "fcstTime"],
        )
        merged["datetime"] = pd.to_datetime(
            merged["fcstDate"] + merged["fcstTime"].str.zfill(4),
            format="%Y%m%d%H%M",
        )
        return merged.sort_values("datetime").reset_index(drop=True), "실시간 예보"
    except Exception:
        pass

    # ── 시뮬레이션 폴백 ──
    rows, now = [], datetime.now().replace(minute=0, second=0, microsecond=0)
    rng = np.random.default_rng(42)
    for offset in range(72):
        dt = now + timedelta(hours=offset)
        h  = dt.hour
        temp = 26 + 4 * math.sin((h - 14) * math.pi / 12) + rng.normal(0, 1.2)
        hum  = 65 + 15 * math.cos((h - 3)  * math.pi / 12) + rng.normal(0, 3)
        rows.append({
            "datetime": dt,
            "temp":     round(temp, 1),
            "humidity": round(float(np.clip(hum, 30, 99)), 0),
        })
    return pd.DataFrame(rows), "시뮬레이션 예보"


@st.cache_data
def generate_historical():
    """최근 30일 일별 전력 수급 데이터 생성"""
    rng   = np.random.default_rng(20260609)
    today = datetime.now().date()
    rows  = []

    for i in range(29, -1, -1):
        date = today - timedelta(days=i)
        dow  = date.weekday()
        is_we = dow >= 5
        month = date.month

        base_t = {3: 13, 4: 18, 5: 22, 6: 27, 7: 30, 8: 28}.get(month, 20)
        temp  = round(base_t + float(rng.normal(0, 4)), 1)
        hum   = round(float(np.clip(rng.normal(62, 14), 30, 98)), 1)

        t_eff = max(0, temp - 22) * 850 + max(0, 16 - temp) * 600
        h_eff = max(0, hum  - 60) * 90
        w_eff = -4500 if is_we else 0
        demand = int(65000 + t_eff + h_eff + w_eff + rng.normal(0, 900))
        supply = int(108000 + rng.uniform(0, 8000))
        reserve = supply - demand
        rate    = round(reserve / supply * 100, 1)

        label, color, bg, _ = get_alert(rate)
        rows.append({
            "date":       date,
            "date_str":   date.strftime("%m/%d"),
            "weekday":    ["월","화","수","목","금","토","일"][dow],
            "temp":       temp,
            "humidity":   hum,
            "max_demand": demand,
            "supply":     supply,
            "reserve":    reserve,
            "reserve_rate": rate,
            "is_weekend": is_we,
            "alert_label": label,
            "alert_color": color,
        })

    return pd.DataFrame(rows)


def make_hourly(date_str: str, hist_df: pd.DataFrame) -> pd.DataFrame:
    """선택 날짜의 24시간 발전원별 발전량 데이터 생성"""
    row  = hist_df[hist_df["date_str"] == date_str].iloc[0]
    peak = row["max_demand"]
    rng  = np.random.default_rng(int(row["date"].strftime("%Y%m%d")))

    nuclear_base = int(18500 + rng.integers(-500, 1000))
    coal_base    = int(25500 + rng.integers(-2000, 3000))
    records = []

    for h in range(24):
        if   h < 6:  f = 0.62 + h * 0.012
        elif h < 9:  f = 0.69 + (h - 6) * 0.05
        elif h < 11: f = 0.84 + (h - 9) * 0.04
        elif h < 20: f = 0.92 + min((h - 11) * 0.01, 0.07)
        elif h < 22: f = 0.93 - (h - 20) * 0.06
        else:        f = 0.79 - (h - 22) * 0.05

        demand    = int(peak * f + rng.normal(0, 300))
        nuclear   = int(nuclear_base * (0.97 + rng.uniform(0, 0.06)))
        hydro     = int(1200 + rng.uniform(0, 600))
        oil       = int(300  + rng.uniform(0, 400))
        solar_f   = math.sin(max(0, (h - 6)) * math.pi / 13) if 6 <= h <= 19 else 0
        renewable = int(4500 * solar_f + rng.uniform(0, 800))
        coal      = int(coal_base * (0.85 + rng.uniform(0, 0.3)))
        gas       = max(0, demand - nuclear - coal - renewable - hydro - oil)

        records.append({
            "hour_str":  f"{h:02d}:00",
            "demand":    demand,
            "nuclear":   nuclear,
            "coal":      coal,
            "gas":       int(gas),
            "renewable": renewable,
            "hydro":     hydro,
            "oil":       oil,
        })

    return pd.DataFrame(records)


def predict_profile(temp: float, hum: float, is_we: bool) -> pd.DataFrame:
    """날씨 조건으로 24시간 예비력 예측"""
    t_eff = max(0, temp - 22) * 850 + max(0, 16 - temp) * 600
    h_eff = max(0, hum  - 60) * 90
    w_eff = -4500 if is_we else 0
    peak  = 65000 + t_eff + h_eff + w_eff
    supply = 110000

    records = []
    for h in range(24):
        if   h < 6:  f = 0.62 + h * 0.012
        elif h < 9:  f = 0.69 + (h - 6) * 0.05
        elif h < 11: f = 0.84 + (h - 9) * 0.04
        elif h < 20: f = 0.92 + min((h - 11) * 0.01, 0.07)
        elif h < 22: f = 0.93 - (h - 20) * 0.06
        else:        f = 0.79 - (h - 22) * 0.05

        demand  = int(peak * f)
        reserve = supply - demand
        rate    = round(reserve / supply * 100, 1)
        label, color, bg, _ = get_alert(rate)

        records.append({
            "hour_str":    f"{h:02d}:00",
            "hour":        h,
            "demand":      demand,
            "reserve":     reserve,
            "reserve_rate": rate,
            "alert":       label,
            "alert_color": color,
        })

    return pd.DataFrame(records)


# ═════════════════════════════════════════════
# 데이터 로드
# ═════════════════════════════════════════════
current       = fetch_current_power()
weather_df, weather_src = fetch_weather_forecast()
hist_df       = generate_historical()

c_label, c_color, c_bg, c_border = get_alert(current["reserve_rate"])

# ─────────────────────────────────────────────
# 헤더
# ─────────────────────────────────────────────
h1, h2 = st.columns([4, 1])
with h1:
    st.markdown("## ⚡ 전력 수급 모니터")
    st.caption(
        f"서울 기준 날씨 &nbsp;·&nbsp; 전국 전력 데이터 &nbsp;·&nbsp; "
        f"날씨: {weather_src} &nbsp;·&nbsp; 전력: {current['source']}"
    )
with h2:
    st.markdown(
        f"<p style='text-align:right;color:#6B7280;font-size:13px;margin-top:1.2rem;'>"
        f"{datetime.now().strftime('%Y-%m-%d %H:%M')}</p>",
        unsafe_allow_html=True,
    )

# ─────────────────────────────────────────────
# 현재 경보 배너
# ─────────────────────────────────────────────
st.markdown(
    f"""<div class="alert-banner"
        style="background:{c_bg}; border-left:5px solid {c_color};">
        <span style="font-size:19px;font-weight:700;color:{c_color};">
            {ALERT_ICON.get(c_label,'')} 현재 전력 수급 상태: {c_label}
        </span>
        &nbsp;&nbsp;
        <span style="font-size:14px;color:#374151;">
            예비력 {current['reserve']:,.0f} MW &nbsp;·&nbsp;
            예비율 {current['reserve_rate']:.1f}% &nbsp;·&nbsp;
            수요 {current['demand']:,.0f} / 공급 {current['supply']:,.0f} MW
        </span>
    </div>""",
    unsafe_allow_html=True,
)

# ─────────────────────────────────────────────
# KPI 카드
# ─────────────────────────────────────────────
k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("⚡ 공급 능력",  f"{current['supply']:,.0f} MW")
k2.metric("📊 현재 수요",  f"{current['demand']:,.0f} MW")
k3.metric("🔋 예비력",     f"{current['reserve']:,.0f} MW")
k4.metric(
    "📈 예비율",
    f"{current['reserve_rate']:.1f} %",
    delta=f"{current['reserve_rate'] - 15:.1f}%p (안전 기준 대비)",
)

if not weather_df.empty:
    now_w = weather_df[weather_df["datetime"] >= datetime.now()].head(1)
    if not now_w.empty:
        k5.metric("🌡️ 서울 기온", f"{now_w.iloc[0]['temp']:.1f} °C")
        k6.metric("💧 서울 습도", f"{int(now_w.iloc[0]['humidity'])} %")

st.divider()

# ═════════════════════════════════════════════
# 메인 레이아웃: 좌(과거) | 우(예측)
# ═════════════════════════════════════════════
left, right = st.columns([3, 2], gap="large")


# ══════════════════════════════════════
# 왼쪽: 과거 전력 수급 & 경보 이력
# ══════════════════════════════════════
with left:

    # ── 30일 수급 추이 차트 ────────────────────
    st.markdown('<p class="sec-title">📊 최근 30일 전력 수급 추이 & 경보 이력</p>',
                unsafe_allow_html=True)

    fig = go.Figure()

    # 경보 구간 음영
    for y0, y1, col, opacity in [
        (0,    4_000, "#DC2626", 0.06),
        (4_000, 5_500, "#EA580C", 0.05),
        (5_500, 7_000, "#D97706", 0.05),
    ]:
        fig.add_hrect(y0=y0, y1=y1,
                      fillcolor=f"rgba({','.join(str(int(col[i:i+2], 16)) for i in (1,3,5))},{opacity})",
                      line_width=0)

    # 수요 면적
    fig.add_trace(go.Scatter(
        x=hist_df["date_str"], y=hist_df["max_demand"],
        name="최대 수요", yaxis="y2",
        fill="tozeroy", fillcolor="rgba(29,158,117,0.08)",
        line=dict(color="#1D9E75", width=1.5),
        hovertemplate="%{x}<br>최대 수요: %{y:,.0f} MW<extra></extra>",
    ))

    # 공급 능력 점선
    fig.add_trace(go.Scatter(
        x=hist_df["date_str"], y=hist_df["supply"],
        name="공급 능력", yaxis="y2",
        line=dict(color="#2563EB", width=1.5, dash="dot"),
        hovertemplate="%{x}<br>공급 능력: %{y:,.0f} MW<extra></extra>",
    ))

    # 예비력 + 경보 색 마커
    fig.add_trace(go.Scatter(
        x=hist_df["date_str"], y=hist_df["reserve"],
        name="예비력",
        line=dict(color="#7C3AED", width=2),
        mode="lines+markers",
        marker=dict(color=hist_df["alert_color"].tolist(), size=8,
                    line=dict(width=1, color="white")),
        hovertemplate=(
            "%{x}<br>예비력: %{y:,.0f} MW<br>"
            + "경보: " + hist_df["alert_label"]
            + "<extra></extra>"
        ),
    ))

    # 기준선 (add_hline 대신 add_shape — 최신 Plotly 호환)
    for y, label, col in [
        (4_000, "심각 기준", "#DC2626"),
        (5_500, "경계 기준", "#EA580C"),
        (7_000, "주의 기준", "#D97706"),
    ]:
        fig.add_shape(
            type="line",
            x0=0, x1=1, xref="paper",
            y0=y, y1=y,
            line=dict(dash="dot", color=col, width=1.2),
        )
        fig.add_annotation(
            x=1, xref="paper", y=y,
            text=f"{label} ({y:,} MW)  ",
            showarrow=False,
            font=dict(size=10, color=col),
            xanchor="right",
        )

    fig.update_layout(
        height=300,
        margin=dict(l=0, r=10, t=10, b=0),
        xaxis=dict(tickangle=-45, tickfont=dict(size=10), showgrid=False),
        yaxis=dict(title="예비력 (MW)", title_font_size=11, tickfont=dict(size=10)),
        yaxis2=dict(title="수요/공급 (MW)", overlaying="y", side="right",
                    title_font_size=11, tickfont=dict(size=10)),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, font=dict(size=11)),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        
    )
    st.plotly_chart(fig, use_container_width=True)

    # ── 기온·습도 미니 차트 ──────────────────────
    fig_w = go.Figure()
    fig_w.add_trace(go.Scatter(
        x=hist_df["date_str"], y=hist_df["temp"],
        name="기온 (°C)", line=dict(color="#EF9F27", width=2),
        hovertemplate="%{x}<br>기온: %{y:.1f}°C<extra></extra>",
    ))
    fig_w.add_trace(go.Scatter(
        x=hist_df["date_str"], y=hist_df["humidity"],
        name="습도 (%)", line=dict(color="#60A5FA", width=2, dash="dot"),
        yaxis="y2",
        hovertemplate="%{x}<br>습도: %{y:.0f}%<extra></extra>",
    ))
    fig_w.update_layout(
        height=150,
        margin=dict(l=0, r=10, t=5, b=0),
        xaxis=dict(tickangle=-45, tickfont=dict(size=10), showgrid=False),
        yaxis=dict(title="기온 (°C)", title_font_size=10, tickfont=dict(size=10)),
        yaxis2=dict(title="습도 (%)", overlaying="y", side="right",
                    title_font_size=10, tickfont=dict(size=10)),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, font=dict(size=11)),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig_w, use_container_width=True)

    # ── 경보 발령 이력 테이블 ──────────────────────
    non_safe = hist_df[hist_df["alert_label"] != "안전"].copy()
    if not non_safe.empty:
        st.markdown('<p class="sec-title">🚨 경보 발령 이력 (관심 이상)</p>',
                    unsafe_allow_html=True)
        display = non_safe[
            ["date_str", "weekday", "temp", "humidity", "reserve", "reserve_rate", "alert_label"]
        ].copy()
        display.columns = ["날짜", "요일", "기온(°C)", "습도(%)", "예비력(MW)", "예비율(%)", "경보"]

        COLORS_MAP = {
            "관심": "#EFF6FF", "주의": "#FFFBEB", "경계": "#FFF7ED", "심각": "#FEF2F2"
        }

        def row_style(row):
            bg = COLORS_MAP.get(row["경보"], "")
            return [f"background-color:{bg}"] * len(row)

        st.dataframe(
            display.style.apply(row_style, axis=1),
            use_container_width=True,
            hide_index=True,
            height=min(210, (len(display) + 1) * 36),
        )
    else:
        st.info("✅ 최근 30일 내 경보 발령 이력이 없습니다.")

    # ── 시간별 발전량 차트 ──────────────────────────
    st.markdown('<p class="sec-title">🔋 시간별 발전원별 발전량</p>',
                unsafe_allow_html=True)

    date_opts = hist_df["date_str"].tolist()
    sel_date  = st.selectbox(
        "날짜 선택",
        options=date_opts,
        index=len(date_opts) - 1,
    )

    hrly = make_hourly(sel_date, hist_df)

    fig_hr = go.Figure()
    GEN_META = [
        ("nuclear",   "#7C3AED", "원자력"),
        ("coal",      "#6B7280", "석탄"),
        ("gas",       "#D97706", "가스"),
        ("renewable", "#059669", "신재생"),
        ("hydro",     "#2563EB", "수력"),
        ("oil",       "#DC2626", "유류"),
    ]
    for key, col, label in GEN_META:
        fig_hr.add_trace(go.Bar(
            x=hrly["hour_str"], y=hrly[key],
            name=label, marker_color=col,
            hovertemplate=f"{label}: %{{y:,.0f}} MW<extra></extra>",
        ))

    fig_hr.add_trace(go.Scatter(
        x=hrly["hour_str"], y=hrly["demand"],
        name="수요", mode="lines",
        line=dict(color="#111827", width=2.5, dash="dot"),
        hovertemplate="수요: %{y:,.0f} MW<extra></extra>",
    ))

    fig_hr.update_layout(
        barmode="stack",
        height=280,
        margin=dict(l=0, r=10, t=10, b=0),
        xaxis=dict(tickfont=dict(size=9), tickangle=-30),
        yaxis=dict(title="발전량 (MW)", title_font_size=11, tickfont=dict(size=10)),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, font=dict(size=11)),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig_hr, use_container_width=True)


# ══════════════════════════════════════
# 오른쪽: 예비력 예측
# ══════════════════════════════════════
with right:
    st.markdown('<p class="sec-title">🔮 날씨 기반 예비력 예측</p>',
                unsafe_allow_html=True)
    st.caption("날씨 조건을 입력하거나 기상청 예보에서 자동 불러오기")

    # 기상청 예보 자동 입력
    default_temp, default_hum, default_hour, default_we = 28, 65, 14, False

    if not weather_df.empty:
        future_w = weather_df[weather_df["datetime"] > datetime.now()].head(16)
        if not future_w.empty:
            fc_labels = {
                row["datetime"].strftime("%m/%d %H시"): row
                for _, row in future_w.iterrows()
            }
            sel_fc = st.selectbox(
                "기상청 예보 시각 선택 (자동 입력)",
                options=list(fc_labels.keys()),
            )
            fc_row       = fc_labels[sel_fc]
            default_temp = int(fc_row["temp"])
            default_hum  = int(fc_row["humidity"])
            default_hour = fc_row["datetime"].hour
            default_we   = fc_row["datetime"].weekday() >= 5

    st.markdown("---")

    p_temp = st.slider("🌡️ 기온 (°C)",     -10, 40,  default_temp,  step=1)
    p_hum  = st.slider("💧 습도 (%)",       20,  100, default_hum,   step=1)
    p_hour = st.slider("🕐 시간대",          0,   23,  default_hour,  step=1,
                       format="%d시")
    p_day  = st.radio("📅 요일 구분", ["평일", "휴일"], horizontal=True,
                      index=1 if default_we else 0)
    is_we  = p_day == "휴일"

    # ── 예측 계산 ──────────────────────────────────
    profile    = predict_profile(p_temp, p_hum, is_we)
    sel_hr     = profile.iloc[p_hour]
    p_label, p_color, p_bg, p_border = get_alert(sel_hr["reserve_rate"])

    # 예측 결과 박스
    st.markdown(
        f"""<div class="pred-box"
            style="background:{p_bg}; border-left:5px solid {p_color};">
            <div style="font-size:20px;font-weight:700;color:{p_color};margin-bottom:8px;">
                {ALERT_ICON.get(p_label,'')} {p_hour:02d}시 예측 경보: {p_label}
            </div>
            <div style="font-size:14px;color:#374151;line-height:1.9;">
                예상 수요 &nbsp;&nbsp;: <b>{sel_hr['demand']:,} MW</b><br>
                예상 예비력 : <b>{sel_hr['reserve']:,} MW</b><br>
                예상 예비율 : <b>{sel_hr['reserve_rate']:.1f}%</b>
            </div>
        </div>""",
        unsafe_allow_html=True,
    )

    # ── 24시간 예측 프로파일 차트 ──────────────────
    st.markdown('<p class="sec-title">24시간 예측 프로파일</p>',
                unsafe_allow_html=True)

    fig_p = go.Figure()

    # 경보 구간 배경
    for y0, y1, col, op in [
        (0,     4_000, "220,38,38",   0.07),
        (4_000, 5_500, "234,88,12",   0.05),
        (5_500, 7_000, "217,119,6",   0.05),
        (7_000, 15_000, "37,99,235",  0.03),
    ]:
        fig_p.add_hrect(y0=y0, y1=y1,
                        fillcolor=f"rgba({col},{op})", line_width=0)

    # 수요 면적
    fig_p.add_trace(go.Scatter(
        x=profile["hour_str"], y=profile["demand"],
        name="예상 수요", yaxis="y2",
        fill="tozeroy", fillcolor="rgba(29,158,117,0.07)",
        line=dict(color="#1D9E75", width=1.5),
        hovertemplate="%{x}<br>예상 수요: %{y:,.0f} MW<extra></extra>",
    ))

    # 예비력 라인 + 경보 색 마커
    fig_p.add_trace(go.Scatter(
        x=profile["hour_str"], y=profile["reserve"],
        name="예상 예비력",
        line=dict(color="#7C3AED", width=2),
        mode="lines+markers",
        marker=dict(color=profile["alert_color"].tolist(), size=9,
                    line=dict(width=1, color="white")),
        hovertemplate=(
            "%{x}<br>예비력: %{y:,.0f} MW<br>"
            + "경보: " + profile["alert"]
            + "<extra></extra>"
        ),
    ))

    # 선택 시간 마커 (add_vline 대신 add_shape — 문자열 x축 호환)
    fig_p.add_shape(
        type="line",
        x0=profile.iloc[p_hour]["hour_str"],
        x1=profile.iloc[p_hour]["hour_str"],
        y0=0, y1=1, yref="paper",
        line=dict(dash="dash", color="#374151", width=1.5),
    )
    fig_p.add_annotation(
        x=profile.iloc[p_hour]["hour_str"],
        y=1, yref="paper",
        text=f"  {p_hour:02d}시",
        showarrow=False,
        font=dict(size=11, color="#374151"),
        xanchor="left",
    )

    for y, label, col in [
        (4_000, "심각", "#DC2626"),
        (5_500, "경계", "#EA580C"),
        (7_000, "주의", "#D97706"),
    ]:
        fig_p.add_shape(
            type="line",
            x0=0, x1=1, xref="paper",
            y0=y, y1=y,
            line=dict(dash="dot", color=col, width=1),
        )
        fig_p.add_annotation(
            x=0, xref="paper", y=y,
            text=f"  {label}",
            showarrow=False,
            font=dict(size=9, color=col),
            xanchor="left",
        )

    fig_p.update_layout(
        height=260,
        margin=dict(l=0, r=10, t=5, b=0),
        xaxis=dict(tickfont=dict(size=9), tickangle=-45),
        yaxis=dict(title="예비력 (MW)", title_font_size=10, tickfont=dict(size=10)),
        yaxis2=dict(title="수요 (MW)", overlaying="y", side="right",
                    title_font_size=10, tickfont=dict(size=10)),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, font=dict(size=10)),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig_p, use_container_width=True)

    # ── 최악/최선 시간 미니 카드 ──────────────────
    worst = profile.loc[profile["reserve"].idxmin()]
    best  = profile.loc[profile["reserve"].idxmax()]

    wl, bl = get_alert(worst["reserve_rate"]), get_alert(best["reserve_rate"])
    c1, c2 = st.columns(2)

    with c1:
        st.markdown(
            f"""<div class="mini-card"
                style="background:{wl[2]};border:1px solid {wl[1]};">
                <div class="caption-sm">가장 위험한 시간</div>
                <div style="font-size:20px;font-weight:700;color:{wl[1]};">
                    {int(worst['hour_str'].replace(':00',''))}시
                </div>
                <div style="font-size:11px;color:{wl[1]};">
                    {wl[0]} · {worst['reserve_rate']:.1f}%
                </div>
            </div>""",
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            f"""<div class="mini-card"
                style="background:{bl[2]};border:1px solid {bl[1]};">
                <div class="caption-sm">가장 안전한 시간</div>
                <div style="font-size:20px;font-weight:700;color:{bl[1]};">
                    {int(best['hour_str'].replace(':00',''))}시
                </div>
                <div style="font-size:11px;color:{bl[1]};">
                    {bl[0]} · {best['reserve_rate']:.1f}%
                </div>
            </div>""",
            unsafe_allow_html=True,
        )

    # ── 유사 과거 데이터 비교 ───────────────────────
    st.markdown('<p class="sec-title">📅 유사 날씨 과거 데이터</p>',
                unsafe_allow_html=True)

    similar = hist_df[
        hist_df["temp"].between(p_temp - 3, p_temp + 3) &
        hist_df["humidity"].between(p_hum - 15, p_hum + 15)
    ][["date_str", "temp", "humidity", "reserve", "reserve_rate", "alert_label"]].copy()

    if not similar.empty:
        similar.columns = ["날짜", "기온", "습도", "예비력(MW)", "예비율(%)", "경보"]
        st.dataframe(similar.head(6), use_container_width=True, hide_index=True)
    else:
        st.caption("유사한 날씨 조건의 과거 데이터가 없습니다.")

    # ── 경보 기준 안내 ──────────────────────────────
    with st.expander("📋 전력수급 경보 기준 안내"):
        st.markdown("""
        | 단계 | 예비율 기준 | 의미 |
        |------|------------|------|
        | ✅ 안전 | 15% 이상 | 정상 운영 |
        | 🔵 관심 | 10 – 15% | 모니터링 강화 |
        | ⚠️ 주의 | 7 – 10%  | 수요 관리 권고 |
        | 🔶 경계 | 5 – 7%   | 비상 절전 준비 |
        | 🚨 심각 | 5% 미만  | 비상 절전 시행 |
        """)


# ─────────────────────────────────────────────
# 푸터
# ─────────────────────────────────────────────
st.divider()
st.caption(
    "데이터 출처: 기상청 단기예보 조회서비스 · 한국전력거래소 현재전력수급현황 · 공공데이터포털 &nbsp;|&nbsp; "
    "예측 모델은 교육 목적의 단순화된 추정치이며 실제 계통 운영과 다를 수 있습니다."
)
