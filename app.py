import streamlit as st
import google.generativeai as genai
import feedparser
import requests
import json
import time
import ssl

# ==========================================
# 1. 基礎設定與 SSL 修復
# ==========================================
st.set_page_config(page_title="IFA 智能新聞策展", layout="wide")

# 強制忽略 SSL 憑證檢查
try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context

# ==========================================
# 2. 核心功能函數
# ==========================================

def fetch_news():
    """抓取 Google News RSS 新聞"""
    news_items = []
    
    urls = [
        ("稅務與法規", "https://news.google.com/rss/search?q=台灣+稅務+OR+房地合一+OR+所得稅+when:1d&hl=zh-TW&gl=TW&ceid=TW:zh-TW"),
        ("退休與年金", "https://news.google.com/rss/search?q=台灣+退休金+OR+勞保+OR+勞退+when:1d&hl=zh-TW&gl=TW&ceid=TW:zh-TW"),
        ("投資與ETF", "https://news.google.com/rss/search?q=台灣+ETF+配息+OR+金管會+when:1d&hl=zh-TW&gl=TW&ceid=TW:zh-TW"),
        ("房產與保險", "https://news.google.com/rss/search?q=台灣+房貸+OR+新青安+OR+長照保險+when:1d&hl=zh-TW&gl=TW&ceid=TW:zh-TW")
    ]

    for category, url in urls:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:10]: # 減少每類抓取量以加快速度
                news_items.append({
                    "category": category,
                    "title": entry.title,
                    "link": entry.link,
                    "source": entry.source.title if hasattr(entry, 'source') else "Google News",
                    "summary": entry.summary[:200] if hasattr(entry, 'summary') else ""
                })
        except Exception as e:
            print(f"Error fetching {category}: {e}")
            
    return news_items

def get_working_model():
    """
    【核心修復】模型輪盤：自動嘗試所有可能的模型名稱
    直到找到一個不會報錯的為止。
    """
    candidate_models = [
        "gemini-1.5-flash",          # 最新標準名
        "gemini-1.5-flash-latest",   # 變體名
        "models/gemini-1.5-flash",   # 帶前綴名
        "gemini-pro",                # 舊版穩定名
        "models/gemini-pro",         # 舊版帶前綴
        "gemini-1.0-pro"             # 原始版
    ]
    
    # 這裡我們只回傳一個基礎物件，真正的測試在 generate_content 時
    # 為了節省時間，我們預設先用第一個，但在執行時做錯誤攔截
    return candidate_models

def batch_generate_comments(selected_news):
    """批次生成評論 (包含重試機制)"""
    
    # 準備給 AI 的資料包
    news_text_block = json.dumps([{
        "id": i, 
        "title": n['title'], 
        "summary": n['summary']
    } for i, n in enumerate(selected_news)], ensure_ascii=False)

    prompt = f"""
    你是台灣資深財務顧問(IFA)。以下是 6 則精選財經新聞：
    {news_text_block}

    請針對「每一則」新聞，撰寫簡短專業點評。
    
    【格式要求】：
    請回傳一個純 JSON List，不要有 markdown 標記。
    List 中包含 6 個物件，每個物件格式如下：
    {{
        "id": (對應的新聞ID),
        "advisor_view": "顧問觀點 (條列式，2點，關注資產影響)",
        "action": "行動建議 (1句)"
    }}
    """
    
    # 取得候選模型清單
    candidates = get_working_model()
    last_error = ""

    # 【關鍵迴圈】一個一個試，直到成功
    for model_name in candidates:
        try:
            # 嘗試建立模型
            model = genai.GenerativeModel(model_name)
            # 嘗試生成內容
            response = model.generate_content(prompt)
            
            # 如果成功執行到這裡，代表這個模型名稱是對的！
            cleaned_text = response.text.replace("```json", "").replace("```", "").strip()
            comments_data = json.loads(cleaned_text)
            
            # 成功後直接回傳，並在 Console 印出是用哪個模型成功的
            print(f"SUCCESS with model: {model_name}")
            return comments_data
            
        except Exception as e:
            error_msg = str(e)
            print(f"Failed with {model_name}: {error_msg}")
            
            # 如果是 429 (額度滿)，這不是名字錯，要休息一下再試
            if "429" in error_msg:
                time.sleep(2)
                continue # 換下一個模型試試看運氣
                
            # 如果是 404 (名字錯)，直接換下一個
            last_error = error_msg
            continue

    # 如果全部都試失敗了
    st.error(f"所有 AI 模型都無法連線。最後一次錯誤: {last_error}")
    return []

# ==========================================
# 3. 整合流程 (加上快取機制)
# ==========================================

@st.cache_data(ttl=3600, show_spinner=False)
def run_curation_pipeline(api_key):
    genai.configure(api_key=api_key)
    
    # 1. 抓取新聞
    raw_news = fetch_news()
    if not raw_news:
        return None, "無法抓取新聞"

    # 2. 多樣性篩選 (Python 邏輯)
    selected_news = []
    seen_titles = set()
    categories = ["稅務與法規", "退休與年金", "投資與ETF", "房產與保險"]
    
    # 確保每個分類至少有一篇
    while len(selected_news) < 6 and raw_news:
        for cat in categories:
            candidates = [n for n in raw_news if n['category'] == cat and n['title'] not in seen_titles]
            if candidates:
                pick = candidates[0]
                selected_news.append(pick)
                seen_titles.add(pick['title'])
                if len(selected_news) >= 6: break
        
        if len(selected_news) < 6:
            remaining = [n for n in raw_news if n['title'] not in seen_titles]
            if not remaining: break
            pick = remaining[0]
            selected_news.append(pick)
            seen_titles.add(pick['title'])

    # 3. 批次生成評論 (這步最容易錯，現在有輪盤保護)
    comments_data = batch_generate_comments(selected_news)
    
    # 4. 組合結果
    final_results = []
    for news in selected_news:
        comment = next((c for c in comments_data if c.get('title') == news['title'] or c.get('id') == selected_news.index(news)), None)
        if not comment and len(comments_data) > selected_news.index(news):
            comment = comments_data[selected_news.index(news)]

        final_results.append({
            "news": news,
            "comment": comment
        })
        
    return final_results, None

# ==========================================
# 4. 主程式介面 (UI)
# ==========================================

def main():
    st.title("🤖 IFA 智能新聞策展系統")
    st.caption("自動彙整稅務、退休、投資與房產資訊，生成顧問觀點。")

    api_key = None
    if "GOOGLE_API_KEY" in st.secrets:
        api_key = st.secrets["GOOGLE_API_KEY"]
    else:
        api_key = st.sidebar.text_input("請輸入 Google API Key", type="password")

    if not api_key:
        st.warning("請先設定 API Key 才能開始運作。")
        return

    if st.button("開始策展 (更新日報)"):
        with st.spinner("AI 正在嘗試連接最佳模型並整理新聞..."):
            results, error = run_curation_pipeline(api_key)
            
            if error:
                st.error(error)
            else:
                st.success(f"策展完成！資料來源：Google News")
                
                st.divider()
                cols = st.columns(2)
                
                for idx, item in enumerate(results):
                    news = item['news']
                    comment = item['comment']
                    
                    with cols[idx % 2]:
                        with st.container(border=True):
                            st.subheader(news['title'])
                            st.caption(f"由 {news['source']} 發布於 {news['category']}")
                            
                            advisor_view = "\n".join([f"- {p}" for p in comment.get('advisor_view', [])]) if comment else "AI 生成中斷"
                            action = comment.get('action', '建議詳閱原文') if comment else ""
                            
                            content = f"""
### 💼 顧問觀點
{advisor_view}

### 🚀 建議行動
{action}

[閱讀原文]({news['link']})
"""
                            st.markdown(content)
                            with st.expander("複製文案"):
                                st.code(f"{news['title']}\n\n{content}", language="markdown")

if __name__ == "__main__":
    main()
