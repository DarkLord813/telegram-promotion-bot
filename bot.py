import os
import logging
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import asyncio

# Third-party imports
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes
)
from telegram.constants import ParseMode
import pytz

# Scheduler imports
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# ==================== CONFIGURATION ====================
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("No BOT_TOKEN found in environment variables")

ADMIN_IDS = [int(id.strip()) for id in os.getenv('ADMIN_IDS', '').split(',') if id.strip()]
WEBHOOK_URL = os.getenv('WEBHOOK_URL')
PORT = int(os.getenv('PORT', 8080))
RENDER_EXTERNAL_URL = os.getenv('RENDER_EXTERNAL_URL')

# If on Render and no webhook URL set, use Render's URL
if not WEBHOOK_URL and RENDER_EXTERNAL_URL:
    WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}/webhook"

TIMEZONE = pytz.timezone(os.getenv('TIMEZONE', 'UTC'))

# ==================== LOGGING SETUP ====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== SIMPLE FILE-BASED STORAGE ====================
DATA_DIR = "bot_data"
os.makedirs(DATA_DIR, exist_ok=True)

def save_json(filename, data):
    """Save data to JSON file"""
    with open(os.path.join(DATA_DIR, filename), 'w') as f:
        json.dump(data, f, default=str)

def load_json(filename, default=None):
    """Load data from JSON file"""
    try:
        with open(os.path.join(DATA_DIR, filename), 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return default if default is not None else {}

# Data storage
users = load_json('users.json', {})
channels = load_json('channels.json', [])
promotions = load_json('promotions.json', [])
schedules = load_json('schedules.json', [])

# Temporary storage for ongoing operations
temp_promotions = {}
temp_schedules = {}
waiting_for = {}

# ==================== BOT CLASS ====================
class PromotionBot:
    def __init__(self):
        self.application = None
        self.scheduler = AsyncIOScheduler(timezone=TIMEZONE)
        
    def setup_handlers(self):
        """Setup all bot handlers"""
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CallbackQueryHandler(self.handle_callback))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text))
        self.application.add_handler(MessageHandler(filters.PHOTO, self.handle_photo))
        
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        user = update.effective_user
        user_id = str(user.id)
        
        # Save user
        users[user_id] = {
            'id': user_id,
            'username': user.username,
            'first_name': user.first_name,
            'joined': str(datetime.now())
        }
        save_json('users.json', users)
        
        # Create inline keyboard based on user role
        keyboard = []
        
        if int(user_id) in ADMIN_IDS:
            keyboard = [
                [InlineKeyboardButton("📊 Statistics", callback_data="stats")],
                [InlineKeyboardButton("📢 Manage Channels", callback_data="manage_channels")],
                [InlineKeyboardButton("📝 Create Promotion", callback_data="create_promotion")],
                [InlineKeyboardButton("⏰ Schedules", callback_data="view_schedules")],
                [InlineKeyboardButton("📋 My Promotions", callback_data="list_promotions")]
            ]
        else:
            keyboard = [
                [InlineKeyboardButton("📢 Channels", callback_data="view_channels")]
            ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"👋 Welcome {user.first_name}!\n\nUse the buttons below.",
            reply_markup=reply_markup
        )
    
    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle all callback queries"""
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        data = query.data
        
        # Check admin permissions
        if data not in ["view_channels"] and user_id not in ADMIN_IDS:
            await query.edit_message_text("❌ You don't have permission.")
            return
        
        # Route handlers
        if data == "stats":
            await self.show_stats(query)
        elif data == "manage_channels":
            await self.manage_channels(query)
        elif data == "verify_channels":
            await self.verify_channels(query, context)
        elif data == "create_promotion":
            await self.create_promotion(query)
        elif data == "view_schedules":
            await self.view_schedules(query)
        elif data == "list_promotions":
            await self.list_promotions(query)
        elif data == "view_channels":
            await self.show_channels(query)
        elif data == "back_to_main":
            await self.back_to_main(query, user_id)
        elif data.startswith("promo_"):
            await self.handle_promo_action(query, context)
        elif data.startswith("schedule_"):
            await self.handle_schedule_action(query, context)
    
    async def show_stats(self, query):
        """Show statistics"""
        stats_text = (
            "📊 *Statistics*\n\n"
            f"👥 Users: {len(users)}\n"
            f"📢 Channels: {len(channels)}\n"
            f"📝 Promotions: {len(promotions)}\n"
            f"⏰ Schedules: {len(schedules)}"
        )
        
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="back_to_main")]]
        await query.edit_message_text(stats_text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    async def manage_channels(self, query):
        """Channel management menu"""
        keyboard = [
            [InlineKeyboardButton("✅ Verify Channel", callback_data="verify_channels")],
            [InlineKeyboardButton("📋 List Channels", callback_data="list_channels")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_to_main")]
        ]
        await query.edit_message_text("📢 *Channel Management*", parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    async def verify_channels(self, query, context):
        """Verify channel by forwarded message"""
        user_id = query.from_user.id
        waiting_for[user_id] = "forward_channel"
        
        keyboard = [[InlineKeyboardButton("🔙 Cancel", callback_data="manage_channels")]]
        await query.edit_message_text(
            "📤 Please forward a message from the channel where I'm an admin.\n\n"
            "I'll automatically verify and add it.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def create_promotion(self, query):
        """Start creating promotion"""
        user_id = query.from_user.id
        temp_promotions[user_id] = {'step': 'name', 'data': {}}
        
        keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="back_to_main")]]
        await query.edit_message_text(
            "📝 *Create Promotion*\n\nEnter a name:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        waiting_for[user_id] = "promotion_name"
    
    async def view_schedules(self, query):
        """View all schedules"""
        if not schedules:
            keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="back_to_main")]]
            await query.edit_message_text("No active schedules.", reply_markup=InlineKeyboardMarkup(keyboard))
            return
        
        text = "⏰ *Active Schedules*\n\n"
        keyboard = []
        
        for sched in schedules:
            if sched.get('active', True):
                promo = next((p for p in promotions if p['id'] == sched['promotion_id']), None)
                channel = next((c for c in channels if c['id'] == sched['channel_id']), None)
                
                if promo and channel:
                    text += f"• {promo['name']} → {channel['title']}\n"
                    text += f"  Type: {sched['type']}\n\n"
        
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_main")])
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    async def list_promotions(self, query):
        """List all promotions"""
        if not promotions:
            keyboard = [
                [InlineKeyboardButton("📝 Create", callback_data="create_promotion")],
                [InlineKeyboardButton("🔙 Back", callback_data="back_to_main")]
            ]
            await query.edit_message_text("No promotions yet.", reply_markup=InlineKeyboardMarkup(keyboard))
            return
        
        text = "📋 *Your Promotions*\n\n"
        keyboard = []
        
        for promo in promotions:
            text += f"• {promo['name']}\n"
            keyboard.append([
                InlineKeyboardButton(f"📤 Schedule '{promo['name']}'", callback_data=f"promo_schedule_{promo['id']}"),
                InlineKeyboardButton(f"👁 Preview", callback_data=f"promo_preview_{promo['id']}")
            ])
        
        keyboard.append([InlineKeyboardButton("➕ New", callback_data="create_promotion")])
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_main")])
        
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    async def show_channels(self, query):
        """Show channels to users"""
        if not channels:
            await query.edit_message_text("No channels available.")
            return
        
        text = "📢 *Our Channels*\n\n"
        keyboard = []
        
        for channel in channels:
            text += f"• {channel['title']}\n"
            if channel.get('username'):
                keyboard.append([
                    InlineKeyboardButton(f"Join {channel['title']}", url=f"https://t.me/{channel['username'].lstrip('@')}")
                ])
        
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_main")])
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    
    async def back_to_main(self, query, user_id):
        """Return to main menu"""
        # Clear temp data
        if user_id in waiting_for:
            del waiting_for[user_id]
        if user_id in temp_promotions:
            del temp_promotions[user_id]
        if user_id in temp_schedules:
            del temp_schedules[user_id]
        
        keyboard = []
        if user_id in ADMIN_IDS:
            keyboard = [
                [InlineKeyboardButton("📊 Statistics", callback_data="stats")],
                [InlineKeyboardButton("📢 Manage Channels", callback_data="manage_channels")],
                [InlineKeyboardButton("📝 Create Promotion", callback_data="create_promotion")],
                [InlineKeyboardButton("⏰ Schedules", callback_data="view_schedules")],
                [InlineKeyboardButton("📋 My Promotions", callback_data="list_promotions")]
            ]
        else:
            keyboard = [[InlineKeyboardButton("📢 Channels", callback_data="view_channels")]]
        
        await query.edit_message_text("Main Menu:", reply_markup=InlineKeyboardMarkup(keyboard))
    
    async def handle_promo_action(self, query, context):
        """Handle promotion actions"""
        data = query.data
        user_id = query.from_user.id
        
        if data.startswith("promo_schedule_"):
            promo_id = int(data.split("_")[2])
            temp_schedules[user_id] = {'promotion_id': promo_id, 'step': 'channel'}
            
            # Show channels for selection
            if not channels:
                await query.edit_message_text("No channels. Verify one first.", 
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Verify", callback_data="verify_channels")]]))
                return
            
            keyboard = []
            for channel in channels:
                keyboard.append([InlineKeyboardButton(channel['title'], callback_data=f"schedule_channel_{channel['id']}")])
            keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="list_promotions")])
            
            await query.edit_message_text("Select channel:", reply_markup=InlineKeyboardMarkup(keyboard))
        
        elif data.startswith("promo_preview_"):
            promo_id = int(data.split("_")[2])
            promo = next((p for p in promotions if p['id'] == promo_id), None)
            
            if promo:
                # Create keyboard
                keyboard = []
                if promo.get('buttons'):
                    for btn in promo['buttons']:
                        keyboard.append([InlineKeyboardButton(btn['text'], url=btn['url'])])
                
                reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
                
                if promo.get('image_id'):
                    await context.bot.send_photo(
                        chat_id=user_id,
                        photo=promo['image_id'],
                        caption=f"*{promo['name']}*\n\n{promo['text']}",
                        reply_markup=reply_markup,
                        parse_mode=ParseMode.MARKDOWN
                    )
                else:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=f"*{promo['name']}*\n\n{promo['text']}",
                        reply_markup=reply_markup,
                        parse_mode=ParseMode.MARKDOWN
                    )
                
                await query.edit_message_text("Preview sent!", 
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="list_promotions")]]))
    
    async def handle_schedule_action(self, query, context):
        """Handle schedule actions"""
        data = query.data
        user_id = query.from_user.id
        
        if data.startswith("schedule_channel_"):
            channel_id = int(data.split("_")[2])
            
            if user_id in temp_schedules:
                temp_schedules[user_id]['channel_id'] = channel_id
                temp_schedules[user_id]['step'] = 'type'
                
                keyboard = [
                    [InlineKeyboardButton("🕐 One Time", callback_data="schedule_type_once")],
                    [InlineKeyboardButton("📅 Daily", callback_data="schedule_type_daily")],
                    [InlineKeyboardButton("📆 Weekly", callback_data="schedule_type_weekly")],
                    [InlineKeyboardButton("🗓 Monthly", callback_data="schedule_type_monthly")]
                ]
                await query.edit_message_text("Select type:", reply_markup=InlineKeyboardMarkup(keyboard))
        
        elif data.startswith("schedule_type_"):
            sched_type = data.split("_")[2]
            
            if user_id in temp_schedules:
                temp_schedules[user_id]['type'] = sched_type
                temp_schedules[user_id]['step'] = 'datetime'
                
                await query.edit_message_text(
                    "Enter date/time (YYYY-MM-DD HH:MM in UTC):\nExample: 2024-12-31 15:30",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="list_promotions")]])
                )
                waiting_for[user_id] = "schedule_datetime"
        
        elif data.startswith("schedule_repeat_"):
            days = int(data.split("_")[2])
            
            if user_id in temp_schedules:
                temp_schedules[user_id]['repeat_days'] = days
                await self.save_schedule(user_id, query)
    
    async def save_schedule(self, user_id, query):
        """Save schedule"""
        temp = temp_schedules.get(user_id)
        if not temp:
            return
        
        # Create schedule
        schedule = {
            'id': len(schedules) + 1,
            'promotion_id': temp['promotion_id'],
            'channel_id': temp['channel_id'],
            'type': temp['type'],
            'time': str(temp['datetime']),
            'repeat_days': temp.get('repeat_days', 0),
            'active': True,
            'created': str(datetime.now())
        }
        
        schedules.append(schedule)
        save_json('schedules.json', schedules)
        
        # Add to scheduler
        await self.add_to_scheduler(schedule)
        
        # Cleanup
        del temp_schedules[user_id]
        if user_id in waiting_for:
            del waiting_for[user_id]
        
        await query.edit_message_text(
            "✅ Schedule created!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📋 View All", callback_data="view_schedules")]])
        )
    
    async def add_to_scheduler(self, schedule):
        """Add schedule to APScheduler"""
        dt = datetime.fromisoformat(schedule['time'])
        job_id = f"schedule_{schedule['id']}"
        
        if schedule['type'] == 'once':
            trigger = DateTrigger(run_date=dt)
        elif schedule['type'] == 'daily':
            trigger = IntervalTrigger(days=1, start_date=dt)
        elif schedule['type'] == 'weekly':
            trigger = IntervalTrigger(weeks=1, start_date=dt)
        elif schedule['type'] == 'monthly':
            trigger = IntervalTrigger(days=30, start_date=dt)
        else:
            return
        
        self.scheduler.add_job(
            self.send_scheduled_message,
            trigger=trigger,
            args=[schedule['id']],
            id=job_id
        )
    
    async def send_scheduled_message(self, schedule_id):
        """Send scheduled message"""
        schedule = next((s for s in schedules if s['id'] == schedule_id), None)
        if not schedule or not schedule['active']:
            return
        
        promo = next((p for p in promotions if p['id'] == schedule['promotion_id']), None)
        channel = next((c for c in channels if c['id'] == schedule['channel_id']), None)
        
        if not promo or not channel:
            return
        
        # Create keyboard
        keyboard = []
        if promo.get('buttons'):
            for btn in promo['buttons']:
                keyboard.append([InlineKeyboardButton(btn['text'], url=btn['url'])])
        
        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        
        try:
            if promo.get('image_id'):
                await self.application.bot.send_photo(
                    chat_id=channel['chat_id'],
                    photo=promo['image_id'],
                    caption=promo['text'],
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await self.application.bot.send_message(
                    chat_id=channel['chat_id'],
                    text=promo['text'],
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.MARKDOWN
                )
            
            # Handle repeat count
            if schedule['repeat_days'] > 0:
                # For simplicity, we'll just log that it was sent
                logger.info(f"Schedule {schedule_id} sent")
            
        except Exception as e:
            logger.error(f"Error sending scheduled message: {e}")
    
    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle text messages"""
        user_id = update.effective_user.id
        text = update.message.text
        
        # Save user
        users[str(user_id)] = {
            'id': str(user_id),
            'username': update.effective_user.username,
            'last_seen': str(datetime.now())
        }
        save_json('users.json', users)
        
        # Check if waiting for input
        if user_id in waiting_for:
            state = waiting_for[user_id]
            
            if state == "forward_channel":
                # Handle channel verification
                if update.message.forward_from_chat:
                    chat = update.message.forward_from_chat
                    
                    try:
                        bot_member = await context.bot.get_chat_member(chat.id, context.bot.id)
                        
                        if bot_member.status in ['administrator', 'creator']:
                            # Save channel
                            channel = {
                                'id': len(channels) + 1,
                                'chat_id': chat.id,
                                'title': chat.title,
                                'username': chat.username,
                                'type': chat.type,
                                'added': str(datetime.now())
                            }
                            channels.append(channel)
                            save_json('channels.json', channels)
                            
                            await update.message.reply_text(
                                f"✅ Channel '{chat.title}' added!",
                                reply_markup=InlineKeyboardMarkup([[
                                    InlineKeyboardButton("📢 Manage", callback_data="manage_channels"),
                                    InlineKeyboardButton("🏠 Main", callback_data="back_to_main")
                                ]])
                            )
                        else:
                            await update.message.reply_text("❌ I'm not an admin there.")
                    except Exception as e:
                        await update.message.reply_text(f"❌ Error: {str(e)}")
                else:
                    await update.message.reply_text("Please forward a message from the channel.")
                
                del waiting_for[user_id]
            
            elif state == "promotion_name":
                if user_id in temp_promotions:
                    temp_promotions[user_id]['data']['name'] = text
                    temp_promotions[user_id]['step'] = 'text'
                    waiting_for[user_id] = "promotion_text"
                    
                    await update.message.reply_text("✅ Name saved!\nNow send the text content:")
            
            elif state == "promotion_text":
                if user_id in temp_promotions:
                    temp_promotions[user_id]['data']['text'] = text
                    temp_promotions[user_id]['step'] = 'image'
                    
                    keyboard = [
                        [InlineKeyboardButton("✅ Skip Image", callback_data="promo_skip_image")],
                        [InlineKeyboardButton("❌ Cancel", callback_data="back_to_main")]
                    ]
                    
                    await update.message.reply_text(
                        "✅ Text saved!\nNow send an image (or skip):",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                    waiting_for[user_id] = "promotion_image"
            
            elif state == "promotion_buttons":
                if '|' in text:
                    btn_text, url = text.split('|', 1)
                    btn_text = btn_text.strip()
                    url = url.strip()
                    
                    if not url.startswith(('http://', 'https://')):
                        url = 'https://' + url
                    
                    if user_id in temp_promotions:
                        if 'buttons' not in temp_promotions[user_id]['data']:
                            temp_promotions[user_id]['data']['buttons'] = []
                        
                        temp_promotions[user_id]['data']['buttons'].append({'text': btn_text, 'url': url})
                        
                        keyboard = [
                            [InlineKeyboardButton("➕ Add Another", callback_data="promo_add_button")],
                            [InlineKeyboardButton("✅ Finish", callback_data="promo_finish_buttons")]
                        ]
                        
                        await update.message.reply_text(
                            f"✅ Button added! Total: {len(temp_promotions[user_id]['data']['buttons'])}",
                            reply_markup=InlineKeyboardMarkup(keyboard)
                        )
                else:
                    await update.message.reply_text("Use format: `Button Text | url`", parse_mode=ParseMode.MARKDOWN)
            
            elif state == "schedule_datetime":
                try:
                    dt = datetime.strptime(text, '%Y-%m-%d %H:%M')
                    dt = TIMEZONE.localize(dt)
                    
                    if user_id in temp_schedules:
                        temp_schedules[user_id]['datetime'] = dt
                        
                        keyboard = [
                            [InlineKeyboardButton("🔄 No Repeat", callback_data="schedule_repeat_0")],
                            [InlineKeyboardButton("📅 7 Days", callback_data="schedule_repeat_7")],
                            [InlineKeyboardButton("📅 30 Days", callback_data="schedule_repeat_30")]
                        ]
                        
                        await update.message.reply_text(
                            "Repeat for how many days?",
                            reply_markup=InlineKeyboardMarkup(keyboard)
                        )
                        del waiting_for[user_id]
                except ValueError:
                    await update.message.reply_text("Invalid format. Use YYYY-MM-DD HH:MM")
        
        else:
            await update.message.reply_text("Use the buttons to navigate.")
    
    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle photo messages"""
        user_id = update.effective_user.id
        
        if user_id in waiting_for and waiting_for[user_id] == "promotion_image":
            if user_id in temp_promotions:
                photo = update.message.photo[-1]
                temp_promotions[user_id]['data']['image_id'] = photo.file_id
                
                keyboard = [
                    [InlineKeyboardButton("➕ Add Button", callback_data="promo_add_button")],
                    [InlineKeyboardButton("✅ Skip Buttons", callback_data="promo_finish_buttons")]
                ]
                
                await update.message.reply_text(
                    "✅ Image saved!\nNow add buttons:",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                waiting_for[user_id] = "promotion_buttons"
    
    async def handle_promo_callback(self, query, context):
        """Handle promotion creation callbacks"""
        data = query.data
        user_id = query.from_user.id
        
        if data == "promo_skip_image":
            if user_id in temp_promotions:
                keyboard = [
                    [InlineKeyboardButton("➕ Add Button", callback_data="promo_add_button")],
                    [InlineKeyboardButton("✅ Finish", callback_data="promo_finish_buttons")]
                ]
                await query.edit_message_text("Image skipped.\nNow add buttons:", reply_markup=InlineKeyboardMarkup(keyboard))
                waiting_for[user_id] = "promotion_buttons"
        
        elif data == "promo_add_button":
            await query.edit_message_text(
                "Send button: `Button Text | url`",
                parse_mode=ParseMode.MARKDOWN
            )
        
        elif data == "promo_finish_buttons":
            if user_id in temp_promotions:
                promo_data = temp_promotions[user_id]['data']
                
                # Save promotion
                promo = {
                    'id': len(promotions) + 1,
                    'name': promo_data['name'],
                    'text': promo_data['text'],
                    'image_id': promo_data.get('image_id'),
                    'buttons': promo_data.get('buttons', []),
                    'created_by': user_id,
                    'created': str(datetime.now())
                }
                
                promotions.append(promo)
                save_json('promotions.json', promotions)
                
                # Cleanup
                del temp_promotions[user_id]
                if user_id in waiting_for:
                    del waiting_for[user_id]
                
                await query.edit_message_text(
                    f"✅ Promotion '{promo['name']}' created!",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("📤 Schedule It", callback_data=f"promo_schedule_{promo['id']}")],
                        [InlineKeyboardButton("📋 All Promotions", callback_data="list_promotions")]
                    ])
                )
    
    async def load_schedules(self):
        """Load schedules from storage"""
        for schedule in schedules:
            if schedule.get('active', True):
                await self.add_to_scheduler(schedule)
        logger.info(f"Loaded {len(schedules)} schedules")
    
    async def post_init(self, application):
        """Post initialization"""
        await self.load_schedules()
        self.scheduler.start()
        logger.info("Bot started!")
    
    def run(self):
        """Run the bot"""
        self.application = Application.builder() \
            .token(BOT_TOKEN) \
            .post_init(self.post_init) \
            .build()
        
        self.setup_handlers()
        
        # Add promo callback handler
        self.application.add_handler(CallbackQueryHandler(self.handle_promo_callback, pattern="^promo_"))
        
        if WEBHOOK_URL:
            # Webhook mode for Render
            logger.info(f"Starting webhook on port {PORT}")
            self.application.run_webhook(
                listen="0.0.0.0",
                port=PORT,
                url_path=BOT_TOKEN,
                webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}"
            )
        else:
            # Polling mode for development
            logger.info("Starting polling")
            self.application.run_polling()

# ==================== MAIN ====================
if __name__ == "__main__":
    bot = PromotionBot()
    bot.run()
