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

# 強制忽略 SSL 憑證檢查 (解決 RSS 抓取失敗問題)
try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context

# ==========================================
# 2. 核心功能函數
# ==========================================

def get_active_model_name():
    """
    【核心邏輯】自動偵測可用的 Gemini 模型
    不猜測名字，而是列出帳號內可用的模型，優先選擇 Flash 版本
    """
    try:
        # 列出所有支援 'generateContent' 的模型
        models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        
        # 顯示在終端機以便除錯
        print(f"Detected Models: {models}")

        # 優先順序策略 (避免選到 2.5 這種沒額度的，優先選 1.5)
        # 1. 找 gemini-1.5-flash (最優選)
        for m in models:
            if "gemini-1.5-flash" in m and "002" not in m: # 避開實驗版
                return m
        
        # 2. 找任何 flash
        for m in models:
            if "flash" in m: return m
            
        # 3. 找 gemini-pro (穩定版)
        for m in models:
            if "gemini-pro" in m: return m
            
        # 4. 真的沒魚蝦也好，回傳第一個
        return models[0] if models else "gemini-pro"
        
    except Exception as e:
        print(f"Error listing models: {e}")
        return "gemini-pro"

def fetch_news():
    """抓取 Google News RSS 新聞"""
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
            # 每一類抓前 10 篇
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

def batch_generate_comments(selected_news, model_name):
    """批次生成評論"""
    
    # 使用自動偵測到的模型名稱
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
        cleaned_text = response.text.replace("```json", "").replace("```", "").strip()
        comments_data = json.loads(cleaned_text)
        return comments_data
    except Exception as e:
        if "429" in str(e):
            st.warning("⚠️ 系統忙碌中 (API 額度限制)。請稍等 1 分鐘後再試。")
        else:
            st.error(f"AI 生成失敗: {e}")
        return []

# ==========================================
# 3. 整合流程 (加上快取機制)
# ==========================================

@st.cache_data(ttl=3600, show_spinner=False)
def run_curation_pipeline(api_key):
    """1小時內只執行一次"""
    genai.configure(api_key=api_key)
    
    # 1. 自動偵測模型 (這是您要的關鍵功能)
    model_name = get_active_model_name()
    
    # 2. 抓取新聞
    raw_news = fetch_news()
    if not raw_news:
        return None, "無法抓取新聞", model_name

    # 3. 多樣性篩選
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

    # 4. 批次生成評論 (傳入自動偵測到的模型)
    comments_data = batch_generate_comments(selected_news, model_name)
    
    # 5. 組合結果
    final_results = []
    for news in selected_news:
        comment = next((c for c in comments_data if c.get('title') == news['title'] or c.get('id') == selected_news.index(news)), None)
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

    api_key = None
    if "GOOGLE_API_KEY" in st.secrets:
        api_key = st.secrets["GOOGLE_API_KEY"]
    else:
        api_key = st.sidebar.text_input("請輸入 Google API Key", type="password")

    if not api_key:
        st.warning("請先設定 API Key 才能開始運作。")
        return

    if st.button("開始策展 (更新日報)"):
        with st.spinner("AI 正在自動匹配最佳模型並整理新聞..."):
            results, error, model_used = run_curation_pipeline(api_key)
            
            if error:
                st.error(error)
            else:
                # 這裡會顯示您熟悉的通知
                st.toast(f"Using AI Model: {model_used}")
                st.success(f"策展完成！使用模型：{model_used}")
                
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
