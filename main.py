import logging
import os
import asyncio
import re
import json
import httpx
import yfinance as yf
from bs4 import BeautifulSoup
from typing import Dict, List
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
from dotenv import load_dotenv
from google import genai
import database

# Load environment variables
load_dotenv()

DATA_DIR = os.getenv("DATA_DIR", ".")
HISTORY_FILE = os.path.join(DATA_DIR, "chat_histories.json")

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                data = json.load(f)
                return {int(k): v for k, v in data.items()}
        except Exception:
            pass
    return {}

def save_history(history):
    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(history, f)
    except Exception:
        pass

# Initialize paper trading DB
database.init_db()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Initialize the new Google GenAI client
client = None
if GEMINI_API_KEY:
    client = genai.Client(api_key=GEMINI_API_KEY)

MODEL_NAME = "gemini-2.5-flash-lite"

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Dictionary to hold the last N messages for context
chat_histories: Dict[int, List[str]] = load_history()
MAX_HISTORY_LENGTH = 150  # Increased for daily catchup

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "Hello! 🤖 *Fact Checker & Analyst Bot* is active.\n\n"
        "I spectate this group to intervene if I detect objectively factually incorrect statements.\n"
        "You can also reply to my messages or tag me to ask for my opinion, reasoning, or analysis of the ongoing conversation!\n\n"
        "**New Commands:**\n"
        "• Send a link, and I will automatically reply with a 5-6 bullet summary.\n"
        "• `/analyse` - I'll summarize the recent debate and pick a factual winner.\n"
        "• `/devils_advocate` - I'll read the room's consensus and argue the exact opposite.\n"
        "• `/buy [stock] [amount]` - Paper trade any Stock, Crypto, or Gold with a ₹1 Crore starting balance! (e.g. /buy bitcoin 10, /buy reliance 50)\n"
        "• `/portfolio` - Check your fictional stock portfolio and ROI.\n"
        "• `/leaderboard` - Check the rank of all paper-traders by Net Worth and Rate of Return.\n"
        "• `/settlethis` - I'll generate a Telegram poll based on the current argument.\n"
        "• `/catchup` - Wake up to 100 missed messages? I will brief you like a news anchor."
    )
    await update.message.reply_text(msg, parse_mode='Markdown')

# --- WEB SCRAPING UTILS ---
def extract_urls(text: str) -> List[str]:
    url_pattern = re.compile(r'https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+[^\s]*')
    return url_pattern.findall(text)

async def fetch_article_text(url: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as http_client:
            response = await http_client.get(url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            for script in soup(["script", "style"]):
                script.extract()
            text = soup.get_text(separator=' ')
            lines = (line.strip() for line in text.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            text = '\n'.join(chunk for chunk in chunks if chunk)
            return text[:15000]
    except Exception as e:
        logger.error(f"Error fetching URL {url}: {e}")
        return ""

async def summarize_link(url: str) -> str:
    article_text = await fetch_article_text(url)
    if not article_text:
        return ""
    prompt = (
        "You are an intelligent summarization AI. A user just shared a link in a group chat.\n"
        "CRITICAL INSTRUCTIONS:\n"
        "1. Provide exactly a 5 to 6 bullet point summary of the following article content.\n"
        "2. Be extremely direct and concise.\n"
        "3. NEVER use diplomatic filler like 'As an AI' or 'Here is the summary'. Start straight with the bullets.\n\n"
        f"--- ARTICLE CONTENT ---\n{article_text}"
    )
    try:
        response = await asyncio.to_thread(client.models.generate_content, model=MODEL_NAME, contents=prompt)
        if response and response.text:
            return response.text.strip()
    except Exception:
        pass
    return ""

# --- ADVANCED COMMANDS ---
async def devils_advocate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not client or chat_id not in chat_histories or len(chat_histories[chat_id]) < 5:
        await update.message.reply_text("Need more chat history to play Devil's Advocate!")
        return
    history = "\n".join(chat_histories[chat_id][-30:])
    prompt = (
        "You are playing 'Devil's Advocate' in a family group chat. "
        "Read the recent chat history and identify the current consensus or majority agreement.\n"
        "CRITICAL INSTRUCTIONS:\n"
        "1. Act like the smart, slightly contrarian cousin. Use 'bhaiya/didi/guys'.\n"
        "2. Formulate the absolute strongest, most factual argument *against* what the group is currently agreeing on to spark a deeper debate.\n"
        "3. Respond in exactly 3-5 punchy bullet points. No essays. No 'As an AI'.\n\n"
        f"--- RECENT HISTORY ---\n{history}"
    )
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action='typing')
        response = await asyncio.to_thread(client.models.generate_content, model=MODEL_NAME, contents=prompt)
        await update.message.reply_text(f"😈 *Devil's Advocate Mode:*\n\n{response.text.strip()}", parse_mode='Markdown')
    except Exception as e:
        logger.error(e)

async def catchup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not client or chat_id not in chat_histories or len(chat_histories[chat_id]) < 10:
        await update.message.reply_text("Not enough messages to generate a daily briefing.")
        return
    history = "\n".join(chat_histories[chat_id])
    prompt = (
        "You are the family's resident news anchor cousin. Summarize the missed messages for someone rejoining the chat.\n"
        "CRITICAL INSTRUCTIONS:\n"
        "1. Start with a fun, friendly greeting like 'Here is what you missed, guys!'.\n"
        "2. Group the chatter by topics in 3-5 bullet points. Keep it engaging.\n"
        "3. Highlight if anyone had a massive argument or shared something important.\n"
        "4. No essays, keep it punchy.\n\n"
        f"--- FULL CHAT HISTORY ---\n{history}"
    )
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action='typing')
        response = await asyncio.to_thread(client.models.generate_content, model=MODEL_NAME, contents=prompt)
        await update.message.reply_text(f"📰 *Daily Catch-Up:*\n\n{response.text.strip()}", parse_mode='Markdown')
    except Exception as e:
        logger.error(e)

async def settlethis_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not client or chat_id not in chat_histories or len(chat_histories[chat_id]) < 6:
        await update.message.reply_text("Need more context to identify a debate to settle!")
        return
    history = "\n".join(chat_histories[chat_id][-40:])
    prompt = (
        "Identify the core disagreement in the following group chat.\n"
        "Return ONLY raw JSON with exactly two fields: 'question' (max 255 chars) and 'options' (a list of EXACTLY 2 to 4 distinct viewpoints/options, max 100 chars each).\n"
        "DO NOT output markdown ticks or the word json. ONLY RAW VALID JSON.\n"
        "Example: {\"question\": \"Is Bitcoin a good hedge against inflation right now?\", \"options\": [\"Yes, it is fundamentally sound\", \"No, it is too volatile\"]}\n"
        f"--- HISTORY ---\n{history}"
    )
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action='typing')
        response = await asyncio.to_thread(client.models.generate_content, model=MODEL_NAME, contents=prompt)
        text = response.text.replace("```json", "").replace("```", "").strip()
        data = json.loads(text)
        if "question" in data and "options" in data:
            await context.bot.send_poll(chat_id=chat_id, question=f"⚖️ Lets Settle This: {data['question']}", options=data['options'], is_anonymous=False)
        else:
            await update.message.reply_text("Couldn't figure out exactly what the debate was to make a poll.")
    except Exception as e:
        logger.error(e)
        await update.message.reply_text("Failed to generate a poll.")

async def buy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_name = update.message.from_user.first_name
    text = update.message.text
    
    prompt = (
        "A user in an Indian telegram group wants to paper-trade an asset. Extract the correct Yahoo Finance ticker symbol and the quantity.\n"
        f"Message: '{text}'\n"
        "CRITICAL INSTRUCTIONS:\n"
        "1. Identify the asset and return its EXACT Yahoo Finance ticker.\n"
        "   - For Indian stocks: Append '.NS' for NSE or '.BO' for BSE (e.g. Reliance -> 'RELIANCE.NS', Tata Motors -> 'TATAMOTORS.NS').\n"
        "   - For US Stocks: Keep normal (e.g. 'AAPL', 'TSLA').\n"
        "   - For Crypto or Commodities (Bitcoin, Gold, Silver): Must format as pair against INR if possible (e.g. 'BTC-INR', or 'GC=F' for Gold).\n"
        "2. Identify the quantity. If no quantity is specified, assume 1.\n"
        "3. Return ONLY raw valid JSON exactly like this: {\"ticker\": \"RELIANCE.NS\", \"quantity\": 10}\n"
        "4. DO NOT wrap with markdown json tags."
    )
    try:
        response = await asyncio.to_thread(client.models.generate_content, model=MODEL_NAME, contents=prompt)
        text_resp = response.text.replace("```json", "").replace("```", "").strip()
        data = json.loads(text_resp)
        ticker = data.get("ticker", "").upper()
        quantity = float(data.get("quantity", 1))
        
        if not ticker:
            await update.message.reply_text("Could not identify the stock ticker. Try again!")
            return
            
        stock = yf.Ticker(ticker)
        current_price = stock.fast_info.get("lastPrice")
        
        # fallback for crypto sometimes missing on INR pair
        if not current_price and "-INR" in ticker:
             fallback = ticker.replace("-INR","-USD")
             stock = yf.Ticker(fallback)
             current_price = stock.fast_info.get("lastPrice")
             # rough conversion if usd
             if current_price: current_price *= 86.0  
             ticker = fallback
             
        if not current_price:
            await update.message.reply_text(f"Could not fetch real-time price for {ticker}.")
            return
            
        total_cost = current_price * quantity
        balance = database.get_balance(user_id)
        
        if balance < total_cost:
            await update.message.reply_text(f"❌ *Failed:* You need ₹{total_cost:,.2f} for {quantity}x {ticker}, but your balance is only ₹{balance:,.2f}.", parse_mode='Markdown')
            return
            
        database.update_balance(user_id, balance - total_cost)
        database.buy_stock(user_id, user_name, ticker, quantity, current_price)
        
        await update.message.reply_text(f"✅ *Paper Trade Executed!*\n\n{user_name} bought **{quantity}x {ticker}** at **₹{current_price:,.2f}**.\nTotal value: **₹{total_cost:,.2f}**\nRemaining Cash: **₹{(balance - total_cost):,.2f}**", parse_mode='Markdown')
    except Exception as e:
        logger.error(e)
        await update.message.reply_text("Failed to process paper trade. Try clarifying the stock name.")

async def portfolio_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_name = update.message.from_user.first_name
    
    balance = database.get_balance(user_id)
    holdings = database.get_portfolio(user_id)
    
    if not holdings:
        await update.message.reply_text(f"💼 *{user_name}'s Portfolio*\n\nCash: **₹{balance:,.2f}**\n\nYou own no assets right now. Use `/buy` to invest!", parse_mode='Markdown')
        return
        
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
    msg = f"💼 *{user_name}'s Portfolio*\n\nCash: **₹{balance:,.2f}**\n\n*Holdings:*\n"
    total_value = balance
    total_invested = 0
    
    for h in holdings:
        ticker = h['ticker']
        shares = h['shares']
        avg = h['avg_price']
        total_invested += (shares * avg)
        try:
            curr = yf.Ticker(ticker).fast_info.get("lastPrice", avg)
            val = shares * curr
            total_value += val
            pct_change = ((curr - avg) / avg) * 100
            emoji = "🟢" if pct_change >= 0 else "🔴"
            msg += f"• **{ticker}**: {shares} QTY @ ₹{curr:,.2f} (Avg ₹{avg:,.2f}) {emoji} `{pct_change:+.2f}%`\n"
        except Exception:
            msg += f"• **{ticker}**: {shares} QTY (Avg ₹{avg:,.2f})\n"
            total_value += (shares * avg)
            
    total_roi = ((total_value - 10000000.0) / 10000000.0) * 100
    emoji_roi = "🟢" if total_roi >= 0 else "🔴"
    msg += f"\n*Net Worth:* **₹{total_value:,.2f}**\n*All-Time ROI:* {emoji_roi} `{total_roi:+.2f}%`"
    await update.message.reply_text(msg, parse_mode='Markdown')

async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    investors = database.get_all_investors()
    if not investors:
        await update.message.reply_text("No one has started trading yet! Use `/buy` to get started.", parse_mode="Markdown")
        return
        
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
    
    leaderboard = []
    INITIAL_CASH = 10000000.0
    
    for inv in investors:
        uid = inv["user_id"]
        name = inv["user_name"]
        
        balance = database.get_balance(uid)
        holdings = database.get_portfolio(uid)
        
        net_worth = balance
        for h in holdings:
            try:
                curr = yf.Ticker(h['ticker']).fast_info.get("lastPrice", h['avg_price'])
                net_worth += (h['shares'] * curr)
            except Exception:
                net_worth += (h['shares'] * h['avg_price'])
                
        roi_pct = ((net_worth - INITIAL_CASH) / INITIAL_CASH) * 100
        leaderboard.append({"name": name, "nw": net_worth, "roi": roi_pct})
        
    # Sort for Absolute Net Worth
    lb_nw = sorted(leaderboard, key=lambda x: x["nw"], reverse=True)
    
    # Sort for Best Investors (ROI %)
    lb_roi = sorted(leaderboard, key=lambda x: x["roi"], reverse=True)
    
    msg = "🏆 *GROUP TRADING LEADERBOARD*\n\n"
    
    msg += "💰 *By Total Net Worth (Absolute)*\n"
    for i, inv in enumerate(lb_nw):
        medal = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else "  "
        msg += f"{medal} **{inv['name']}** - ₹{inv['nw']:,.2f}\n"

    msg += "\n📈 *By Best Investors (ROI %)*\n"
    for i, inv in enumerate(lb_roi):
        medal = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else "  "
        emoji = "🟢" if inv['roi'] >= 0 else "🔴"
        msg += f"{medal} **{inv['name']}** - {emoji} `{inv['roi']:+.2f}%`\n"

    await update.message.reply_text(msg, parse_mode='Markdown')

# --- ORIGINAL COMMANDS ---
async def analyse_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not client or chat_id not in chat_histories or len(chat_histories[chat_id]) < 5:
        await update.message.reply_text("I need a bit more chat history (at least 5 messages) to analyze the debate!")
        return
    history_context = "\n".join(chat_histories[chat_id][-30:])
    prompt = (
        "You are an intelligent, objective referee and factual analyst in a group chat.\n"
        "The user has commanded you to analyze the recent conversation.\n"
        "CRITICAL INSTRUCTIONS:\n"
        "1. Read the following chat history (formatted as Name: Message).\n"
        "2. Concisely summarize the main arguments being made.\n"
        "3. Explicitly state your take on WHO is winning the debate or who is more realistic in their analysis.\n"
        "4. Name the person who has the better arguments and explain WHY based strictly on facts, realism, and logic.\n"
        "5. NEVER use diplomatic phrases like 'As an AI, I am neutral'. You MUST pick a winner or explicitly call out the better argument.\n"
        "6. ABSOLUTE RULE: Do not use any profanity. Do not abuse constitutional posts.\n\n"
        f"--- RECENT CHAT HISTORY (Last 30 messages) ---\n{history_context}"
    )
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action='typing')
        response = await asyncio.to_thread(client.models.generate_content, model=MODEL_NAME, contents=prompt)
        await update.message.reply_text(f"📊 *Debate Analysis:*\n\n{response.text.strip()}", parse_mode='Markdown')
    except Exception as e:
        logger.error(e)

async def analyze_message_with_gemini(chat_history_str: str, current_message: str, is_direct_query: bool = False) -> str:
    if not client:
        return ""
    try:
        if is_direct_query:
            prompt = (
                "You are an intelligent, objective, and highly knowledgeable AI Assistant acting as one of the cousins in a family group chat discussing business, finance, news, or general topics. "
                "Someone has directly asked you for your input, reasoning, or opinion. "
                "CRITICAL INSTRUCTIONS:\n"
                "1. PERSONA: You must act like a smart, friendly cousin. Occasionally use terms like 'bhaiya', 'didi', or 'guys'. Keep the tone homely but incredibly factual.\n"
                "2. NO ESSAYS. You must respond using concise, hard-hitting bullet points. Use your intelligence to determine how many points are needed to accurately balance the topic, but NEVER exceed 10 points.\n"
                "3. NEVER use diplomatic phrases like 'As an AI, I am neutral'.\n"
                "4. If asked about an opinion or a debatable topic, present both sides of the argument fairly within those bullets, then give a realistic conclusion.\n"
                "5. ABSOLUTE RULE: Do NOT use any profanity. Do NOT abuse or disrespect constitutional posts.\n\n"
                "Below is the recent chat history for context, followed by the explicit message/query directed at you.\n\n"
                f"--- RECENT CHAT HISTORY ---\n{chat_history_str}\n\n"
                f"--- DIRECT QUERY FOR YOU ---\n{current_message}"
            )
        else:
            prompt = (
                "You are a strict, objective fact-checker acting as a smart cousin spectating a family group chat. "
                "Your job is to read the latest message in the context of the recent conversation, and determine if the latest statement is fundamentally and objectively factually incorrect. "
                "CRITICAL INSTRUCTIONS:\n"
                "1. If the statement is a subjective opinion, an argument, a debatable viewpoint, or simply mostly accurate, you MUST reply with ONLY the exact string 'NO_CORRECTION_NEEDED'.\n"
                "2. If there is a blatant factual error, intervene and lightly correct your cousins (you can use 'bhaiya/didi/guys'). Provide the correct facts immediately in 1 to 2 short bullet points. No essays.\n"
                "3. NEVER use diplomatic phrases or meta-commentary about being an AI.\n"
                "4. ABSOLUTE RULE: Do NOT use any profanity. Do NOT abuse constitutional posts.\n"
                "Do not intervene for minor technicalities; only jump in when something is demonstrably false and misleading.\n\n"
                "Below is the recent chat history for context, followed by the latest message.\n\n"
                f"--- RECENT CHAT HISTORY ---\n{chat_history_str}\n\n"
                f"--- LATEST MESSAGE TO CHECK ---\n{current_message}"
            )
        response = await asyncio.to_thread(client.models.generate_content, model=MODEL_NAME, contents=prompt)
        if response and response.text:
            return response.text.strip()
    except Exception:
        pass
    return ""

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
        
    text = update.message.text
    chat_id = update.effective_chat.id
    chat_type = update.effective_chat.type
    user_name = update.message.from_user.first_name if update.message.from_user else "User"
    bot_username = context.bot.username
    
    if chat_id not in chat_histories:
        chat_histories[chat_id] = []
        
    formatted_msg = f"{user_name}: {text}"
    chat_histories[chat_id].append(formatted_msg)
    
    if len(chat_histories[chat_id]) > MAX_HISTORY_LENGTH:
        chat_histories[chat_id].pop(0)
        
    save_history(chat_histories)

    # Summarize URLs
    urls = extract_urls(text)
    if urls:
        first_url = urls[0]
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action='typing')
        except:
            pass
        summary = await summarize_link(first_url)
        if summary:
            await update.message.reply_text(f"🔗 *Article Summary:*\n\n{summary}", parse_mode='Markdown', reply_to_message_id=update.message.id)
            return

    history_context = "\n".join(chat_histories[chat_id][-30:]) if len(chat_histories[chat_id]) > 1 else "(No prior context)"

    is_reply_to_bot = (
        update.message.reply_to_message and 
        update.message.reply_to_message.from_user and
        update.message.reply_to_message.from_user.id == context.bot.id
    )
    is_mentioned = f"@{bot_username}" in text if bot_username else False
    is_direct_query = is_reply_to_bot or is_mentioned or chat_type == 'private'

    if is_direct_query:
        query_text = text
        if bot_username:
            query_text = query_text.replace(f"@{bot_username}", "").strip()
        if not query_text and update.message.reply_to_message and update.message.reply_to_message.text:
             query_text = f"[Replying to: {update.message.reply_to_message.text}]"
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action='typing')
        except Exception:
            pass
        result = await analyze_message_with_gemini(history_context, f"{user_name}: {query_text}", is_direct_query=True)
        if result:
            await update.message.reply_text(result, parse_mode='Markdown')
            
    else:
        # Fact-Checking specator mode
        if len(text.split()) > 4: 
            result = await analyze_message_with_gemini(history_context, f"{user_name}: {text}", is_direct_query=False)
            if result and result.strip() != "NO_CORRECTION_NEEDED":
                 await update.message.reply_text(f"⚠️ *Fact Check:*\n\n{result}", parse_mode='Markdown', reply_to_message_id=update.message.id)

def main():
    if not TOKEN:
        return
    application = ApplicationBuilder().token(TOKEN).build()
    
    # Adding original + new commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("analyse", analyse_command))
    application.add_handler(CommandHandler("devils_advocate", devils_advocate_command))
    application.add_handler(CommandHandler("catchup", catchup_command))
    application.add_handler(CommandHandler("settlethis", settlethis_command))
    application.add_handler(CommandHandler("buy", buy_command))
    application.add_handler(CommandHandler("portfolio", portfolio_command))
    application.add_handler(CommandHandler("leaderboard", leaderboard_command))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), text_handler))

    logger.info(f"Fact Checker & Analyst Bot is running... (Model: {MODEL_NAME})")
    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
