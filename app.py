import base64
from pathlib import Path
import streamlit as st

# 1. 페이지 기본 설정
st.set_page_config(
    page_title="봄내틔움 새싹 상태칸", page_icon="🌱", layout="centered"
)


# 2. 🌐 [팀원 공유] 실행 시 외부 접속 링크(ngrok) 자동 생성
@st.cache_resource
def start_sharing_link():
    try:
        from pyngrok import ngrok

        tunnel = ngrok.connect(8501)
        return tunnel.public_url
    except Exception:
        return None


# 왼쪽 사이드바에 팀원 공유용 박스 출력
st.sidebar.header("📢 팀원 공유용 링크")
shared_url = start_sharing_link()

if shared_url:
    st.sidebar.success("🎉 외부 접속 링크가 생성되었습니다!")
    st.sidebar.code(shared_url, language="text")
    st.sidebar.caption("👉 위 링크를 복사해서 팀원에게 전달하세요!")
else:
    st.sidebar.info(
        "💡 팀원 공유 링크를 원하시면 Terminal에\n`pip install pyngrok`을 1번 실행해 주세요."
    )


# 3. 🖼️ 이미지 처리 (파일 유무 안전장치)
img_path = Path("assets/sprout_idle.png")

if img_path.exists():
    img_base64 = base64.b64encode(img_path.read_bytes()).decode()
    img_src = f"data:image/png;base64,{img_base64}"
else:
    img_src = "https://raw.githubusercontent.com/twitter/twemoji/master/assets/svg/1f331.svg"


# 4. HTML / CSS UI 카드
html_code = f"""
<style>
.growth-card {{
    max-width: 380px;
    margin: 10px auto;
    border-radius: 28px;
    padding: 28px;
    background: linear-gradient(180deg, #ffffff 0%, #effbea 100%);
    border: 1px solid #d9efcf;
    box-shadow: 0 18px 40px rgba(67, 125, 55, 0.13);
    text-align: center;
    position: relative;
    overflow: hidden;
    font-family: "Pretendard", "Noto Sans KR", Arial, sans-serif;
}}

.badge {{
    display: inline-block;
    padding: 7px 13px;
    border-radius: 999px;
    background: #e4f8dc;
    color: #3b8d2d;
    font-size: 14px;
    font-weight: 700;
    margin-bottom: 10px;
}}

.growth-title {{
    margin: 6px 0 4px;
    font-size: 26px;
    color: #23451d;
    font-weight: 800;
}}

.growth-desc {{
    margin: 0;
    color: #60715a;
    font-size: 14px;
    line-height: 1.5;
}}

.garden {{
    height: 210px;
    display: grid;
    place-items: center;
    margin: 12px 0 18px;
}}

.floating-sprout {{
    width: 130px;
    animation: floatSprout 1.8s ease-in-out infinite;
    filter: drop-shadow(0 12px 12px rgba(49, 91, 35, 0.18));
}}

@keyframes floatSprout {{
    0% {{ transform: translateY(0) rotate(-1deg); }}
    50% {{ transform: translateY(-12px) rotate(1deg); }}
    100% {{ transform: translateY(0) rotate(-1deg); }}
}}

.progress-wrap {{
    text-align: left;
    margin-top: 14px;
}}

.progress-text {{
    display: flex;
    justify-content: space-between;
    font-size: 13px;
    color: #55704e;
    margin-bottom: 7px;
    font-weight: 700;
}}

.progress {{
    height: 10px;
    background: #e5f2df;
    border-radius: 999px;
    overflow: hidden;
}}

.progress-bar {{
    width: 35%;
    height: 100%;
    background: linear-gradient(90deg, #7ed957, #3cae42);
    border-radius: 999px;
}}
</style>

<section class="growth-card">
  <span class="badge">현재 상태</span>
  <div class="growth-title">새싹</div>
  <p class="growth-desc">첫 프로그램을 수강하고<br>가능성이 막 틔어나기 시작했어요.</p>

  <div class="garden">
    <img src="{img_src}" class="floating-sprout" alt="새싹 캐릭터">
  </div>

  <div class="progress-wrap">
    <div class="progress-text">
      <span>새싹 성장도</span>
      <span>35%</span>
    </div>
    <div class="progress">
      <div class="progress-bar"></div>
    </div>
  </div>
</section>
"""

# 최신 스트림릿 전용 HTML 렌더링 함수 사용
st.html(html_code)