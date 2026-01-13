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
    你是台灣資深財務顧問(IFA)。以下是 6 則精選財經
