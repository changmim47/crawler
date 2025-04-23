import streamlit as st
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
import pandas as pd
import time
from datetime import datetime, timedelta
import tempfile
import os
import gspread
from google.oauth2.service_account import Credentials  # ✅ 최신 라이브러리로 변경

# -------------------- 로그인 --------------------
def login(driver, username, password):
    driver.get("https://faq.megagong.net/")
    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.NAME, "USERID")))
    driver.find_element(By.NAME, "USERID").send_keys(username)
    driver.find_element(By.NAME, "USER_PWD").send_keys(password)
    driver.execute_script("go_submit();")
    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "sStartDate")))

# -------------------- QID 중복 필터 --------------------
def load_collected_qids(file_path="collected_qids.txt"):
    try:
        with open(file_path, "r") as f:
            return set(line.strip() for line in f)
    except FileNotFoundError:
        return set()

def save_new_qids(new_qids, file_path="collected_qids.txt"):
    with open(file_path, "a") as f:
        for qid in new_qids:
            f.write(f"{qid}\n")

# -------------------- QID 수집 --------------------
def collect_qna_ids(driver):
    soup = BeautifulSoup(driver.page_source, "html.parser")
    qna_elements = soup.select('td.subject[onclick^="fnProperties("]')
    ids = [elem.get("onclick").split("'")[1] for elem in qna_elements]
    return ids

# -------------------- 총 페이지 수 추출 --------------------
def get_total_pages(driver):
    soup = BeautifulSoup(driver.page_source, "html.parser")
    page_links = soup.select("td.pagenum a[href*='page=']")
    pages = []
    for link in page_links:
        href = link.get("href")
        if "page=" in href:
            try:
                page_num = int(href.split("page=")[-1])
                pages.append(page_num)
            except ValueError:
                continue
    return max(pages) if pages else 1

# -------------------- 질문/답변 추출 --------------------
def extract_question(driver, idx):
    driver.get(f"https://faq.megagong.net/erms/transmissionM/properties.asp?intQstIdx={idx}&page=1")
    time.sleep(1)
    soup = BeautifulSoup(driver.page_source, "html.parser", from_encoding="euc-kr")
    h2_tags = soup.select("div.infor_customer > h2")
    for h2 in h2_tags:
        if "질문내용" in h2.text:
            content_div = h2.find_next_sibling("div")
            return content_div.get_text(strip=True) if content_div else ""
    return ""

def extract_answer(driver, idx):
    driver.get(f"https://faq.megagong.net/erms/transmissionM/properties_02.asp?intQstIdx={idx}&page=1")
    time.sleep(1)
    soup = BeautifulSoup(driver.page_source, "html.parser", from_encoding="euc-kr")
    h2_tags = soup.select("div.infor_customer > h2")
    for h2 in h2_tags:
        if "답변내역" in h2.text:
            answer_paragraphs = []
            for p in h2.find_all_next("p"):
                if p.find_previous("h2") != h2:
                    break
                answer_paragraphs.append(p.get_text(strip=True))
            return "\n".join(answer_paragraphs)
    return ""

# -------------------- 구글 시트 업로드 --------------------
def upload_to_google_sheet(sheet_name, df):
    scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
    gc = gspread.authorize(creds)

    try:
        sh = gc.open(sheet_name)
    except gspread.SpreadsheetNotFound:
        st.error("❌ 구글 시트를 찾을 수 없습니다. 이름을 확인하세요.")
        return

    worksheet = sh.sheet1
    worksheet.clear()
    worksheet.update([df.columns.values.tolist()] + df.values.tolist())

# -------------------- Streamlit 인터페이스 --------------------
st.title("📌 메가공 FAQ 자동 수집기")

if "df" not in st.session_state:
    st.session_state.df = pd.DataFrame()

username = st.text_input("🔐 관리자 ID", key="admin_id")
password = st.text_input("🔐 관리자 비밀번호", type="password", key="admin_pw")

criterion = st.radio("📅 기준 선택", ["질문일 기준", "답변일 기준"])
rCriterion = "1" if criterion == "질문일 기준" else "2"

start_date = st.date_input("시작일", value=datetime.today() - timedelta(days=1))
start_hour = st.selectbox("시작 시간 (0~23시)", list(range(24)), index=0)

end_date = st.date_input("종료일", value=datetime.today())
end_hour = st.selectbox("종료 시간 (0~23시)", list(range(24)), index=23)

filter_duplicates = st.checkbox("✅ 기존에 수집한 QID는 제외하고 수집", value=True)
sheet_name = st.text_input("📄 업로드할 Google 시트 이름", key="sheet_name_input")
auto_upload = st.checkbox("📡 크롤링 완료 후 자동 업로드", value=False)

if st.button("🚀 크롤링 시작"):
    with st.spinner("크롤링 중입니다... 잠시만 기다려주세요."):
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        from webdriver_manager.chrome import ChromeDriverManager

        options = Options()
        options.add_argument("--headless")
        options.add_argument("--disable-gpu")
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

        login(driver, username, password)

        base_url = (
            "https://faq.megagong.net/erms/transmissionM/index.asp"
            f"?rCriterion={rCriterion}"
            f"&sStartDate={start_date.strftime('%Y-%m-%d')}"
            f"&sStartHour={start_hour:02}"
            f"&sEndDate={end_date.strftime('%Y-%m-%d')}"
            f"&sEndHour={end_hour:02}"
        )

        driver.get(base_url)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, "td.subject[onclick^='fnProperties']")))

        total_pages = get_total_pages(driver)
        existing_qids = load_collected_qids() if filter_duplicates else set()

        results = []
        new_qids = []
        progress = st.progress(0, text="질문/답변 수집 중...")

        for page in range(1, total_pages + 1):
            page_url = base_url + f"&page={page}"
            driver.get(page_url)
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, "td.subject[onclick^='fnProperties']")))

            qna_ids = collect_qna_ids(driver)
            if not qna_ids:
                break

            for i, qid in enumerate(qna_ids):
                if qid in existing_qids:
                    continue
                q = extract_question(driver, qid)
                a = extract_answer(driver, qid)
                results.append({"QID": qid, "질문": q, "답변": a})
                new_qids.append(qid)
                progress.progress((i + 1) / len(qna_ids), text=f"{i + 1}/{len(qna_ids)} 수집 완료 (페이지 {page})")

        driver.quit()

        if new_qids:
            save_new_qids(new_qids)

        df = pd.DataFrame(results)
        st.session_state.df = df
        st.success(f"총 {len(df)}건 수집 완료")

        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
            df.to_excel(tmp.name, index=False)
            tmp.seek(0)
            st.download_button("📅 엑셀 다운로드", tmp.read(), file_name="qna_data.xlsx")

        if auto_upload and sheet_name:
            try:
                upload_to_google_sheet(sheet_name, df)
                st.success("✅ 구글 시트 자동 업로드 완료!")
            except Exception as e:
                st.error(f"❌ 자동 업로드 실패: {e}")

# -------------------- 수동 업로드 UI --------------------
st.subheader("📤 구글 시트 수동 업로드")

if st.button("📡 수동 업로드 실행") and sheet_name:
    if st.session_state.df.empty:
        st.warning("⚠️ 먼저 크롤링 데이터를 수집해 주세요.")
    else:
        try:
            upload_to_google_sheet(sheet_name, st.session_state.df)
            st.success("✅ 구글 시트 수동 업로드 완료!")
        except Exception as e:
            st.error(f"❌ 수동 업로드 실패: {e}")
