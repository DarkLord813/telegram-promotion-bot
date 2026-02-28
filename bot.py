import os
import logging
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import asyncio
from dataclasses import dataclass
from enum import Enum

# Third-party imports
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes
)
from telegram.constants import ParseMode
import pytz

# Database imports - SQLAlchemy 1.4.x compatible
from sqlalchemy import create_engine, Column, Integer, String, BigInteger, Boolean, DateTime, Text, JSON, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy.exc import SQLAlchemyError

# Scheduler imports
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# ==================== CONFIGURATION ====================
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("No BOT_TOKEN found in environment variables")

ADMIN_IDS = [int(id.strip()) for id in os.getenv('ADMIN_IDS', '').split(',') if id.strip()]
DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    # Default to SQLite for local development
    DATABASE_URL = 'sqlite:///bot_database.db'

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

# ==================== DATABASE MODELS ====================
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, unique=True, nullable=False)
    username = Column(String, nullable=True)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    joined_date = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)
    last_interaction = Column(DateTime, default=datetime.utcnow)

class Channel(Base):
    __tablename__ = 'channels'
    
    id = Column(Integer, primary_key=True)
    chat_id = Column(BigInteger, unique=True, nullable=False)
    chat_type = Column(String)  # 'channel', 'group', 'supergroup'
    title = Column(String)
    username = Column(String, nullable=True)
    invite_link = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    added_date = Column(DateTime, default=datetime.utcnow)
    last_verified = Column(DateTime, default=datetime.utcnow)
    
    # Relationship
    promotions = relationship("ScheduledPromotion", back_populates="channel")

class Promotion(Base):
    __tablename__ = 'promotions'
    
    id = Column(Integer, primary_key=True)
    name = Column(String)
    text = Column(Text)
    image_id = Column(String, nullable=True)
    buttons = Column(JSON, default=list)
    created_by = Column(BigInteger)
    created_date = Column(DateTime, default=datetime.utcnow)
    
    # Relationship
    schedules = relationship("ScheduledPromotion", back_populates="promotion")

class ScheduledPromotion(Base):
    __tablename__ = 'scheduled_promotions'
    
    id = Column(Integer, primary_key=True)
    promotion_id = Column(Integer, ForeignKey('promotions.id'))
    channel_id = Column(BigInteger, ForeignKey('channels.chat_id'))
    schedule_type = Column(String)  # 'once', 'daily', 'weekly', 'monthly'
    schedule_time = Column(DateTime)
    repeat_days = Column(Integer, default=0)  # 0 = infinite
    repeat_count = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    next_run = Column(DateTime)
    created_date = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    promotion = relationship("Promotion", back_populates="schedules")
    channel = relationship("Channel", back_populates="promotions")

# Create tables
Base.metadata.create_all(engine)

# ==================== DATA CLASSES ====================
class ScheduleType(Enum):
    ONCE = "once"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"

@dataclass
class TempPromotion:
    """Temporary promotion being created"""
    user_id: int
    name: str = ""
    text: str = ""
    image_id: str = None
    buttons: List = None
    
    def __post_init__(self):
        if self.buttons is None:
            self.buttons = []

@dataclass
class TempSchedule:
    """Temporary schedule being created"""
    user_id: int
    promotion_id: int = None
    channel_id: int = None
    schedule_type: str = None
    schedule_time: datetime = None
    repeat_days: int = 0

# ==================== BOT CLASS ====================
class PromotionBot:
    def __init__(self):
        self.application = None
        self.scheduler = AsyncIOScheduler(timezone=TIMEZONE)
        self.temp_promotions: Dict[int, TempPromotion] = {}
        self.temp_schedules: Dict[int, TempSchedule] = {}
        self.waiting_for = {}  # Track what input we're waiting for from users
        
    def setup_handlers(self):
        """Setup all bot handlers"""
        # Command handlers
        self.application.add_handler(CommandHandler("start", self.start_command))
        
        # Callback query handlers (for inline keyboards)
        self.application.add_handler(CallbackQueryHandler(self.handle_callback))
        
        # Message handlers
        self.application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND, self.handle_text
        ))
        self.application.add_handler(MessageHandler(
            filters.PHOTO, self.handle_photo
        ))
        
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        user = update.effective_user
        user_id = user.id
        
        # Save user to database
        self.save_user(user)
        
        # Create inline keyboard based on user role
        keyboard = []
        
        if user_id in ADMIN_IDS:
            keyboard = [
                [InlineKeyboardButton("📊 Statistics", callback_data="stats")],
                [InlineKeyboardButton("📢 Manage Channels", callback_data="manage_channels")],
                [InlineKeyboardButton("📝 Create Promotion", callback_data="create_promotion")],
                [InlineKeyboardButton("⏰ Schedule Messages", callback_data="view_schedules")],
                [InlineKeyboardButton("📋 My Promotions", callback_data="list_promotions")]
            ]
        else:
            keyboard = [
                [InlineKeyboardButton("ℹ️ About", callback_data="about")],
                [InlineKeyboardButton("📢 Channels", callback_data="view_channels")]
            ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        welcome_text = (
            f"👋 Welcome {user.first_name}!\n\n"
            f"I'm a Promotion Bot that helps manage and schedule promotional messages.\n"
            f"Use the buttons below to navigate."
        )
        
        await update.message.reply_text(welcome_text, reply_markup=reply_markup)
    
    def save_user(self, user):
        """Save or update user in database"""
        session = SessionLocal()
        try:
            db_user = session.query(User).filter_by(user_id=user.id).first()
            
            if not db_user:
                db_user = User(
                    user_id=user.id,
                    username=user.username,
                    first_name=user.first_name,
                    last_name=user.last_name
                )
                session.add(db_user)
            else:
                db_user.username = user.username
                db_user.first_name = user.first_name
                db_user.last_name = user.last_name
                db_user.last_interaction = datetime.utcnow()
            
            session.commit()
        except SQLAlchemyError as e:
            logger.error(f"Database error: {e}")
            session.rollback()
        finally:
            session.close()
    
    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle all callback queries from inline keyboards"""
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        data = query.data
        
        # Check admin permissions for admin functions
        if data not in ["about", "view_channels"] and user_id not in ADMIN_IDS:
            await query.edit_message_text("❌ You don't have permission to do that.")
            return
        
        # Route to appropriate handler
        handlers = {
            "stats": self.show_stats,
            "manage_channels": self.manage_channels,
            "verify_channels": self.verify_channels,
            "create_promotion": self.create_promotion,
            "view_schedules": self.view_schedules,
            "list_promotions": self.list_promotions,
            "about": self.show_about,
            "view_channels": self.show_channels,
            "back_to_main": self.back_to_main
        }
        
        # Check if it's a dynamic callback
        if data in handlers:
            await handlers[data](query, context)
        elif data.startswith("channel_"):
            await self.handle_channel_action(query, context)
        elif data.startswith("promo_"):
            await self.handle_promo_action(query, context)
        elif data.startswith("schedule_"):
            await self.handle_schedule_action(query, context)
    
    async def show_stats(self, query, context):
        """Show bot statistics"""
        session = SessionLocal()
        try:
            total_users = session.query(User).count()
            active_users = session.query(User).filter_by(is_active=True).count()
            total_channels = session.query(Channel).count()
            active_channels = session.query(Channel).filter_by(is_active=True).count()
            total_promotions = session.query(Promotion).count()
            total_schedules = session.query(ScheduledPromotion).filter_by(is_active=True).count()
            
            stats_text = (
                "📊 *Bot Statistics*\n\n"
                f"👥 *Users:*\n"
                f"  Total: {total_users}\n"
                f"  Active: {active_users}\n\n"
                f"📢 *Channels:*\n"
                f"  Total: {total_channels}\n"
                f"  Active: {active_channels}\n\n"
                f"📝 *Content:*\n"
                f"  Promotions: {total_promotions}\n"
                f"  Active Schedules: {total_schedules}"
            )
            
            keyboard = [
                [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_main")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                stats_text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=reply_markup
            )
        finally:
            session.close()
    
    async def manage_channels(self, query, context):
        """Show channel management menu"""
        keyboard = [
            [InlineKeyboardButton("✅ Verify Channels", callback_data="verify_channels")],
            [InlineKeyboardButton("📋 List Channels", callback_data="list_channels")],
            [InlineKeyboardButton("🔄 Refresh Channel List", callback_data="verify_channels")],
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "📢 *Channel Management*\n\n"
            "Select an option:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )
    
    async def verify_channels(self, query, context):
        """Verify and add channels where bot is admin"""
        user_id = query.from_user.id
        bot = context.bot
        
        await query.edit_message_text(
            "🔄 Scanning for channels and groups where I'm an admin...\n"
            "This may take a moment."
        )
        
        try:
            # Get bot's chat list (this requires the bot to be able to fetch chats)
            # Alternative: We'll try to get updates from channels the bot is in
            channels_added = 0
            
            # Method 1: Try to get channels from chat member updates
            # This requires the bot to have received updates from channels
            
            # Method 2: Ask user to forward a message from the channel
            keyboard = [
                [InlineKeyboardButton("📤 Forward a message from channel", callback_data="forward_channel")],
                [InlineKeyboardButton("🔙 Back", callback_data="manage_channels")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                "To verify channels:\n\n"
                "1️⃣ Add me as an admin to your channel/group\n"
                "2️⃣ Forward any message from that channel/group to me\n"
                "3️⃣ I'll automatically detect and add it\n\n"
                "Or click the button below to forward:",
                reply_markup=reply_markup
            )
            
            # Store that we're waiting for a forwarded message
            self.waiting_for[user_id] = "forward_channel"
            
        except Exception as e:
            logger.error(f"Error verifying channels: {e}")
            await query.edit_message_text(
                f"❌ Error verifying channels: {str(e)}",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Back", callback_data="manage_channels")
                ]])
            )
    
    async def create_promotion(self, query, context):
        """Start creating a new promotion"""
        user_id = query.from_user.id
        
        # Create temporary promotion object
        self.temp_promotions[user_id] = TempPromotion(user_id=user_id)
        
        keyboard = [
            [InlineKeyboardButton("❌ Cancel", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "📝 *Create New Promotion*\n\n"
            "Please enter a name for this promotion:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )
        
        # Set state
        self.waiting_for[user_id] = "promotion_name"
    
    async def view_schedules(self, query, context):
        """View all scheduled promotions"""
        session = SessionLocal()
        try:
            schedules = session.query(ScheduledPromotion).filter_by(is_active=True).all()
            
            if not schedules:
                keyboard = [
                    [InlineKeyboardButton("📝 Create Promotion First", callback_data="create_promotion")],
                    [InlineKeyboardButton("🔙 Back", callback_data="back_to_main")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(
                    "No active schedules found.\n"
                    "Create a promotion first to schedule it.",
                    reply_markup=reply_markup
                )
                return
            
            text = "⏰ *Active Schedules*\n\n"
            keyboard = []
            
            for schedule in schedules:
                promotion = schedule.promotion
                channel = schedule.channel
                
                text += f"• *{promotion.name}* → {channel.title}\n"
                text += f"  Type: {schedule.schedule_type}\n"
                text += f"  Next: {schedule.next_run.strftime('%Y-%m-%d %H:%M') if schedule.next_run else 'Not set'}\n\n"
                
                keyboard.append([
                    InlineKeyboardButton(
                        f"Edit {promotion.name}",
                        callback_data=f"schedule_edit_{schedule.id}"
                    )
                ])
            
            keyboard.append([InlineKeyboardButton("➕ New Schedule", callback_data="create_promotion")])
            keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_main")])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=reply_markup
            )
        finally:
            session.close()
    
    async def list_promotions(self, query, context):
        """List all promotions"""
        session = SessionLocal()
        try:
            promotions = session.query(Promotion).all()
            
            if not promotions:
                keyboard = [
                    [InlineKeyboardButton("📝 Create Promotion", callback_data="create_promotion")],
                    [InlineKeyboardButton("🔙 Back", callback_data="back_to_main")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(
                    "No promotions found.\nCreate your first promotion!",
                    reply_markup=reply_markup
                )
                return
            
            text = "📋 *Your Promotions*\n\n"
            keyboard = []
            
            for promo in promotions:
                text += f"• *{promo.name}* (ID: {promo.id})\n"
                text += f"  Created: {promo.created_date.strftime('%Y-%m-%d')}\n\n"
                
                keyboard.append([
                    InlineKeyboardButton(
                        f"📤 Schedule '{promo.name}'",
                        callback_data=f"promo_schedule_{promo.id}"
                    ),
                    InlineKeyboardButton(
                        f"👁 Preview",
                        callback_data=f"promo_preview_{promo.id}"
                    )
                ])
            
            keyboard.append([InlineKeyboardButton("➕ New Promotion", callback_data="create_promotion")])
            keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_main")])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=reply_markup
            )
        finally:
            session.close()
    
    async def show_about(self, query, context):
        """Show about information"""
        keyboard = [
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "ℹ️ *About This Bot*\n\n"
            "This is a promotion bot that helps you:\n"
            "• Create promotional messages\n"
            "• Add inline buttons with links\n"
            "• Schedule messages to channels\n"
            "• Repeat messages for multiple days\n"
            "• Manage multiple channels\n\n"
            "Use the menu buttons to navigate.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )
    
    async def show_channels(self, query, context):
        """Show available channels to users"""
        session = SessionLocal()
        try:
            channels = session.query(Channel).filter_by(is_active=True).all()
            
            if not channels:
                await query.edit_message_text(
                    "No channels available at the moment.",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔙 Back", callback_data="back_to_main")
                    ]])
                )
                return
            
            text = "📢 *Our Channels*\n\n"
            keyboard = []
            
            for channel in channels:
                text += f"• {channel.title}\n"
                if channel.username:
                    keyboard.append([
                        InlineKeyboardButton(
                            f"Join {channel.title}",
                            url=f"https://t.me/{channel.username.lstrip('@')}"
                        )
                    ])
                elif channel.invite_link:
                    keyboard.append([
                        InlineKeyboardButton(
                            f"Join {channel.title}",
                            url=channel.invite_link
                        )
                    ])
            
            keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_main")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(text, reply_markup=reply_markup)
        finally:
            session.close()
    
    async def back_to_main(self, query, context):
        """Return to main menu"""
        user_id = query.from_user.id
        
        # Clear any pending states
        if user_id in self.waiting_for:
            del self.waiting_for[user_id]
        if user_id in self.temp_promotions:
            del self.temp_promotions[user_id]
        if user_id in self.temp_schedules:
            del self.temp_schedules[user_id]
        
        # Show main menu
        keyboard = []
        
        if user_id in ADMIN_IDS:
            keyboard = [
                [InlineKeyboardButton("📊 Statistics", callback_data="stats")],
                [InlineKeyboardButton("📢 Manage Channels", callback_data="manage_channels")],
                [InlineKeyboardButton("📝 Create Promotion", callback_data="create_promotion")],
                [InlineKeyboardButton("⏰ Schedule Messages", callback_data="view_schedules")],
                [InlineKeyboardButton("📋 My Promotions", callback_data="list_promotions")]
            ]
        else:
            keyboard = [
                [InlineKeyboardButton("ℹ️ About", callback_data="about")],
                [InlineKeyboardButton("📢 Channels", callback_data="view_channels")]
            ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"👋 Welcome back!\n\nMain Menu:",
            reply_markup=reply_markup
        )
    
    async def handle_channel_action(self, query, context):
        """Handle channel-related callbacks"""
        data = query.data
        user_id = query.from_user.id
        
        if data == "list_channels":
            session = SessionLocal()
            try:
                channels = session.query(Channel).all()
                
                if not channels:
                    text = "No channels added yet."
                else:
                    text = "📋 *Added Channels*\n\n"
                    for ch in channels:
                        status = "✅ Active" if ch.is_active else "❌ Inactive"
                        text += f"• {ch.title} ({ch.chat_type})\n"
                        text += f"  ID: `{ch.chat_id}`\n"
                        text += f"  Status: {status}\n\n"
                
                keyboard = [
                    [InlineKeyboardButton("🔄 Verify New", callback_data="verify_channels")],
                    [InlineKeyboardButton("🔙 Back", callback_data="manage_channels")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(
                    text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=reply_markup
                )
            finally:
                session.close()
    
    async def handle_promo_action(self, query, context):
        """Handle promotion-related callbacks"""
        data = query.data
        user_id = query.from_user.id
        
        if data.startswith("promo_schedule_"):
            promo_id = int(data.split("_")[2])
            
            # Store promotion ID for scheduling
            self.temp_schedules[user_id] = TempSchedule(user_id=user_id, promotion_id=promo_id)
            
            # Ask for channel selection
            session = SessionLocal()
            try:
                channels = session.query(Channel).filter_by(is_active=True).all()
                
                if not channels:
                    await query.edit_message_text(
                        "No channels available. Please verify channels first.",
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("✅ Verify Channels", callback_data="verify_channels")
                        ]])
                    )
                    return
                
                text = "Select a channel to schedule this promotion:"
                keyboard = []
                
                for channel in channels:
                    keyboard.append([
                        InlineKeyboardButton(
                            channel.title,
                            callback_data=f"schedule_channel_{channel.chat_id}"
                        )
                    ])
                
                keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="list_promotions")])
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(text, reply_markup=reply_markup)
            finally:
                session.close()
        
        elif data.startswith("promo_preview_"):
            promo_id = int(data.split("_")[2])
            
            session = SessionLocal()
            try:
                promotion = session.query(Promotion).get(promo_id)
                
                if not promotion:
                    await query.edit_message_text("Promotion not found.")
                    return
                
                # Create keyboard from saved buttons
                keyboard = []
                if promotion.buttons:
                    for btn in promotion.buttons:
                        keyboard.append([InlineKeyboardButton(btn['text'], url=btn['url'])])
                
                reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
                
                # Send preview
                if promotion.image_id:
                    await context.bot.send_photo(
                        chat_id=user_id,
                        photo=promotion.image_id,
                        caption=f"*Preview: {promotion.name}*\n\n{promotion.text}",
                        reply_markup=reply_markup,
                        parse_mode=ParseMode.MARKDOWN
                    )
                else:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=f"*Preview: {promotion.name}*\n\n{promotion.text}",
                        reply_markup=reply_markup,
                        parse_mode=ParseMode.MARKDOWN
                    )
                
                await query.edit_message_text(
                    "Preview sent!",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔙 Back to Promotions", callback_data="list_promotions")
                    ]])
                )
            finally:
                session.close()
    
    async def handle_schedule_action(self, query, context):
        """Handle schedule-related callbacks"""
        data = query.data
        user_id = query.from_user.id
        
        if data.startswith("schedule_channel_"):
            channel_id = int(data.split("_")[2])
            
            if user_id in self.temp_schedules:
                self.temp_schedules[user_id].channel_id = channel_id
                
                # Ask for schedule type
                keyboard = [
                    [InlineKeyboardButton("🕐 One Time", callback_data="schedule_type_once")],
                    [InlineKeyboardButton("📅 Daily", callback_data="schedule_type_daily")],
                    [InlineKeyboardButton("📆 Weekly", callback_data="schedule_type_weekly")],
                    [InlineKeyboardButton("🗓 Monthly", callback_data="schedule_type_monthly")],
                    [InlineKeyboardButton("🔙 Back", callback_data="list_promotions")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(
                    "Select schedule type:",
                    reply_markup=reply_markup
                )
        
        elif data.startswith("schedule_type_"):
            schedule_type = data.split("_")[2]
            
            if user_id in self.temp_schedules:
                self.temp_schedules[user_id].schedule_type = schedule_type
                
                # Ask for date/time
                await query.edit_message_text(
                    "Please enter the date and time for the first message (YYYY-MM-DD HH:MM format in UTC):\n"
                    "Example: 2024-12-31 15:30",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔙 Back", callback_data="list_promotions")
                    ]])
                )
                
                self.waiting_for[user_id] = "schedule_datetime"
        
        elif data.startswith("schedule_repeat_"):
            # Handle repeat days selection
            parts = data.split("_")
            if len(parts) == 3:
                days = int(parts[2])
                
                if user_id in self.temp_schedules:
                    self.temp_schedules[user_id].repeat_days = days
                    
                    # Save the schedule
                    await self.save_schedule(user_id, query, context)
    
    async def save_schedule(self, user_id, query, context):
        """Save the schedule to database"""
        temp_schedule = self.temp_schedules.get(user_id)
        
        if not temp_schedule:
            await query.edit_message_text("Error: Schedule data not found.")
            return
        
        session = SessionLocal()
        try:
            # Get promotion and channel
            promotion = session.query(Promotion).get(temp_schedule.promotion_id)
            channel = session.query(Channel).filter_by(chat_id=temp_schedule.channel_id).first()
            
            if not promotion or not channel:
                await query.edit_message_text("Error: Promotion or Channel not found.")
                return
            
            # Create scheduled promotion
            scheduled = ScheduledPromotion(
                promotion_id=promotion.id,
                channel_id=channel.chat_id,
                schedule_type=temp_schedule.schedule_type,
                schedule_time=temp_schedule.schedule_time,
                repeat_days=temp_schedule.repeat_days,
                next_run=temp_schedule.schedule_time,
                is_active=True
            )
            
            session.add(scheduled)
            session.commit()
            
            # Schedule in APScheduler
            await self.schedule_message(scheduled)
            
            # Clear temporary data
            del self.temp_schedules[user_id]
            if user_id in self.waiting_for:
                del self.waiting_for[user_id]
            
            await query.edit_message_text(
                f"✅ Message scheduled successfully!\n\n"
                f"Promotion: {promotion.name}\n"
                f"Channel: {channel.title}\n"
                f"Type: {temp_schedule.schedule_type}\n"
                f"First run: {temp_schedule.schedule_time.strftime('%Y-%m-%d %H:%M UTC')}\n"
                f"Repeat days: {temp_schedule.repeat_days if temp_schedule.repeat_days > 0 else 'Infinite'}",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("📋 View All Schedules", callback_data="view_schedules"),
                    InlineKeyboardButton("🔙 Main Menu", callback_data="back_to_main")
                ]])
            )
            
        except Exception as e:
            logger.error(f"Error saving schedule: {e}")
            session.rollback()
            await query.edit_message_text(f"❌ Error saving schedule: {str(e)}")
        finally:
            session.close()
    
    async def schedule_message(self, scheduled: ScheduledPromotion):
        """Add message to scheduler"""
        job_id = f"promo_{scheduled.id}"
        
        if scheduled.schedule_type == "once":
            trigger = DateTrigger(run_date=scheduled.schedule_time)
        elif scheduled.schedule_type == "daily":
            trigger = IntervalTrigger(
                days=1,
                start_date=scheduled.schedule_time,
                timezone=TIMEZONE
            )
        elif scheduled.schedule_type == "weekly":
            trigger = IntervalTrigger(
                weeks=1,
                start_date=scheduled.schedule_time,
                timezone=TIMEZONE
            )
        elif scheduled.schedule_type == "monthly":
            trigger = IntervalTrigger(
                days=30,
                start_date=scheduled.schedule_time,
                timezone=TIMEZONE
            )
        else:
            return
        
        # Add job to scheduler
        self.scheduler.add_job(
            self.send_scheduled_message,
            trigger=trigger,
            args=[scheduled.id],
            id=job_id,
            replace_existing=True
        )
    
    async def send_scheduled_message(self, scheduled_id: int):
        """Send a scheduled message"""
        session = SessionLocal()
        try:
            scheduled = session.query(ScheduledPromotion).get(scheduled_id)
            
            if not scheduled or not scheduled.is_active:
                return
            
            promotion = scheduled.promotion
            channel = scheduled.channel
            
            # Create keyboard
            keyboard = []
            if promotion.buttons:
                for btn in promotion.buttons:
                    keyboard.append([InlineKeyboardButton(btn['text'], url=btn['url'])])
            
            reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
            
            # Send message
            bot = self.application.bot
            try:
                if promotion.image_id:
                    await bot.send_photo(
                        chat_id=channel.chat_id,
                        photo=promotion.image_id,
                        caption=promotion.text,
                        reply_markup=reply_markup,
                        parse_mode=ParseMode.MARKDOWN
                    )
                else:
                    await bot.send_message(
                        chat_id=channel.chat_id,
                        text=promotion.text,
                        reply_markup=reply_markup,
                        parse_mode=ParseMode.MARKDOWN
                    )
                
                # Update repeat count
                scheduled.repeat_count += 1
                
                # Check if we've reached repeat limit
                if scheduled.repeat_days > 0 and scheduled.repeat_count >= scheduled.repeat_days:
                    scheduled.is_active = False
                    # Remove from scheduler
                    try:
                        self.scheduler.remove_job(f"promo_{scheduled.id}")
                    except:
                        pass
                else:
                    # Update next run time
                    # This will be handled by APScheduler automatically
                    pass
                
                session.commit()
                
            except Exception as e:
                logger.error(f"Error sending scheduled message {scheduled_id}: {e}")
                
        except Exception as e:
            logger.error(f"Error in send_scheduled_message: {e}")
        finally:
            session.close()
    
    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle text messages"""
        user_id = update.effective_user.id
        text = update.message.text
        
        # Save/update user
        self.save_user(update.effective_user)
        
        # Check if we're waiting for input from this user
        if user_id in self.waiting_for:
            waiting_for = self.waiting_for[user_id]
            
            if waiting_for == "forward_channel":
                # User forwarded a message, try to extract channel info
                if update.message.forward_from_chat:
                    chat = update.message.forward_from_chat
                    
                    # Check if bot is admin in this chat
                    try:
                        bot_member = await context.bot.get_chat_member(chat.id, context.bot.id)
                        
                        if bot_member.status in ['administrator', 'creator']:
                            # Save channel to database
                            session = SessionLocal()
                            try:
                                channel = session.query(Channel).filter_by(chat_id=chat.id).first()
                                
                                if not channel:
                                    channel = Channel(
                                        chat_id=chat.id,
                                        chat_type=chat.type,
                                        title=chat.title,
                                        username=chat.username,
                                        invite_link=chat.invite_link
                                    )
                                    session.add(channel)
                                else:
                                    channel.title = chat.title
                                    channel.username = chat.username
                                    channel.last_verified = datetime.utcnow()
                                
                                session.commit()
                                
                                await update.message.reply_text(
                                    f"✅ Channel '{chat.title}' has been verified and added!",
                                    reply_markup=InlineKeyboardMarkup([[
                                        InlineKeyboardButton("📢 Manage Channels", callback_data="manage_channels"),
                                        InlineKeyboardButton("🔙 Main Menu", callback_data="back_to_main")
                                    ]])
                                )
                                
                            finally:
                                session.close()
                        else:
                            await update.message.reply_text(
                                "❌ I'm not an admin in that channel/group. Please make me an admin first.",
                                reply_markup=InlineKeyboardMarkup([[
                                    InlineKeyboardButton("🔄 Try Again", callback_data="verify_channels")
                                ]])
                            )
                    except Exception as e:
                        await update.message.reply_text(
                            f"❌ Error verifying channel: {str(e)}",
                            reply_markup=InlineKeyboardMarkup([[
                                InlineKeyboardButton("🔄 Try Again", callback_data="verify_channels")
                            ]])
                        )
                else:
                    await update.message.reply_text(
                        "Please forward a message from the channel/group you want to verify.",
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("❌ Cancel", callback_data="manage_channels")
                        ]])
                    )
                
                # Clear waiting state
                del self.waiting_for[user_id]
            
            elif waiting_for == "promotion_name":
                # Save promotion name
                if user_id in self.temp_promotions:
                    self.temp_promotions[user_id].name = text
                    
                    await update.message.reply_text(
                        "✅ Name saved!\n\nNow send the text content for your promotion:",
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("❌ Cancel", callback_data="back_to_main")
                        ]])
                    )
                    
                    self.waiting_for[user_id] = "promotion_text"
            
            elif waiting_for == "promotion_text":
                # Save promotion text
                if user_id in self.temp_promotions:
                    self.temp_promotions[user_id].text = text
                    
                    keyboard = [
                        [InlineKeyboardButton("✅ No Image (Skip)", callback_data="promo_skip_image")],
                        [InlineKeyboardButton("❌ Cancel", callback_data="back_to_main")]
                    ]
                    
                    await update.message.reply_text(
                        "✅ Text saved!\n\nNow send an image for your promotion (or skip):",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                    
                    self.waiting_for[user_id] = "promotion_image"
            
            elif waiting_for == "promotion_buttons":
                # Add button
                if '|' in text:
                    try:
                        btn_text, url = text.split('|', 1)
                        btn_text = btn_text.strip()
                        url = url.strip()
                        
                        if not url.startswith(('http://', 'https://')):
                            url = 'https://' + url
                        
                        if user_id in self.temp_promotions:
                            self.temp_promotions[user_id].buttons.append({
                                'text': btn_text,
                                'url': url
                            })
                            
                            keyboard = [
                                [InlineKeyboardButton("➕ Add Another Button", callback_data="promo_add_button")],
                                [InlineKeyboardButton("✅ Finish Buttons", callback_data="promo_finish_buttons")],
                                [InlineKeyboardButton("❌ Cancel", callback_data="back_to_main")]
                            ]
                            
                            await update.message.reply_text(
                                f"✅ Button '{btn_text}' added!\n\n"
                                f"Total buttons: {len(self.temp_promotions[user_id].buttons)}",
                                reply_markup=InlineKeyboardMarkup(keyboard)
                            )
                    except Exception as e:
                        await update.message.reply_text(
                            "❌ Invalid format. Use: `Button Text | url`",
                            parse_mode=ParseMode.MARKDOWN
                        )
                else:
                    await update.message.reply_text(
                        "❌ Please use the format: `Button Text | url`",
                        parse_mode=ParseMode.MARKDOWN
                    )
            
            elif waiting_for == "schedule_datetime":
                try:
                    # Parse datetime
                    dt = datetime.strptime(text, '%Y-%m-%d %H:%M')
                    dt = TIMEZONE.localize(dt)
                    
                    if user_id in self.temp_schedules:
                        self.temp_schedules[user_id].schedule_time = dt
                        
                        # Ask for repeat days
                        keyboard = [
                            [InlineKeyboardButton("🔄 No Repeat (Once)", callback_data="schedule_repeat_0")],
                            [InlineKeyboardButton("📅 7 Days", callback_data="schedule_repeat_7")],
                            [InlineKeyboardButton("📅 30 Days", callback_data="schedule_repeat_30")],
                            [InlineKeyboardButton("♾️ Infinite", callback_data="schedule_repeat_0")],
                            [InlineKeyboardButton("🔙 Back", callback_data="list_promotions")]
                        ]
                        
                        await update.message.reply_text(
                            "How many days should this repeat?",
                            reply_markup=InlineKeyboardMarkup(keyboard)
                        )
                        
                        del self.waiting_for[user_id]
                        
                except ValueError:
                    await update.message.reply_text(
                        "❌ Invalid format. Please use YYYY-MM-DD HH:MM (e.g., 2024-12-31 15:30)"
                    )
        
        else:
            # Regular message from user
            await update.message.reply_text(
                "Please use the buttons to navigate.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Main Menu", callback_data="back_to_main")
                ]])
            )
    
    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle photo messages"""
        user_id = update.effective_user.id
        
        # Save user
        self.save_user(update.effective_user)
        
        if user_id in self.waiting_for and self.waiting_for[user_id] == "promotion_image":
            if user_id in self.temp_promotions:
                # Get the largest photo
                photo = update.message.photo[-1]
                self.temp_promotions[user_id].image_id = photo.file_id
                
                # Ask for buttons
                keyboard = [
                    [InlineKeyboardButton("➕ Add Button", callback_data="promo_add_button")],
                    [InlineKeyboardButton("✅ Skip Buttons", callback_data="promo_finish_buttons")],
                    [InlineKeyboardButton("❌ Cancel", callback_data="back_to_main")]
                ]
                
                await update.message.reply_text(
                    "✅ Image saved!\n\nNow add buttons to your promotion:",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                
                self.waiting_for[user_id] = "promotion_buttons"
    
    async def handle_promo_callback(self, query, context):
        """Handle promotion creation callbacks"""
        data = query.data
        user_id = query.from_user.id
        
        if data == "promo_skip_image":
            if user_id in self.temp_promotions:
                keyboard = [
                    [InlineKeyboardButton("➕ Add Button", callback_data="promo_add_button")],
                    [InlineKeyboardButton("✅ Skip Buttons", callback_data="promo_finish_buttons")],
                    [InlineKeyboardButton("❌ Cancel", callback_data="back_to_main")]
                ]
                
                await query.edit_message_text(
                    "Image skipped.\n\nNow add buttons to your promotion:",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                
                self.waiting_for[user_id] = "promotion_buttons"
        
        elif data == "promo_add_button":
            await query.edit_message_text(
                "Send button details in format:\n`Button Text | url`\n\n"
                "Example: `Visit Website | https://example.com`",
                parse_mode=ParseMode.MARKDOWN
            )
        
        elif data == "promo_finish_buttons":
            if user_id in self.temp_promotions:
                # Save promotion to database
                temp = self.temp_promotions[user_id]
                
                session = SessionLocal()
                try:
                    promotion = Promotion(
                        name=temp.name,
                        text=temp.text,
                        image_id=temp.image_id,
                        buttons=temp.buttons,
                        created_by=user_id
                    )
                    
                    session.add(promotion)
                    session.commit()
                    
                    # Create preview
                    keyboard = []
                    if temp.buttons:
                        for btn in temp.buttons:
                            keyboard.append([InlineKeyboardButton(btn['text'], url=btn['url'])])
                    
                    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
                    
                    # Send preview
                    if temp.image_id:
                        await context.bot.send_photo(
                            chat_id=user_id,
                            photo=temp.image_id,
                            caption=f"*Preview: {temp.name}*\n\n{temp.text}",
                            reply_markup=reply_markup,
                            parse_mode=ParseMode.MARKDOWN
                        )
                    else:
                        await context.bot.send_message(
                            chat_id=user_id,
                            text=f"*Preview: {temp.name}*\n\n{temp.text}",
                            reply_markup=reply_markup,
                            parse_mode=ParseMode.MARKDOWN
                        )
                    
                    await query.edit_message_text(
                        f"✅ Promotion '{temp.name}' created successfully!\n\n"
                        f"What would you like to do next?",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("⏰ Schedule This Promotion", callback_data=f"promo_schedule_{promotion.id}")],
                            [InlineKeyboardButton("📋 View All Promotions", callback_data="list_promotions")],
                            [InlineKeyboardButton("🔙 Main Menu", callback_data="back_to_main")]
                        ])
                    )
                    
                    # Clear temporary data
                    del self.temp_promotions[user_id]
                    if user_id in self.waiting_for:
                        del self.waiting_for[user_id]
                    
                except Exception as e:
                    logger.error(f"Error saving promotion: {e}")
                    session.rollback()
                    await query.edit_message_text(f"❌ Error saving promotion: {str(e)}")
                finally:
                    session.close()
    
    async def load_schedules_from_db(self):
        """Load all active schedules from database"""
        session = SessionLocal()
        try:
            schedules = session.query(ScheduledPromotion).filter_by(is_active=True).all()
            
            for schedule in schedules:
                await self.schedule_message(schedule)
                
            logger.info(f"Loaded {len(schedules)} schedules from database")
        except Exception as e:
            logger.error(f"Error loading schedules: {e}")
        finally:
            session.close()
    
    async def post_init(self, application):
        """Initialize after bot starts"""
        # Load schedules from database
        await self.load_schedules_from_db()
        
        # Start scheduler
        self.scheduler.start()
        
        logger.info("Bot initialized successfully")
    
    def run(self):
        """Run the bot"""
        # Create application
        self.application = Application.builder() \
            .token(BOT_TOKEN) \
            .post_init(self.post_init) \
            .build()
        
        # Setup handlers
        self.setup_handlers()
        
        # Add custom callback handler for promotion creation
        self.application.add_handler(CallbackQueryHandler(
            self.handle_promo_callback,
            pattern="^promo_"
        ))
        
        # Determine run method based on environment
        if WEBHOOK_URL:
            # Run with webhook (for Render)
            logger.info(f"Starting bot with webhook at {WEBHOOK_URL}")
            self.application.run_webhook(
                listen="0.0.0.0",
                port=PORT,
                url_path=BOT_TOKEN,
                webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}"
            )
        else:
            # Run with polling (for development)
            logger.info("Starting bot with polling")
            self.application.run_polling()

# ==================== MAIN EXECUTION ====================
if __name__ == "__main__":
    bot = PromotionBot()
    bot.run()
