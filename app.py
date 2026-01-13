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
# 2. 核心功能函數
# ==========================================

def get_active_model_name():
    """
    自動偵測可用模型，優先順序：
    1. gemini-1.5-flash (最快、配額最高)
    2. gemini-1.5-pro 
    3. 列表中的第一個可用模型
    4. 最終備案: gemini-1.5-flash
    """
    try:
        # 列出所有支援生成內容的模型
        models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        
        # 1. 優先找 gemini-1.5-flash
        for m in models:
            if "gemini-1.5-flash" in m: 
                return m
                
        # 2. 其次找 gemini-1.5-pro
        for m in models:
            if "gemini-1.5-pro" in m: 
                return m
        
        # 3. 如果都沒找到指定名稱，但列表不為空，回傳第一個
        if models:
            return models[0]
            
    except Exception as e:
        print(f"Model discovery error: {e}")
        pass

    # 4. 最終備案
    return "gemini-1.5-flash"

def get_generative_model(api_key):
    """
    工廠函數：建立模型實例
    """
    genai.configure(api_key=api_key)
    model_name = get_active_model_name()
    return genai.GenerativeModel(model_name), model_name

def fetch_news():
    """抓取 Google News RSS 新聞"""
    news_items = []
    
    # 針對台灣理財顧問 (IFA) 的 5 大核心維度
    urls = [
        # 1. 總經與國際：關注聯準會、美股與匯率，影響資產配置
        ("全球總經", "https://news.google.com/rss/search?q=聯準會+OR+美股+OR+美元匯率+OR+央行升息+when:1d&hl=zh-TW&gl=TW&ceid=TW:zh-TW"),
        
        # 2. 投資與市場：台股、ETF 與債券市場動態
        ("投資市場", "https://news.google.com/rss/search?q=台灣+股市+OR+ETF+配息+OR+債券殖利率+when:1d&hl=zh-TW&gl=TW&ceid=TW:zh-TW"),
        
        # 3. 稅務與傳承：高資產客戶最關注的遺產、贈與與信託
        ("稅務傳承", "https://news.google.com/rss/search?q=台灣+遺產稅+OR+贈與稅+OR+房地合一+OR+家族信託+when:1d&hl=zh-TW&gl=TW&ceid=TW:zh-TW"),
        
        # 4. 保險與風險：專注於保障、理賠與長照醫療
        ("保險規劃", "https://news.google.com/rss/search?q=台灣+保險+理賠+OR+實支實付+OR+長照險+OR+失能險+when:1d&hl=zh-TW&gl=TW&ceid=TW:zh-TW"),
        
        # 5. 退休與房產：房市動態與退休金制度
        ("退休房產", "https://news.google.com/rss/search?q=台灣+退休金+OR+勞退+OR+房市+OR+房貸+OR+以房養老+when:1d&hl=zh-TW&gl=TW&ceid=TW:zh-TW")
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

def analyze_and_curate_news(all_raw_news, api_key):
    """
    AI 總編模式：接收所有原始新聞，由 AI 挑選最重要 6 則並撰寫評論。
    """
    model, used_name = get_generative_model(api_key)
    
    # 1. 整理所有新聞為精簡清單供 AI 閱讀
    news_candidates = []
    for i, n in enumerate(all_raw_news):
        news_candidates.append({
            "id": i,
            "category": n['category'],
            "title": n['title'],
            "source": n['source']
        })
    
    news_json_block = json.dumps(news_candidates, ensure_ascii=False)

    # 2. 建構總編級 Prompt
    prompt = f"""
    你是台灣資深財務顧問(IFA)的「新聞總編輯」。
    以下是今日抓取的 {len(all_raw_news)} 則財經新聞候選清單：
    {news_json_block}

    【任務目標】：
    請從中嚴選出 **最關鍵的 6 則** 新聞，製作成給高資產客戶的日報。

    【篩選標準 (由高至低優先)】：
    1. **實質影響**：優先選擇「三讀通過的法規」、「確定的稅務改革」、「聯準會/央行正式決議」、「確定的配息/財報數據」。
    2. **客戶攸關**：與「退休規劃」、「資產傳承」、「房地產稅務」直接相關者優先。
    3. **類別平衡**：請盡量確保「全球總經」、「投資」、「稅務傳承」、「保險」與「退休房產」等領域皆有入選 (除非當天某領域無重要新聞)。
    4. **排除名單**：嚴格排除「純預測/猜測」、「券商行銷廣告」、「與個人理財無關的政治口水」。

    【回傳格式】：
    請回傳一個純 JSON List (Array)，包含挑選出的 6 個物件。每個物件欄位如下：
    {{
        "original_id": (對應上方輸入清單的 id, int),
        "news_summary": "新聞摘要 (一兩句話，繁體中文)",
        "advisor_view": ["觀點1 (請針對資產配置或稅務影響)", "觀點2"],
        "action": "行動建議 (具體、可執行的建議，一兩句話)"
    }}
    
    請直接回傳 JSON，不要有任何 markdown 標記。
    """
    
    try:
        response = model.generate_content(prompt)
        cleaned_text = response.text.replace("```json", "").replace("```", "").strip()
        curated_data = json.loads(cleaned_text)
        return curated_data, used_name
    except Exception as e:
        st.error(f"AI 總編篩選失敗: {e}")
        return [], used_name

# ==========================================
# 3. 整合流程 (加上快取機制)
# ==========================================

@st.cache_data(ttl=3600, show_spinner=False)
def run_curation_pipeline(api_key):
    """
    AI 總編自動策展流程 (Cached 1hr)
    流程：Fetch All -> AI Select Top 6 & Comment -> Merge -> Return
    """
    # 1. 抓取所有新聞 (原料)
    raw_news = fetch_news()
    if not raw_news:
        return None, "無法抓取新聞，請檢查網路連接。", "None"

    # 2. 呼叫 AI 總編進行篩選與點評
    curated_data, model_used = analyze_and_curate_news(raw_news, api_key)
    
    if not curated_data:
        return None, "AI 篩選回應為空，請稍後再試。", model_used

    # 3. 組合結果
    final_results = []
    
    # 建立 id 對照表以加速查詢
    raw_map = {i: n for i, n in enumerate(raw_news)}
    
    for item in curated_data:
        oid = item.get('original_id')
        if oid is not None and oid in raw_map:
            original_news = raw_map[oid]
            
            final_results.append({
                "news": original_news,
                "comment": {
                    "news_summary": item.get('news_summary', '摘要生成中...'),
                    "advisor_view": item.get('advisor_view', []),
                    "action": item.get('action', '詳閱內文')
                }
            })
            
    return final_results, None, model_used

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
                            
                            news_summary = comment.get('news_summary', '摘要生成中...')
                            advisor_view = "\n".join([f"- {p}" for p in comment.get('advisor_view', [])]) if comment else "AI 生成中斷"
                            action = comment.get('action', '建議詳閱原文') if comment else ""
                            
                            content = f"""
### 📰 新聞摘要
{news_summary}

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
