import logging
import os
import asyncio
import re
import httpx
from bs4 import BeautifulSoup
from typing import Dict, List
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
from dotenv import load_dotenv
from google import genai
from google.genai import types

# Load environment variables
load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Initialize the new Google GenAI client
client = None
if GEMINI_API_KEY:
    client = genai.Client(api_key=GEMINI_API_KEY)

# Use Gemini 2.5 Flash Lite per user request
MODEL_NAME = "gemini-2.5-flash-lite"

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Dictionary to hold the last N messages for context
chat_histories: Dict[int, List[str]] = {}
MAX_HISTORY_LENGTH = 30  # Increased to 30 as requested

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "Hello! 🤖 *Fact Checker & Analyst Bot* is active.\n\n"
        "I spectate this group to intervene if I detect objectively factually incorrect statements.\n"
        "You can also reply to my messages or tag me to ask for my opinion, reasoning, or analysis of the ongoing conversation!\n\n"
        "**New Features:**\n"
        "• Send a link, and I will automatically reply with a 5-6 bullet summary.\n"
        "• Type `/analyse` to get a summary of the debate over the last 30 messages, including my take on who has the most realistic and factual arguments."
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
            
            # Remove scripts and styles
            for script in soup(["script", "style"]):
                script.extract()
                
            text = soup.get_text(separator=' ')
            # Clean up whitespace
            lines = (line.strip() for line in text.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            text = '\n'.join(chunk for chunk in chunks if chunk)
            
            # Return up to 15,000 characters to prevent massive payloads
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
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=MODEL_NAME, 
            contents=prompt
        )
        if response and response.text:
            return response.text.strip()
    except Exception as e:
        logger.error(f"Error summarizing link: {e}")
    return ""

# --- COMMANDS ---
async def analyse_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    if not client:
        await update.message.reply_text("I need a GEMINI_API_KEY to do that.")
        return
        
    if chat_id not in chat_histories or len(chat_histories[chat_id]) < 5:
        await update.message.reply_text("I need a bit more chat history (at least 5 messages) to analyze the debate!")
        return
        
    history_context = "\n".join(chat_histories[chat_id])
    
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
        f"--- RECENT CHAT HISTORY (Last {MAX_HISTORY_LENGTH} messages) ---\n{history_context}"
    )
    
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action='typing')
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=MODEL_NAME, 
            contents=prompt
        )
        if response and response.text:
            await update.message.reply_text(f"📊 *Debate Analysis:*\n\n{response.text.strip()}", parse_mode='Markdown')
        else:
            await update.message.reply_text("I couldn't analyze the chat right now.")
    except Exception as e:
        logger.error(f"Error in analyse command: {e}")
        await update.message.reply_text("An error occurred while analyzing the chat.")


# --- MAIN CHAT HANDLER ---
async def analyze_message_with_gemini(chat_history_str: str, current_message: str, is_direct_query: bool = False) -> str:
    """
    Sends the recent chat history and current message to Gemini for analysis.
    """
    if not client:
        return ""
        
    try:
        if is_direct_query:
            prompt = (
                "You are an intelligent, objective, and highly knowledgeable AI Assistant in a group chat of friends discussing business, finance, news, or general topics. "
                "Someone has directly asked you for your input, reasoning, or opinion. "
                "CRITICAL INSTRUCTIONS:\n"
                "1. NEVER use diplomatic phrases like 'As an AI, I am neutral' or 'I must remain objective.'\n"
                "2. NO ESSAYS. You must respond in exactly 3 to 4 concise, hard-hitting bullet points. Do not write paragraphs of text.\n"
                "3. If asked about an opinion or a debatable topic, present both sides of the argument fairly within those bullets, then give a realistic conclusion.\n"
                "4. ABSOLUTE RULE: Do NOT use any profanity. Do NOT abuse or disrespect constitutional posts (e.g., the Prime Minister, President, etc.). Express critiques respectfully.\n\n"
                "Below is the recent chat history for context, followed by the explicit message/query directed at you.\n\n"
                f"--- RECENT CHAT HISTORY ---\n{chat_history_str}\n\n"
                f"--- DIRECT QUERY FOR YOU ---\n{current_message}"
            )
        else:
            prompt = (
                "You are a strict and objective fact-checker spectating a group chat. "
                "Your job is to read the latest message in the context of the recent conversation, and determine if the latest statement is fundamentally and objectively factually incorrect. "
                "CRITICAL INSTRUCTIONS:\n"
                "1. If the statement is a subjective opinion, an argument, a debatable viewpoint, or simply mostly accurate, you MUST reply with ONLY the exact string 'NO_CORRECTION_NEEDED'.\n"
                "2. If there is a blatant factual error, intervene and provide the correct facts immediately in 1 to 2 short bullet points. No essays.\n"
                "3. NEVER use diplomatic phrases or meta-commentary about being an AI.\n"
                "4. ABSOLUTE RULE: Do NOT use any profanity. Do NOT abuse or disrespect constitutional posts.\n"
                "Do not intervene for minor technicalities; only jump in when something is demonstrably false and misleading.\n\n"
                "Below is the recent chat history for context, followed by the latest message.\n\n"
                f"--- RECENT CHAT HISTORY ---\n{chat_history_str}\n\n"
                f"--- LATEST MESSAGE TO CHECK ---\n{current_message}"
            )

        # Using the new google-genai syntax
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=MODEL_NAME, 
            contents=prompt
        )
        
        if response and response.text:
            return response.text.strip()
            
    except Exception as e:
        logger.error(f"Error querying Gemini: {e}")
        
    return ""

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
        
    text = update.message.text
    chat_id = update.effective_chat.id
    chat_type = update.effective_chat.type
    user_name = update.message.from_user.first_name if update.message.from_user else "User"
    bot_username = context.bot.username
    
    # Update chat history
    if chat_id not in chat_histories:
        chat_histories[chat_id] = []
        
    formatted_msg = f"{user_name}: {text}"
    chat_histories[chat_id].append(formatted_msg)
    
    # Keep history bounded based on the new MAX limit (30)
    if len(chat_histories[chat_id]) > MAX_HISTORY_LENGTH:
        chat_histories[chat_id].pop(0)

    # 1. Feature: Automatic Link Summarization
    urls = extract_urls(text)
    if urls:
        first_url = urls[0] # Just summarize the first link if multiple are sent
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action='typing')
        except:
            pass
            
        summary = await summarize_link(first_url)
        if summary:
            # Send the summary, then return early so we don't double dip into a fact check
            await update.message.reply_text(f"🔗 *Article Summary:*\n\n{summary}", parse_mode='Markdown', reply_to_message_id=update.message.id)
            return

    # Build history context string
    history_context = "\n".join(chat_histories[chat_id][:-1]) if len(chat_histories[chat_id]) > 1 else "(No prior context)"

    # Check if the bot is explicitly mentioned or replied to
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
            await update.message.reply_text("I'm sorry, I couldn't process that right now. Ensure GEMINI_API_KEY is active and valid.")
            
    else:
        # Spectator Mode Fact-Checking
        if len(text.split()) > 4: 
            result = await analyze_message_with_gemini(history_context, f"{user_name}: {text}", is_direct_query=False)
            
            if result and result.strip() != "NO_CORRECTION_NEEDED":
                 intervention_msg = f"⚠️ *Fact Check:*\n\n{result}"
                 await update.message.reply_text(intervention_msg, parse_mode='Markdown', reply_to_message_id=update.message.id)

def main():
    if not TOKEN:
        logger.error("Error: TELEGRAM_BOT_TOKEN not found in environment.")
        return
        
    if not GEMINI_API_KEY:
        logger.warning("Warning: GEMINI_API_KEY not found in environment. Bot will not be able to answer/fact-check.")
        
    application = ApplicationBuilder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("analyse", analyse_command))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), text_handler))

    logger.info(f"Fact Checker & Analyst Bot is running... (Model: {MODEL_NAME})")
    
    # drop_pending_updates=True clears out old messages and fixes the "Conflict" error 
    # if multiple instances or ghost processes try to poll at once
    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
