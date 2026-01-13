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
            for entry in feed.entries[:10]: 
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

def detect_and_generate(prompt):
    """
    【核心修復】自動偵測模型並生成
    不再猜測模型名稱，而是直接讀取 list_models() 的結果。
    """
    try:
        # 1. 直接問 Google 有哪些模型
        available_models = []
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                available_models.append(m.name)
        
        # 顯示在終端機以便除錯
        print(f"偵測到的可用模型: {available_models}")

        # 2. 優先順序策略 (避免用到 2.5 這種沒額度的)
        # 我們要找包含 'flash' 或 'pro' 但不包含 '2.5' 的模型
        chosen_model = None
        
        # 策略 A: 找 Flash (最快)
        for m in available_models:
            if "flash" in m and "2.5" not in m:
                chosen_model = m
                break
        
        # 策略 B: 找 Pro (次選)
        if not chosen_model:
            for m in available_models:
                if "pro" in m and "2.5" not in m:
                    chosen_model = m
                    break
        
        # 策略 C: 隨便找一個 (只要不是 2.5)
        if not chosen_model:
            for m in available_models:
                if "2.5" not in m:
                    chosen_model = m
                    break
        
        # 策略 D: 真的沒魚蝦也好，就用第一個
        if not chosen_model and available_models:
            chosen_model = available_models[0]
            
        if not chosen_model:
            return None, "找不到任何可用模型 (ListModels returned empty)"

        print(f"最終決定使用: {chosen_model}")
        
        # 3. 執行生成
        model = genai.GenerativeModel(chosen_model)
        response = model.generate_content(prompt)
        return response.text, None

    except Exception as e:
        return None, str(e)

def batch_generate_comments(selected_news):
    """批次生成評論"""
    
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
    
    # 使用新的自動偵測函數
    result_text, error = detect_and_generate(prompt)
    
    if error:
        # 如果是 429 錯誤，提示使用者
        if "429" in str(error):
            st.warning("⚠️ Google API 忙碌中 (429 Rate Limit)。請稍候再試。")
        else:
            st.error(f"AI 生成失敗: {error}")
        return []

    try:
        cleaned_text = result_text.replace("```json", "").replace("```", "").strip()
        comments_data = json.loads(cleaned_text)
        return comments_data
    except Exception as e:
        st.error(f"JSON 解析失敗: {e}")
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

    # 2. 多樣性篩選
    selected_news = []
    seen_titles = set()
    categories = ["稅務與法規", "退休與年金", "投資與ETF", "房產與保險"]
    
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

    # 3. 批次生成評論
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
        with st.spinner("AI 正在偵測可用模型並整理新聞..."):
            # 這裡的 try-catch 是為了防止 list_models 本身報錯
            try:
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
            except Exception as e:
                st.error(f"發生未預期的錯誤: {e}")

if __name__ == "__main__":
    main()
