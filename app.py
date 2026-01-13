import streamlit as st
import google.generativeai as genai
import feedparser
import requests
import json
import time
import ssl

# ==========================================
# 1. 基礎設定與 SSL 修復 (必備)
# ==========================================
st.set_page_config(page_title="IFA 智能新聞策展", layout="wide")

# 強制忽略 SSL 憑證檢查 (解決 RSS 抓取失敗問題)
try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context

# ==========================================
# 2. 核心功能函數 (這些是您的工具箱)
# ==========================================

def get_active_model_name():
    """自動偵測可用的 Gemini 模型，優先使用 Flash 1.5"""
    try:
        models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        
        # 優先順序策略
        for m in models:
            if "gemini-1.5-flash" in m: return m
        for m in models:
            if "gemini-1.5-pro" in m: return m
        for m in models:
            if "gemini-pro" in m: return m
            
        return "models/gemini-1.5-flash-latest" # 最終備案
    except:
        return "gemini-pro"

def fetch_news():
    """抓取 Google News RSS 新聞 (之前遺失的部分補在這裡)"""
    news_items = []
    
    # 針對台灣理財的關鍵字搜尋 RSS
    urls = [
        ("稅務與法規", "https://news.google.com/rss/search?q=台灣+稅務+OR+房地合一+OR+所得稅+when:1d&hl=zh-TW&gl=TW&ceid=TW:zh-TW"),
        ("退休與年金", "https://news.google.com/rss/search?q=台灣+退休金+OR+勞保+OR+勞退+when:1d&hl=zh-TW&gl=TW&ceid=TW:zh-TW"),
        ("投資與ETF", "https://news.google.com/rss/search?q=台灣+ETF+配息+OR+金管會+when:1d&hl=zh-TW&gl=TW&ceid=TW:zh-TW"),
        ("房產與保險", "https://news.google.com/rss/search?q=台灣+房貸+OR+新青安+OR+長照保險+when:1d&hl=zh-TW&gl=TW&ceid=TW:zh-TW")
    ]

    for category, url in urls:
        try:
            feed = feedparser.parse(url)
            # 每一類抓前 15 篇，確保原料充足
            for entry in feed.entries[:15]:
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

def batch_generate_comments(selected_news, model_name):
    """批次生成評論 (節省 API 次數)"""
    model = genai.GenerativeModel(model_name)
    
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
    
    try:
        response = model.generate_content(prompt)
        # 清理回傳字串
        cleaned_text = response.text.replace("```json", "").replace("```", "").strip()
        comments_data = json.loads(cleaned_text)
        return comments_data
    except Exception as e:
        st.error(f"AI 生成失敗: {e}")
        return []

# ==========================================
# 3. 整合流程 (加上快取機制)
# ==========================================

@st.cache_data(ttl=3600, show_spinner=False)
def run_curation_pipeline(api_key):
    """
    這是一個「被快取」的超級函數。
    1小時內，不管重新整理幾次，這個函數只會被執行一次。
    """
    # 設定 API Key
    genai.configure(api_key=api_key)
    
    # 1. 偵測模型
    model_name = get_active_model_name()
    
    # 2. 抓取新聞 (修復點：這裡會呼叫上面的 fetch_news)
    raw_news = fetch_news()
    if not raw_news:
        return None, "無法抓取新聞", model_name

    # 3. AI 篩選 (這裡簡化邏輯，直接選不同分類的前幾篇以確保多樣性，節省一次 API)
    # 為了省 API，我們用 Python 邏輯來做「多樣性篩選」，不一定要用 AI
    selected_news = []
    seen_titles = set()
    categories = ["稅務與法規", "退休與年金", "投資與ETF", "房產與保險"]
    
    # 輪流從每個分類抓一篇，直到湊滿 6 篇
    while len(selected_news) < 6 and raw_news:
        for cat in categories:
            # 找該分類的第一篇
            candidates = [n for n in raw_news if n['category'] == cat and n['title'] not in seen_titles]
            if candidates:
                pick = candidates[0]
                selected_news.append(pick)
                seen_titles.add(pick['title'])
                if len(selected_news) >= 6: break
        
        # 如果跑完一輪還不夠，就隨便補
        if len(selected_news) < 6:
            remaining = [n for n in raw_news if n['title'] not in seen_titles]
            if not remaining: break
            pick = remaining[0]
            selected_news.append(pick)
            seen_titles.add(pick['title'])

    # 4. 批次生成評論
    comments_data = batch_generate_comments(selected_news, model_name)
    
    # 5. 組合結果
    final_results = []
    for news in selected_news:
        # 找到對應的評論
        comment = next((c for c in comments_data if c.get('title') == news['title'] or c.get('id') == selected_news.index(news)), None)
        
        # 如果 AI 沒回傳對應 ID，嘗試用順序對應
        if not comment and len(comments_data) > selected_news.index(news):
            comment = comments_data[selected_news.index(news)]

        final_results.append({
            "news": news,
            "comment": comment
        })
        
    return final_results, None, model_name

# ==========================================
# 4. 主程式介面 (UI)
# ==========================================

def main():
    st.title("🤖 IFA 智能新聞策展系統")
    st.caption("自動彙整稅務、退休、投資與房產資訊，生成顧問觀點。")

    # 處理 API Key (優先讀取 secrets，沒有則顯示輸入框)
    api_key = None
    if "GOOGLE_API_KEY" in st.secrets:
        api_key = st.secrets["GOOGLE_API_KEY"]
    else:
        api_key = st.sidebar.text_input("請輸入 Google API Key", type="password")

    if not api_key:
        st.warning("請先設定 API Key 才能開始運作。")
        return

    # 按鈕觸發
    if st.button("開始策展 (更新日報)"):
        with st.spinner("AI 正在閱讀並整理全台財經新聞... (約需 10-20 秒)"):
            results, error, model_used = run_curation_pipeline(api_key)
            
            if error:
                st.error(error)
            else:
                st.toast(f"使用模型: {model_used} | 資料已快取")
                
                # 顯示結果 (雙欄排版)
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
