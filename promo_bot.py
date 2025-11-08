import os
import logging
import sqlite3
import asyncio
from datetime import datetime
from threading import Thread
from flask import Flask
import requests

# Telegram Bot Imports
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

# Flask app for health checks
app = Flask(__name__)

# Configuration
BOT_TOKEN = os.getenv('BOT_TOKEN', 'YOUR_BOT_TOKEN_HERE')
RENDER = os.getenv('RENDER', 'False').lower() == 'true'
PORT = int(os.getenv('PORT', 5000))  # Render automatically sets PORT environment variable

# Updated Pricing Configuration
PRICING_PLANS = {
    500: 50,
    1000: 100,
    3000: 150,
    5000: 200,
    10000: 350  # Special offer price
}

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Database Functions
def init_db():
    """Initialize the database with required tables."""
    try:
        conn = sqlite3.connect('promotion_bot.db')
        c = conn.cursor()
        
        # Users table
        c.execute('''CREATE TABLE IF NOT EXISTS users
                     (user_id INTEGER PRIMARY KEY, 
                      username TEXT, 
                      balance INTEGER DEFAULT 0,
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        
        # Channels table
        c.execute('''CREATE TABLE IF NOT EXISTS channels
                     (channel_id TEXT PRIMARY KEY, 
                      title TEXT, 
                      admin_id INTEGER,
                      member_count INTEGER DEFAULT 0,
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        
        # Campaigns table
        c.execute('''CREATE TABLE IF NOT EXISTS campaigns
                     (campaign_id INTEGER PRIMARY KEY AUTOINCREMENT,
                      channel_id TEXT,
                      admin_id INTEGER,
                      target_subs INTEGER,
                      cost_stars INTEGER,
                      status TEXT DEFAULT 'active',
                      progress INTEGER DEFAULT 0,
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        
        # Orders table
        c.execute('''CREATE TABLE IF NOT EXISTS orders
                     (order_id INTEGER PRIMARY KEY AUTOINCREMENT,
                      user_id INTEGER,
                      campaign_id INTEGER,
                      stars_spent INTEGER,
                      status TEXT DEFAULT 'pending',
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        
        conn.commit()
        logger.info("Database initialized successfully")
        
    except Exception as e:
        logger.error(f"Database initialization error: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

def get_db_connection():
    """Get database connection."""
    return sqlite3.connect('promotion_bot.db')

# Health Check Endpoints
@app.route('/')
def home():
    return {
        "status": "online", 
        "service": "Telegram Promotion Bot",
        "timestamp": datetime.now().isoformat(),
        "version": "1.0.0",
        "port": PORT
    }

@app.route('/health')
def health():
    """Health check endpoint for Render."""
    try:
        # Check database connection
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT 1")
        conn.close()
        
        return {
            "status": "healthy",
            "database": "connected",
            "timestamp": datetime.now().isoformat(),
            "port": PORT
        }, 200
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {
            "status": "unhealthy",
            "error": str(e),
            "timestamp": datetime.now().isoformat(),
            "port": PORT
        }, 500

@app.route('/stats')
def stats():
    """Bot statistics endpoint."""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        c.execute("SELECT COUNT(*) FROM users")
        user_count = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM channels")
        channel_count = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM campaigns WHERE status = 'active'")
        active_campaigns = c.fetchone()[0]
        
        conn.close()
        
        return {
            "users": user_count,
            "channels": channel_count,
            "active_campaigns": active_campaigns,
            "timestamp": datetime.now().isoformat(),
            "port": PORT
        }
    except Exception as e:
        logger.error(f"Stats endpoint error: {e}")
        return {"error": str(e)}, 500

# Keep Alive Function
def keep_alive():
    """Ping the bot periodically to keep it alive on Render."""
    def run():
        while True:
            try:
                if RENDER:
                    # Get the Render external URL
                    service_url = os.getenv('RENDER_EXTERNAL_URL', '')
                    if service_url:
                        # Use the service URL that Render provides
                        response = requests.get(f"{service_url}/health", timeout=10)
                        logger.info(f"Keep-alive ping to {service_url}: {response.status_code}")
                    else:
                        # Fallback: try to construct URL from Render info
                        render_service = os.getenv('RENDER_SERVICE_NAME', '')
                        if render_service:
                            service_url = f"https://{render_service}.onrender.com"
                            response = requests.get(f"{service_url}/health", timeout=10)
                            logger.info(f"Keep-alive ping to {service_url}: {response.status_code}")
                        else:
                            logger.info("Keep-alive: No Render URL available, using localhost")
                            response = requests.get(f"http://localhost:{PORT}/health", timeout=10)
                            logger.info(f"Keep-alive ping to localhost:{PORT}: {response.status_code}")
                else:
                    # Local development - ping localhost
                    response = requests.get(f"http://localhost:{PORT}/health", timeout=10)
                    logger.info(f"Keep-alive ping to localhost:{PORT}: {response.status_code}")
                
                # Sleep for 5 minutes (300 seconds)
                asyncio.run(asyncio.sleep(300))
            except Exception as e:
                logger.error(f"Keep-alive error: {e}")
                # Retry after 2 minutes on error
                asyncio.run(asyncio.sleep(120))
    
    thread = Thread(target=run)
    thread.daemon = True
    thread.start()

# Telegram Bot Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send welcome message and main menu."""
    user_id = update.effective_user.id
    username = update.effective_user.username or "Unknown"
    
    # Add user to database
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
    conn.commit()
    conn.close()
    
    keyboard = [
        [InlineKeyboardButton("💰 Check Balance", callback_data="balance")],
        [InlineKeyboardButton("📢 Promotion Plans", callback_data="plans")],
        [InlineKeyboardButton("➕ Add Channel", callback_data="add_channel")],
        [InlineKeyboardButton("📊 My Campaigns", callback_data="my_campaigns")],
        [InlineKeyboardButton("ℹ️ Help", callback_data="help")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    welcome_text = """
🤖 **Promotion Manager Bot**

I help you organize legitimate promotion campaigns for your channels/groups.

**Legal & Safe:**
✅ Organic growth strategies
✅ Telegram ToS compliant  
✅ No artificial inflation

**Special Offer:** 10,000 subscribers campaign now only 350 ⭐!

Select an option below:
    """
    
    await update.message.reply_text(
        welcome_text.strip(),
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show pricing plans."""
    query = update.callback_query
    await query.answer()
    
    plans_text = "📊 **Promotion Campaign Plans**\n\n"
    plans_text += "These packages include legitimate promotion services:\n\n"
    
    for subs, stars in PRICING_PLANS.items():
        plans_text += f"• {subs:,} subscriber campaign - {stars} ⭐\n"
    
    plans_text += "\n🎉 **Special Offer:** 10K subscribers now only 350 stars!\n\n"
    plans_text += "*Services include: cross-promotion, content marketing, and organic growth strategies*"
    
    keyboard = [
        [InlineKeyboardButton(f"Buy {subs} - {stars}⭐", callback_data=f"buy_{subs}")]
        for subs, stars in PRICING_PLANS.items()
    ]
    keyboard.append([InlineKeyboardButton("💰 Check Balance", callback_data="balance")])
    keyboard.append([InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(plans_text, reply_markup=reply_markup, parse_mode='Markdown')

async def check_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check user balance."""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    result = c.fetchone()
    balance = result[0] if result else 0
    conn.close()
    
    keyboard = [
        [InlineKeyboardButton("📢 View Plans", callback_data="plans")],
        [InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"💰 **Your Balance**\n\nCurrent stars: **{balance} ⭐**\n\n"
        "Use stars to create promotion campaigns for your channels.",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def handle_purchase(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle package purchase selection."""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    if data.startswith("buy_"):
        try:
            target_subs = int(data.split("_")[1])
            cost = PRICING_PLANS.get(target_subs, 0)
            
            user_id = query.from_user.id
            
            # Check user balance
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
            result = c.fetchone()
            balance = result[0] if result else 0
            conn.close()
            
            if balance >= cost:
                # User can afford
                offer_text = f"🎉 **Great Choice!** 🎉\n\n" if target_subs == 10000 else ""
                offer_text += f"You selected: **{target_subs:,} subscribers** for **{cost} ⭐**\n\n"
                offer_text += f"Your balance: {balance} ⭐\n"
                offer_text += f"After purchase: {balance - cost} ⭐\n\n"
                offer_text += "Ready to start your campaign?"
                
                keyboard = [
                    [InlineKeyboardButton("✅ Confirm Purchase", callback_data=f"confirm_{target_subs}")],
                    [InlineKeyboardButton("🔙 View Other Plans", callback_data="plans")]
                ]
            else:
                # Insufficient balance
                offer_text = f"❌ **Insufficient Balance**\n\n"
                offer_text += f"Required: {cost} ⭐\n"
                offer_text += f"Your balance: {balance} ⭐\n"
                offer_text += f"Need: {cost - balance} more stars\n\n"
                offer_text += "Please add more stars to your balance."
                
                keyboard = [
                    [InlineKeyboardButton("💰 Add Stars", callback_data="add_stars")],
                    [InlineKeyboardButton("🔙 View Plans", callback_data="plans")]
                ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(offer_text, reply_markup=reply_markup, parse_mode='Markdown')
            
        except ValueError:
            await query.edit_message_text("❌ Invalid package selection.")

async def confirm_purchase(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm and process purchase."""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    if data.startswith("confirm_"):
        try:
            target_subs = int(data.split("_")[1])
            cost = PRICING_PLANS.get(target_subs, 0)
            user_id = query.from_user.id
            
            # Process purchase
            conn = get_db_connection()
            c = conn.cursor()
            
            # Check balance again
            c.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
            result = c.fetchone()
            balance = result[0] if result else 0
            
            if balance >= cost:
                # Deduct balance and create campaign
                c.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (cost, user_id))
                
                # Create campaign record
                c.execute("""INSERT INTO campaigns (admin_id, target_subs, cost_stars, status) 
                          VALUES (?, ?, ?, 'active')""", (user_id, target_subs, cost))
                
                conn.commit()
                conn.close()
                
                success_text = f"🎉 **Campaign Started!** 🎉\n\n"
                success_text += f"**Package:** {target_subs:,} subscribers\n"
                success_text += f"**Cost:** {cost} ⭐\n"
                success_text += f"**Remaining Balance:** {balance - cost} ⭐\n\n"
                success_text += "Your promotion campaign has been scheduled!\n"
                success_text += "We'll use legitimate methods to grow your channel organically."
                
                keyboard = [
                    [InlineKeyboardButton("📊 View Campaigns", callback_data="my_campaigns")],
                    [InlineKeyboardButton("🔄 Start Another", callback_data="plans")],
                    [InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]
                ]
                
            else:
                conn.close()
                success_text = "❌ Transaction failed: Insufficient balance"
                keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="plans")]]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(success_text, reply_markup=reply_markup, parse_mode='Markdown')
            
        except Exception as e:
            logger.error(f"Purchase error: {e}")
            await query.edit_message_text("❌ Error processing purchase. Please try again.")

async def add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start channel addition process."""
    query = update.callback_query
    await query.answer()
    
    help_text = """
**Add Channel for Promotion**

To add your channel:

1. Make me an admin in your channel
   - Go to channel settings → Administrators
   - Add @YourBotUsername as admin
   - Grant necessary permissions

2. Send your channel username starting with @
   Example: @yourchannel

3. I'll verify and add it to your managed channels

Send your channel username now:
    """
    
    keyboard = [[InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(help_text.strip(), reply_markup=reply_markup, parse_mode='Markdown')
    context.user_data['awaiting_channel'] = True

async def handle_channel_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle channel username input."""
    if context.user_data.get('awaiting_channel'):
        channel_username = update.message.text.strip()
        user_id = update.effective_user.id
        
        if channel_username.startswith('@'):
            # Add channel to database
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("INSERT OR REPLACE INTO channels (channel_id, title, admin_id) VALUES (?, ?, ?)",
                     (channel_username, channel_username, user_id))
            conn.commit()
            conn.close()
            
            await update.message.reply_text(
                f"✅ Channel {channel_username} added successfully!\n\n"
                f"You can now create promotion campaigns for this channel.\n"
                f"Use /start to see your options.",
                parse_mode='Markdown'
            )
            context.user_data['awaiting_channel'] = False
        else:
            await update.message.reply_text(
                "❌ Please send a valid channel username starting with @\n"
                "Example: @yourchannel"
            )

async def show_my_campaigns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's active campaigns."""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        SELECT campaign_id, target_subs, cost_stars, status, progress, created_at 
        FROM campaigns WHERE admin_id = ? ORDER BY created_at DESC
    """, (user_id,))
    campaigns = c.fetchall()
    conn.close()
    
    if campaigns:
        campaigns_text = "📊 **Your Campaigns**\n\n"
        
        for camp in campaigns:
            campaign_id, target_subs, cost, status, progress, created = camp
            status_emoji = "🟢" if status == 'active' else "⚪" if status == 'completed' else "🔴"
            campaigns_text += f"{status_emoji} **{target_subs:,} subs** - {cost}⭐\n"
            campaigns_text += f"   Progress: {progress}% | Status: {status}\n"
            campaigns_text += f"   Created: {created[:10]}\n\n"
    else:
        campaigns_text = "📊 **Your Campaigns**\n\n"
        campaigns_text += "You don't have any active campaigns yet.\n\n"
        campaigns_text += "Start your first promotion campaign now!"
    
    keyboard = [
        [InlineKeyboardButton("🚀 Start New Campaign", callback_data="plans")],
        [InlineKeyboardButton("💰 Check Balance", callback_data="balance")],
        [InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(campaigns_text, reply_markup=reply_markup, parse_mode='Markdown')

async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help information."""
    query = update.callback_query
    await query.answer()
    
    help_text = """
🤖 **Help & Information**

**How It Works:**
1. Add your channel using the 'Add Channel' option
2. Check your star balance
3. Choose a promotion plan that fits your needs
4. Start your campaign and watch organic growth!

**Pricing:**
- 500 subscribers = 50 ⭐
- 1,000 subscribers = 100 ⭐  
- 3,000 subscribers = 150 ⭐
- 5,000 subscribers = 200 ⭐
- 10,000 subscribers = 350 ⭐ 🎉

**Legal Notice:**
This bot provides legitimate promotion services only. We do NOT:
• Artificially inflate member counts
• Violate Telegram's Terms of Service
• Use unauthorized automation

All growth is organic and compliant with platform rules.
    """
    
    keyboard = [
        [InlineKeyboardButton("📢 View Plans", callback_data="plans")],
        [InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(help_text.strip(), reply_markup=reply_markup, parse_mode='Markdown')

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Return to main menu."""
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("💰 Check Balance", callback_data="balance")],
        [InlineKeyboardButton("📢 Promotion Plans", callback_data="plans")],
        [InlineKeyboardButton("➕ Add Channel", callback_data="add_channel")],
        [InlineKeyboardButton("📊 My Campaigns", callback_data="my_campaigns")],
        [InlineKeyboardButton("ℹ️ Help", callback_data="help")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "🤖 **Main Menu**\n\nSelect an option below:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors in the telegram bot."""
    logger.error(f"Exception while handling an update: {context.error}")

# Main function
def main():
    """Start the bot."""
    # Initialize database
    init_db()
    
    # Start keep-alive thread
    keep_alive()
    logger.info("Keep-alive thread started")
    
    # Create Telegram application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(show_plans, pattern="^plans$"))
    application.add_handler(CallbackQueryHandler(check_balance, pattern="^balance$"))
    application.add_handler(CallbackQueryHandler(handle_purchase, pattern="^buy_"))
    application.add_handler(CallbackQueryHandler(confirm_purchase, pattern="^confirm_"))
    application.add_handler(CallbackQueryHandler(add_channel, pattern="^add_channel$"))
    application.add_handler(CallbackQueryHandler(show_my_campaigns, pattern="^my_campaigns$"))
    application.add_handler(CallbackQueryHandler(show_help, pattern="^help$"))
    application.add_handler(CallbackQueryHandler(main_menu, pattern="^main_menu$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_channel_input))
    application.add_error_handler(error_handler)
    
    # Start the Flask app for health checks (in a separate thread)
    def run_flask():
        logger.info(f"Starting Flask health check server on port {PORT}")
        app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)
    
    flask_thread = Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    logger.info(f"Flask health check server started on port {PORT}")
    logger.info(f"Running on Render: {RENDER}")
    
    # Start the Telegram bot
    logger.info("Telegram bot is starting...")
    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
