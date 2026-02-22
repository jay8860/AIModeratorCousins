import logging
import os
import asyncio
from typing import Dict, List
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
from dotenv import load_dotenv
import google.generativeai as genai

# Load environment variables
load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# Use Gemini 1.5 Flash - extremely fast, great for streaming background texts
# Using the -latest suffix as the older SDK can sometimes fail to resolve the base alias
MODEL_NAME = "gemini-1.5-flash-latest"

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Dictionary to hold the last N messages for context: { chat_id: [ list of message strings ] }
chat_histories: Dict[int, List[str]] = {}
MAX_HISTORY_LENGTH = 15

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "Hello! 🤖 *Fact Checker & Analyst Bot* is active.\n\n"
        "I spectate this group to intervene if I detect objectively factually incorrect statements.\n"
        "You can also reply to my messages or tag me to ask for my opinion, reasoning, or analysis of the ongoing conversation!"
    )
    await update.message.reply_text(msg, parse_mode='Markdown')

async def analyze_message_with_gemini(chat_history_str: str, current_message: str, is_direct_query: bool = False) -> str:
    """
    Sends the recent chat history and current message to Gemini for analysis.
    """
    if not GEMINI_API_KEY:
        return ""
        
    try:
        model = genai.GenerativeModel(MODEL_NAME)
        
        if is_direct_query:
            prompt = (
                "You are an intelligent, objective, and highly knowledgeable AI Assistant in a group chat of friends discussing business, finance, news, or general topics. "
                "Someone has directly asked you for your input, reasoning, or opinion. "
                "Below is the recent chat history for context, followed by the explicit message/query directed at you. "
                "Provide a clear, factual, well-reasoned, and helpful response. If they are asking for analysis of an argument, provide your insights based on the context.\n\n"
                f"--- RECENT CHAT HISTORY ---\n{chat_history_str}\n\n"
                f"--- DIRECT QUERY FOR YOU ---\n{current_message}"
            )
        else:
            prompt = (
                "You are a strict and objective fact-checker spectating a group chat. "
                "Your job is to read the latest message in the context of the recent conversation, and determine if the latest statement is fundamentally and objectively factually incorrect. "
                "Below is the recent chat history for context, followed by the latest message. "
                "If the latest message contains a blatant factual error, explain why and provide the correct facts. "
                "If the statement is a subjective opinion, an argument, a debatable viewpoint, or simply mostly accurate, you MUST reply with ONLY the exact string 'NO_CORRECTION_NEEDED'. "
                "Do not intervene for minor technicalities; only jump in when something is demonstrably false and misleading.\n\n"
                f"--- RECENT CHAT HISTORY ---\n{chat_history_str}\n\n"
                f"--- LATEST MESSAGE TO CHECK ---\n{current_message}"
            )

        response = await asyncio.to_thread(model.generate_content, prompt)
        
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
    
    # Keep history bounded
    if len(chat_histories[chat_id]) > MAX_HISTORY_LENGTH:
        chat_histories[chat_id].pop(0)
        
    # Build history context string (excluding the very latest message)
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
        # Prepare text by removing bot mention
        query_text = text
        if bot_username:
            query_text = query_text.replace(f"@{bot_username}", "").strip()
            
        # If it was just a tag " @bot ", try to use the replied message text as the query context
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
            await update.message.reply_text("I'm sorry, I couldn't process that right now. Ensure GEMINI_API_KEY is active.")
            
    else:
        # Spectator Mode Fact-Checking
        # Only check if the message is somewhat conversational
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
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), text_handler))

    logger.info("Fact Checker & Analyst Bot is running...")
    application.run_polling()

if __name__ == '__main__':
    main()
