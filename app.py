import streamlit as st
import feedparser
import google.generativeai as genai
import json
import time
import ssl

# --- 0. Critical Fix: Global SSL Context Bypass ---
try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context

# --- Config & Layout ---
st.set_page_config(
    page_title="IFA 財經新聞自動策展系統",
    page_icon="📰",
    layout="wide"
)

st.title("📰 IFA 財經新聞自動策展系統")
st.markdown("自動抓取新聞 RSS -> Gemini AI 篩選與撰寫 -> 產出顧問式摘要")

# --- 1. API Key Handling ---
def get_api_key():
    """
    Hybrid API Key Handling:
    1. Try loading from st.secrets
    2. Fallback to sidebar input
    3. Stop if no key
    """
    api_key = None
    
    # Attempt to load from secrets
    try:
        api_key = st.secrets["GOOGLE_API_KEY"]
    except Exception:
        pass
    
    # If not in secrets, ask in sidebar
    if not api_key:
        with st.sidebar:
            st.header("設定")
            api_key = st.text_input("請輸入 Google API Key", type="password")
            st.markdown("[取得 Gemini API Key](https://aistudio.google.com/app/apikey)")
            
    if not api_key:
        st.warning("⚠️ 請提供 Google API Key 以繼續使用 (可於 .streamlit/secrets.toml 設定或左側輸入)")
        st.stop()
        
    return api_key

# --- 2. Model Discovery ---
def get_active_model_name():
    """
    Dynamically find the best available model.
    Priority: "flash" > "pro" > "gemini-pro" (fallback)
    """
    try:
        models = list(genai.list_models())
        available_models = [m.name for m in models if 'generateContent' in m.supported_generation_methods]
        
        # Priority 1: Flash
        for m in available_models:
            if "flash" in m.lower():
                return m
        
        # Priority 2: Pro
        for m in available_models:
            if "pro" in m.lower():
                return m
                
        # Priority 3: First available
        if available_models:
            return available_models[0]
            
    except Exception as e:
        print(f"Model discovery error: {e}")
        
    return "models/gemini-pro"

# --- 3. RSS Data Source ---
def fetch_news():
    """Fetch latest articles from Google News RSS feeds with Mock Data fallback.
       Fetches up to 20 per feed to ensure enough candidates for AI selection.
    """
    # Google News RSS searches (reliable & usually no 403)
    rss_urls = [
        "https://news.google.com/rss/search?q=台灣+保險+OR+長照+OR+理賠+OR+失能+OR+健保+when:1d&hl=zh-TW&gl=TW&ceid=TW:zh-TW",  # Risk & Insurance
        "https://news.google.com/rss/search?q=台灣+遺產稅+OR+贈與稅+OR+信託+OR+遺囑+OR+節稅+when:1d&hl=zh-TW&gl=TW&ceid=TW:zh-TW", # Estate & Tax
        "https://news.google.com/rss/search?q=台灣+ETF+OR+台股+OR+配息+OR+聯準會+降息+when:1d&hl=zh-TW&gl=TW&ceid=TW:zh-TW",      # Investment & Macro
        "https://news.google.com/rss/search?q=台灣+房貸+OR+房市+政策+OR+新青安+OR+詐騙+手法+when:1d&hl=zh-TW&gl=TW&ceid=TW:zh-TW"  # Living, Loans & Fraud
    ]
    
    articles = []
    seen_links = set()
    
    try:
        for url in rss_urls:
            # feedparser handles Google News RSS well without custom headers
            feed = feedparser.parse(url)
            
            # Check if feed is empty or faulty (some 403s result in empty feed without exception)
            if not feed.entries and feed.bozo:
                 # Just skip this feed if it fails cleanly, don't crash everything unless all fail
                 continue

            # Take top 20 from each feed
            for entry in feed.entries[:20]: 
                # Basic dedup
                if entry.link not in seen_links:
                    seen_links.add(entry.link)
                    
                    source = entry.get("source", {}).get("title", "Google News")
                    
                    articles.append({
                        "title": entry.title,
                        "link": entry.link,
                        "summary": entry.get("summary", ""),
                        "published": entry.get("published", ""),
                        "source": source
                    })
        
        if not articles:
            raise Exception("No articles fetched from any source.")
                    
    except Exception as e:
        st.error(f"Detailed Error: {e}")
        st.warning("⚠️ 檢測到網路連線異常，目前顯示測試資料 (Mock Data) 以供預覽。")
        
        # Mock Data Fallback
        mock_articles = [
            {
                "title": "財政部預告修法 2026年起加密貨幣獲利須申報所得稅",
                "source": "測試資料來源",
                "link": "https://google.com",
                "summary": "財政部今日預告，將配合國際反避稅趨勢，納入個人加密資產交易所得，預計 2026 年正式上路。",
                "published": "2026-01-13"
            },
            {
                "title": "退休金準備不足？三招教你補足缺口",
                "source": "測試資料來源",
                "link": "https://google.com",
                "summary": "根據最新調查，國人退休金準備普遍不足。專家建議透過定期定額、長期複利效果，提早規劃退休生活。",
                "published": "2026-01-13"
            },
            {
                "title": "全球經濟放緩 投資人應關注防禦型資產",
                "source": "測試資料來源",
                "link": "https://google.com",
                "summary": "IMF 下修全球經濟成長率，分析師建議投資人調整資產配置，增加債券與公用事業等防禦型類股比重。",
                "published": "2026-01-13"
            },
            {
                "title": "新青安房貸政策成效與風險",
                "source": "測試資料來源",
                "link": "https://google.com",
                "summary": "政府推出新青安房貸政策，市場反應熱烈，但專家提醒需注意利率變動風險與自備款壓力。",
                "published": "2026-01-13"
            },
            {
                "title": "保險新制上路，長照險怎麼買？",
                "source": "測試資料來源",
                "link": "https://google.com",
                "summary": "金管會針對長照險發布新規定，專家解析條款差異，建議民眾依自身需求提早規劃。",
                "published": "2026-01-13"
            },
            {
                "title": "高股息ETF夯，成分股篩選邏輯大公開",
                "source": "測試資料來源",
                "link": "https://google.com",
                "summary": "近期高股息ETF備受投資人青睞，本文深入剖析各大ETF的選股邏輯與績效表現。",
                "published": "2026-01-13"
            }
        ]
        return mock_articles
        
    return articles

# --- 4. Gemini AI Logic ---
def filter_news_with_gemini(articles, model):
    """
    Step A: Batch Select.
    Send titles to Gemini, ask for top 6 indices relevant to Financial Planning.
    """
    titles = [f"{i}. {a['title']}" for i, a in enumerate(articles)]
    titles_text = "\n".join(titles)
    
    prompt = f"""
    You are an expert Financial Advisor editor.
    I have a list of new articles. Please identify the Top 6 articles for a "Full Spectrum Financial Plan".
    
    **Selection Criteria**:
    1. Quantity: **You MUST select EXACTLY 6 articles**. Do not return fewer than 6. Even if some articles are less important, pick the best available ones to fill the quota of 6.
    2. Diversity: MAXIMIZE TOPIC DIVERSITY. Try to pick from DIFFERENT categories (Tax, Insurance, Investment, Retirement, Estate, Loan).
       - Do NOT pick 6 articles about the same topic.
    
    Articles:
    {titles_text}
    
    Return a valid JSON list of integers representing the indices of the selected articles.
    Example output: [0, 4, 7, 12, 15, 18]
    """
    
    valid_indices = []
    try:
        response = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        indices = json.loads(response.text)
        # Ensure indices are integers and within range
        valid_indices = [i for i in indices if isinstance(i, int) and 0 <= i < len(articles)]
    except Exception as e:
        st.error(f"AI Filtering Error: {e}")
        valid_indices = []
        
    # --- Python Fallback Padding ---
    # Ensure we ALWAYS have 6 items. If AI returns < 6, fill with other articles.
    target_count = 6
    if len(valid_indices) < target_count:
        existing_ids = set(valid_indices)
        for i in range(len(articles)):
            if i not in existing_ids:
                valid_indices.append(i)
                existing_ids.add(i)
            
            if len(valid_indices) >= target_count:
                break
    
    return valid_indices[:target_count]

def batch_summarize_articles(articles, model):
    """
    Step B: Batch Rewrite.
    Generate summaries for ALL selected articles in ONE API call to avoid 429 Rate Limits.
    """
    # Construct a single prompt with all articles
    articles_text = ""
    for i, a in enumerate(articles):
        articles_text += f"""
        Article {i}:
        Title: {a['title']}
        Content: {a['summary']}
        ---
        """
        
    prompt = f"""
    You are an expert Independent Financial Advisor (IFA) in Taiwan.
    I have {len(articles)} news articles. Please generate a "Client-Ready Summary" for EACH one.
    
    Input Articles:
    {articles_text}
    
    Output Instructions:
    - Return a **pure JSON list** of objects.
    - Each object must correspond to an article in the input order.
    - Format for each object:
      {{
        "title": "News Title",
        "summary": "One concise sentence summarizing the core event (Traditional Chinese)",
        "advisor_view": ["Viewpoint 1", "Viewpoint 2"],
        "action": "One concrete actionable advice"
      }}
    """
    
    try:
        response = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        results = json.loads(response.text)
        
        # Validate list length
        if isinstance(results, list):
            return results
        else:
            st.error("AI returned invalid format (not a list).")
            return []
            
    except Exception as e:
        st.error(f"Batch Summarization Error: {e}")
        return []

# --- Main Execution Flow ---
def main():
    api_key = get_api_key()
    
    # Configure Gemini
    genai.configure(api_key=api_key)
    
    # Dynamic Model Discovery
    model_name = get_active_model_name()
    model = genai.GenerativeModel(model_name)
    st.toast(f"Using AI Model: {model_name}")

    if st.button("🚀 開始策展 (Start Curation)", type="primary"):
        status_container = st.status("正在處理中...", expanded=True)
        
        with status_container:
            # 1. Fetch
            st.write("📡 正在抓取 RSS 新聞來源...")
            articles = fetch_news()
            
            # Debug: Check Raw Quantity
            st.write(f"🔍 Raw articles fetched: {len(articles)}")
            
            if not articles:
                st.error("未能抓取到任何新聞 (No news fetched).")
                status_container.update(label="失敗", state="error")
                return
            st.write(f"✅ 抓取完成，共 {len(articles)} 篇新聞。")
            
            # 2. Filter
            st.write("🧠 AI 正在篩選最攸關 (Full Spectrum Financial Planning) 的 6 篇文章...")
            selected_indices = filter_news_with_gemini(articles, model)
            
            if not selected_indices:
                st.warning("AI 未能選出適合的文章，或回應格式錯誤。")
                status_container.update(label="完成 (無選錄)", state="complete")
                return
                
            selected_articles = [articles[i] for i in selected_indices]
            st.write(f"✅ 篩選完成，選出索引: {selected_indices}")
            
            # 3. Batch Summarize
            st.write(f"✍️ AI 正在撰寫 {len(selected_articles)} 篇摘要 (Batch Process)...")
            summaries_data = batch_summarize_articles(selected_articles, model)
            
            if not summaries_data:
                st.warning("AI 暫時無法連線 (生成失敗)，請直接參考原文連結。")
                summaries_data = [{"title": a["title"], "summary": "AI 生成失敗", "advisor_view": [], "action": "請閱讀原文"} for a in selected_articles]

            status_container.update(label="🎉 策展完成!", state="complete", expanded=False)

        # 4. Display (Grid Layout)
        st.divider()
        st.subheader(f"📋 策展結果 ({len(summaries_data)} 篇)")
        
        cols = st.columns(2) # Create 2 columns
        
        # Iterate and display in grid
        for index, item in enumerate(summaries_data):
            # Fallback for missing keys
            title = item.get('title', 'Unknown Title')
            summary = item.get('summary', 'No summary.')
            views = item.get('advisor_view', [])
            action = item.get('action', 'No advice.')
            
            # Get original link
            # Safety check: if summaries count mismatches selected count, prevent index error
            if index < len(selected_articles):
                original_link = selected_articles[index]['link']
                original_source = selected_articles[index].get('source', 'News')
            else:
                original_link = "#"
                original_source = "News"
            
            # Construct Display Text
            view_bullets = "\n".join([f"- {v}" for v in views]) if views else ""
            display_text = f"### 📰 {title}\n**摘要**：{summary}\n\n"
            if view_bullets:
                display_text += f"**顧問觀點**：\n{view_bullets}\n\n"
            if action:
                display_text += f"**行動建議**：{action}"
            
            with cols[index % 2]: # Alternate columns
                with st.container(border=True):
                    # Render Content
                    st.markdown(display_text)
                    
                    # Source Link
                    st.caption(f"來源: {original_source} | [閱讀原文]({original_link})")
                    
                    # Copy Block
                    st.code(display_text, language="markdown")
                    
if __name__ == "__main__":
    main()
