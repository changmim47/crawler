import streamlit as st
from bs4 import BeautifulSoup
import pandas as pd
import asyncio
from datetime import datetime, timedelta
import tempfile
import os
import gspread
from google.oauth2.service_account import Credentials
from playwright.async_api import async_playwright

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
def collect_qna_ids(html):
    soup = BeautifulSoup(html, "html.parser")
    qna_elements = soup.select('td.subject[onclick^="fnProperties("]')
    ids = [elem.get("onclick").split("'")[1] for elem in qna_elements]
    return ids

# -------------------- 총 페이지 수 추출 --------------------
def get_total_pages(html):
    soup = BeautifulSoup(html, "html.parser")
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
def extract_question(soup):
    h2_tags = soup.select("div.infor_customer > h2")
    for h2 in h2_tags:
        if "질문내용" in h2.text:
            content_div = h2.find_next_sibling("div")
            return content_div.get_text(strip=True) if content_div else ""
    return ""

def extract_answer(soup):
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

# -------------------- 크롤링 --------------------
async def crawl_faq(username, password, base_url, existing_qids):
    results = []
    new_qids = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # 로그인
        await page.goto("https://faq.megagong.net/")
        await page.fill("input[name='USERID']", username)
        await page.fill("input[name='USER_PWD']", password)
        await page.evaluate("go_submit();")
        await page.wait_for_selector("#sStartDate")

        await page.goto(base_url)
        await page.wait_for_selector("td.subject[onclick^='fnProperties']")
        content = await page.content()
        total_pages = get_total_pages(content)

        for page_num in range(1, total_pages + 1):
            url = base_url + f"&page={page_num}"
            await page.goto(url)
            await page.wait_for_selector("td.subject[onclick^='fnProperties']")
            html = await page.content()
            qna_ids = collect_qna_ids(html)
            soup = BeautifulSoup(html, "html.parser", from_encoding="euc-kr")

            for qid in qna_ids:
                if qid in existing_qids:
                    continue

                await page.goto(f"https://faq.megagong.net/erms/transmissionM/properties.asp?intQstIdx={qid}&page=1")
                await page.wait_for_selector("div.infor_customer")
                q_html = await page.content()
                q_soup = BeautifulSoup(q_html, "html.parser", from_encoding="euc-kr")
                q = extract_question(q_soup)

                await page.goto(f"https://faq.megagong.net/erms/transmissionM/properties_02.asp?intQstIdx={qid}&page=1")
                await page.wait_for_selector("div.infor_customer")
                a_html = await page.content()
                a_soup = BeautifulSoup(a_html, "html.parser", from_encoding="euc-kr")
                a = extract_answer(a_soup)

                results.append({"QID": qid, "질문": q, "답변": a})
                new_qids.append(qid)

        await browser.close()
    return results, new_qids

# -------------------- Streamlit 인터페이스 --------------------
st.title("📌 메가공 FAQ 자동 수집기 (Playwright)")

username = st.text_input("🔐 관리자 ID")
password = st.text_input("🔐 관리자 비밀번호", type="password")
criterion = st.radio("📅 기준 선택", ["질문일 기준", "답변일 기준"])
rCriterion = "1" if criterion == "질문일 기준" else "2"
start_date = st.date_input("시작일", value=datetime.today() - timedelta(days=1))
start_hour = st.selectbox("시작 시간 (0~23시)", list(range(24)), index=0)
end_date = st.date_input("종료일", value=datetime.today())
end_hour = st.selectbox("종료 시간 (0~23시)", list(range(24)), index=23)
filter_duplicates = st.checkbox("✅ 기존에 수집한 QID는 제외하고 수집", value=True)
sheet_name = st.text_input("📄 업로드할 Google 시트 이름")
auto_upload = st.checkbox("📡 크롤링 완료 후 자동 업로드", value=False)

if st.button("🚀 크롤링 시작"):
    with st.spinner("크롤링 중입니다... 잠시만 기다려주세요."):
        base_url = (
            "https://faq.megagong.net/erms/transmissionM/index.asp"
            f"?rCriterion={rCriterion}"
            f"&sStartDate={start_date.strftime('%Y-%m-%d')}"
            f"&sStartHour={start_hour:02}"
            f"&sEndDate={end_date.strftime('%Y-%m-%d')}"
            f"&sEndHour={end_hour:02}"
        )

        existing_qids = load_collected_qids() if filter_duplicates else set()

        results, new_qids = asyncio.run(crawl_faq(username, password, base_url, existing_qids))

        if new_qids:
            save_new_qids(new_qids)

        df = pd.DataFrame(results)
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
