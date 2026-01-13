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
    Priority: "1.5-flash" > "flash" > "pro" > "gemini-pro" (fallback)
    """
    try:
        models = list(genai.list_models())
        available_models = [m.name for m in models if 'generateContent' in m.supported_generation_methods]
        
        # Priority 0: Explicit 1.5 Flash (Most Stable Quota)
        for m in available_models:
            if "1.5-flash" in m.lower() and "exp" not in m.lower(): # Avoid excessive experimental versions if possible
                return m

        # Priority 1: Any Flash
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

# ... (fetch_news, filter_news_with_gemini, batch_summarize_articles remain same logic, just indentation check) ...

# --- 5. Caching Pipeline ---
@st.cache_data(ttl=3600, show_spinner="正在執行 AI 策展流程 (每小時更新)...")
def run_curation_pipeline(api_key):
    """
    Executes the full curation flow: Fetch -> Filter -> Summarize.
    Cached for 1 hour to prevent API quota waste.
    """
    # Configure GenAI
    genai.configure(api_key=api_key)
    model_name = get_active_model_name()
    model = genai.GenerativeModel(model_name)
    
    # 1. Fetch
    articles = fetch_news()
    if not articles:
        return {"error": "No news fetched."}
        
    # 2. Filter
    selected_indices = filter_news_with_gemini(articles, model)
    if not selected_indices:
        return {"error": "AI filtering failed."}
        
    selected_articles = [articles[i] for i in selected_indices]
    
    # 3. Batch Summarize
    summaries_data = batch_summarize_articles(selected_articles, model)
    
    return {
        "status": "success",
        "model_used": model_name,
        "data": summaries_data,
        "selected_articles": selected_articles # Keep original for links
    }

# --- Main Execution Flow ---
def main():
    api_key = get_api_key()

    if st.button("🚀 開始策展 (Start Curation)", type="primary"):
        # Run the cached pipeline
        # Note: We pass api_key so cache invalidates if key changes, 
        # but mostly it's for the function to usage it without global scope issues.
        
        result = run_curation_pipeline(api_key)
        
        if result.get("error"):
            st.error(result["error"])
            return

        summaries_data = result.get("data", [])
        selected_articles = result.get("selected_articles", [])
        model_used = result.get("model_used", "Unknown")
        
        st.toast(f"Using AI Model: {model_used} (Cached)")
        st.success(f"🎉 策展完成! (來源: {len(summaries_data)} 篇)")

        # 4. Display (Grid Layout)
        st.divider()
        st.subheader(f"📋 策展結果")
        
        cols = st.columns(2) # Create 2 columns
        
        # Iterate and display in grid
        for index, item in enumerate(summaries_data):
            # Fallback for missing keys
            title = item.get('title', 'Unknown Title')
            summary = item.get('summary', 'No summary.')
            views = item.get('advisor_view', [])
            action = item.get('action', 'No advice.')
            
            # Get original link
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
