import os
import sqlite3
import json
import base64
import logging
import asyncio
import traceback
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from telegram.error import TelegramError
import requests

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

class Database:
    def __init__(self):
        self.db_path = "promotion_bot.db"
        try:
            self.init_db()
            logging.info("✅ Database initialized successfully")
        except Exception as e:
            logging.error(f"❌ Database initialization failed: {e}")
            raise
    
    def init_db(self):
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Channels table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS channels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id INTEGER UNIQUE,
                    channel_username TEXT,
                    channel_title TEXT,
                    owner_id INTEGER,
                    promotion_start DATETIME,
                    promotion_end DATETIME,
                    status TEXT DEFAULT 'active',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Admins table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS admins (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER UNIQUE,
                    username TEXT,
                    added_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Payments table (for star payments)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    channel_id INTEGER,
                    amount INTEGER,
                    duration TEXT,
                    status TEXT DEFAULT 'pending',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # User join status table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_joins (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    channel_id INTEGER,
                    joined BOOLEAN DEFAULT FALSE,
                    checked_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, channel_id)
                )
            ''')
            
            # Target channels table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS target_channels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id INTEGER UNIQUE,
                    channel_username TEXT,
                    channel_title TEXT,
                    added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    auto_added BOOLEAN DEFAULT TRUE
                )
            ''')
            
            # Promotion messages table (to track and delete messages)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS promotion_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id INTEGER,
                    message_id INTEGER,
                    posted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    delete_at DATETIME,
                    status TEXT DEFAULT 'active'
                )
            ''')
            
            # Insert default admin if specified
            admin_ids = os.getenv('ADMIN_USER_IDS', '')
            if admin_ids:
                for admin_id in admin_ids.split(','):
                    if admin_id.strip():
                        try:
                            cursor.execute('''
                                INSERT OR IGNORE INTO admins (user_id, username) 
                                VALUES (?, ?)
                            ''', (int(admin_id.strip()), 'default_admin'))
                            logging.info(f"✅ Added admin: {admin_id}")
                        except Exception as e:
                            logging.error(f"❌ Error adding admin {admin_id}: {e}")
            
            # Insert initial target channels from environment
            target_channels = os.getenv('TARGET_CHANNELS', '')
            if target_channels:
                for channel_id in target_channels.split(','):
                    if channel_id.strip():
                        try:
                            cursor.execute('''
                                INSERT OR IGNORE INTO target_channels (channel_id, auto_added) 
                                VALUES (?, ?)
                            ''', (channel_id.strip(), False))
                            logging.info(f"✅ Added target channel: {channel_id}")
                        except Exception as e:
                            logging.error(f"❌ Error adding target channel {channel_id}: {e}")
            
            conn.commit()
            conn.close()
            logging.info("✅ Database tables created successfully")
            
        except Exception as e:
            logging.error(f"❌ Database initialization failed: {e}")
            raise
    
    def add_channel(self, channel_id, channel_username, channel_title, owner_id, duration_days):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        promotion_start = datetime.now()
        promotion_end = promotion_start + timedelta(days=duration_days)
        
        try:
            cursor.execute('''
                INSERT OR REPLACE INTO channels 
                (channel_id, channel_username, channel_title, owner_id, promotion_start, promotion_end)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (channel_id, channel_username, channel_title, owner_id, promotion_start, promotion_end))
            conn.commit()
            return True
        except Exception as e:
            logging.error(f"Error adding channel: {e}")
            return False
        finally:
            conn.close()
    
    def get_active_channels(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM channels 
            WHERE promotion_end > datetime('now') AND status = 'active'
        ''')
        
        channels = cursor.fetchall()
        conn.close()
        return channels
    
    def get_expired_channels(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM channels 
            WHERE promotion_end <= datetime('now') AND status = 'active'
        ''')
        
        channels = cursor.fetchall()
        conn.close()
        return channels
    
    def expire_channel(self, channel_id):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE channels SET status = 'expired' 
            WHERE channel_id = ?
        ''', (channel_id,))
        
        conn.commit()
        conn.close()
    
    def is_admin(self, user_id):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM admins WHERE user_id = ?', (user_id,))
        admin = cursor.fetchone()
        conn.close()
        
        return admin is not None
    
    def add_admin(self, user_id, username):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                INSERT OR REPLACE INTO admins (user_id, username)
                VALUES (?, ?)
            ''', (user_id, username))
            conn.commit()
            return True
        except:
            return False
        finally:
            conn.close()
    
    def add_payment(self, user_id, channel_id, amount, duration):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                INSERT INTO payments (user_id, channel_id, amount, duration)
                VALUES (?, ?, ?, ?)
            ''', (user_id, channel_id, amount, duration))
            conn.commit()
            return cursor.lastrowid
        except Exception as e:
            logging.error(f"Error adding payment: {e}")
            return None
        finally:
            conn.close()
    
    def complete_payment(self, payment_id):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE payments SET status = 'completed' 
            WHERE id = ?
        ''', (payment_id,))
        
        conn.commit()
        conn.close()
    
    def update_user_join_status(self, user_id, channel_id, joined):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                INSERT OR REPLACE INTO user_joins (user_id, channel_id, joined, checked_at)
                VALUES (?, ?, ?, ?)
            ''', (user_id, channel_id, joined, datetime.now()))
            conn.commit()
        except Exception as e:
            logging.error(f"Error updating join status: {e}")
        finally:
            conn.close()
    
    def get_user_join_status(self, user_id, channel_id):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT joined FROM user_joins 
            WHERE user_id = ? AND channel_id = ?
        ''', (user_id, channel_id))
        
        result = cursor.fetchone()
        conn.close()
        
        return result[0] if result else False
    
    def add_target_channel(self, channel_id, channel_username=None, channel_title=None):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                INSERT OR REPLACE INTO target_channels 
                (channel_id, channel_username, channel_title, auto_added)
                VALUES (?, ?, ?, ?)
            ''', (channel_id, channel_username, channel_title, True))
            conn.commit()
            return True
        except Exception as e:
            logging.error(f"Error adding target channel: {e}")
            return False
        finally:
            conn.close()
    
    def get_target_channels(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM target_channels')
        channels = cursor.fetchall()
        conn.close()
        return channels
    
    def remove_target_channel(self, channel_id):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('DELETE FROM target_channels WHERE channel_id = ?', (channel_id,))
        conn.commit()
        conn.close()
    
    def add_promotion_message(self, channel_id, message_id):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        delete_at = datetime.now() + timedelta(hours=5)
        
        try:
            cursor.execute('''
                INSERT INTO promotion_messages 
                (channel_id, message_id, delete_at)
                VALUES (?, ?, ?)
            ''', (channel_id, message_id, delete_at))
            conn.commit()
            return True
        except Exception as e:
            logging.error(f"Error adding promotion message: {e}")
            return False
        finally:
            conn.close()
    
    def get_promotion_messages_to_delete(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM promotion_messages 
            WHERE delete_at <= datetime('now') AND status = 'active'
        ''')
        
        messages = cursor.fetchall()
        conn.close()
        return messages
    
    def mark_message_deleted(self, message_id):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE promotion_messages SET status = 'deleted' 
            WHERE message_id = ?
        ''', (message_id,))
        
        conn.commit()
        conn.close()
    
    def cleanup_old_messages(self):
        """Clean up messages older than 7 days"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cutoff_date = datetime.now() - timedelta(days=7)
        cursor.execute('''
            DELETE FROM promotion_messages 
            WHERE posted_at < ?
        ''', (cutoff_date,))
        
        conn.commit()
        conn.close()
    
    def export_data(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Export channels
        cursor.execute('SELECT * FROM channels')
        channels = cursor.fetchall()
        
        # Export admins
        cursor.execute('SELECT * FROM admins')
        admins = cursor.fetchall()
        
        # Export payments
        cursor.execute('SELECT * FROM payments')
        payments = cursor.fetchall()
        
        # Export user joins
        cursor.execute('SELECT * FROM user_joins')
        user_joins = cursor.fetchall()
        
        # Export target channels
        cursor.execute('SELECT * FROM target_channels')
        target_channels = cursor.fetchall()
        
        # Export promotion messages
        cursor.execute('SELECT * FROM promotion_messages')
        promotion_messages = cursor.fetchall()
        
        conn.close()
        
        return {
            'channels': channels,
            'admins': admins,
            'payments': payments,
            'user_joins': user_joins,
            'target_channels': target_channels,
            'promotion_messages': promotion_messages,
            'exported_at': datetime.now().isoformat()
        }
    
    def import_data(self, data):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # Clear existing data
            cursor.execute('DELETE FROM channels')
            cursor.execute('DELETE FROM admins')
            cursor.execute('DELETE FROM payments')
            cursor.execute('DELETE FROM user_joins')
            cursor.execute('DELETE FROM target_channels')
            cursor.execute('DELETE FROM promotion_messages')
            
            # Import channels
            for channel in data.get('channels', []):
                cursor.execute('''
                    INSERT INTO channels 
                    (id, channel_id, channel_username, channel_title, owner_id, promotion_start, promotion_end, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', channel)
            
            # Import admins
            for admin in data.get('admins', []):
                cursor.execute('''
                    INSERT INTO admins (id, user_id, username, added_at)
                    VALUES (?, ?, ?, ?)
                ''', admin)
            
            # Import payments
            for payment in data.get('payments', []):
                cursor.execute('''
                    INSERT INTO payments (id, user_id, channel_id, amount, duration, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', payment)
            
            # Import user joins
            for user_join in data.get('user_joins', []):
                cursor.execute('''
                    INSERT INTO user_joins (id, user_id, channel_id, joined, checked_at)
                    VALUES (?, ?, ?, ?, ?)
                ''', user_join)
            
            # Import target channels
            for target_channel in data.get('target_channels', []):
                cursor.execute('''
                    INSERT INTO target_channels (id, channel_id, channel_username, channel_title, added_at, auto_added)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', target_channel)
            
            # Import promotion messages
            for message in data.get('promotion_messages', []):
                cursor.execute('''
                    INSERT INTO promotion_messages (id, channel_id, message_id, posted_at, delete_at, status)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', message)
            
            conn.commit()
            return True
        except Exception as e:
            logging.error(f"Error importing data: {e}")
            return False
        finally:
            conn.close()

class GitHubBackup:
    def __init__(self):
        try:
            self.token = os.getenv('GITHUB_TOKEN')
            self.repo_owner = os.getenv('GITHUB_REPO_OWNER')
            self.repo_name = os.getenv('GITHUB_REPO_NAME')
            self.backup_path = os.getenv('GITHUB_BACKUP_PATH', 'backups')
            self.branch = os.getenv('GITHUB_BACKUP_BRANCH', 'main')
            
            # Log GitHub configuration status
            if self.token and self.repo_owner and self.repo_name:
                self.base_url = f"https://api.github.com/repos/{self.repo_owner}/{self.repo_name}/contents"
                logging.info("✅ GitHub backup configured")
            else:
                self.base_url = None
                logging.warning("⚠️ GitHub backup not fully configured")
        except Exception as e:
            logging.error(f"❌ GitHubBackup initialization failed: {e}")
            self.base_url = None
    
    def backup_database(self, database_export):
        if not self.token or not self.base_url:
            logging.warning("GitHub token not available, skipping backup")
            return False
            
        try:
            # Convert data to JSON
            data_json = json.dumps(database_export, indent=2, default=str)
            data_bytes = data_json.encode('utf-8')
            data_b64 = base64.b64encode(data_bytes).decode('utf-8')
            
            # Create filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{self.backup_path}/backup_{timestamp}.json"
            
            # Prepare API request
            headers = {
                "Authorization": f"token {self.token}",
                "Accept": "application/vnd.github.v3+json"
            }
            
            data = {
                "message": f"Database backup {timestamp}",
                "content": data_b64,
                "branch": self.branch
            }
            
            # Ensure backup directory exists
            self._ensure_backup_directory(headers)
            
            response = requests.put(
                f"{self.base_url}/{filename}",
                headers=headers,
                json=data
            )
            
            if response.status_code == 201:
                logging.info("✅ Backup created successfully on GitHub")
                return True
            else:
                logging.error(f"❌ Backup failed with status {response.status_code}: {response.text}")
                return False
            
        except Exception as e:
            logging.error(f"Backup error: {e}")
            return False
    
    def _ensure_backup_directory(self, headers):
        """Ensure the backup directory exists in the repo"""
        try:
            dir_path = self.backup_path.split('/')[0]
            response = requests.get(
                f"{self.base_url}/{dir_path}",
                headers=headers
            )
            
            if response.status_code == 404:
                # Create directory
                data = {
                    "message": f"Create {dir_path} directory",
                    "content": base64.b64encode(b" ").decode('utf-8'),
                    "branch": self.branch
                }
                requests.put(
                    f"{self.base_url}/{dir_path}/.gitkeep",
                    headers=headers,
                    json=data
                )
        except Exception as e:
            logging.error(f"Directory creation error: {e}")
    
    def load_latest_backup(self):
        if not self.token or not self.base_url:
            return None
            
        try:
            headers = {
                "Authorization": f"token {self.token}",
                "Accept": "application/vnd.github.v3+json"
            }
            
            # Get repository contents in backup directory
            response = requests.get(f"{self.base_url}/{self.backup_path}", headers=headers)
            if response.status_code != 200:
                return None
            
            files = response.json()
            backup_files = [f for f in files if f['name'].startswith('backup_') and f['name'].endswith('.json')]
            
            if not backup_files:
                return None
            
            # Get the latest backup file
            latest_backup = sorted(backup_files, key=lambda x: x['name'], reverse=True)[0]
            
            # Download file content
            file_response = requests.get(latest_backup['download_url'])
            if file_response.status_code == 200:
                return file_response.json()
            
            return None
            
        except Exception as e:
            logging.error(f"Load backup error: {e}")
            return None

class PromotionBot:
    def __init__(self):
        try:
            logging.info("🔄 Initializing PromotionBot...")
            
            self.token = os.getenv('BOT_TOKEN')
            if not self.token:
                raise ValueError("BOT_TOKEN environment variable is required")
            
            logging.info("✅ BOT_TOKEN loaded successfully")
            
            self.required_channels = self.get_required_channels()
            logging.info(f"✅ Required channels: {len(self.required_channels)}")
            
            # Initialize database first
            self.db = Database()
            
            # Initialize GitHub backup
            self.github_backup = GitHubBackup()
            
            # Auto-load latest backup on startup
            self.load_backup_on_startup()
            
            # Pricing configuration
            self.pricing = {
                'week': {'stars': 10, 'days': 7},
                'month': {'stars': 30, 'days': 30},
                '3months': {'stars': 80, 'days': 90},
                '6months': {'stars': 160, 'days': 180},
                'year': {'stars': 300, 'days': 365}
            }
            
            # Create application with modern approach
            self.application = Application.builder().token(self.token).build()
            self.setup_handlers()
            
            logging.info("✅ PromotionBot initialized successfully")
            
        except Exception as e:
            logging.error(f"❌ Failed to initialize PromotionBot: {e}")
            logging.error(traceback.format_exc())
            raise
    
    def get_required_channels(self):
        """Get list of channels that users must join"""
        channels = [
            {
                'id': '-1003429273795',  # Your channel ID for @worldwidepromotion1
                'username': 'worldwidepromotion1'
            }
        ]
        
        # Also check environment variable for additional channels
        channels_env = os.getenv('REQUIRED_CHANNELS', '')
        if channels_env:
            for channel in channels_env.split(','):
                if channel.strip():
                    parts = channel.strip().split(':')
                    if len(parts) == 2:
                        channels.append({
                            'id': parts[0].strip(),
                            'username': parts[1].strip().replace('@', '')
                        })
        
        return channels
    
    def load_backup_on_startup(self):
        """Load the latest backup when bot starts"""
        try:
            backup_data = self.github_backup.load_latest_backup()
            if backup_data:
                success = self.db.import_data(backup_data)
                if success:
                    logging.info("✅ Successfully loaded backup from GitHub")
                else:
                    logging.error("❌ Failed to import backup data")
            else:
                logging.info("ℹ️ No existing backup found, starting fresh")
        except Exception as e:
            logging.error(f"Backup load error: {e}")
    
    async def check_user_joined_channels(self, user_id):
        """Check if user has joined all required channels"""
        if not self.required_channels:
            return True, []
        
        not_joined = []
        
        for channel in self.required_channels:
            try:
                chat_member = await self.application.bot.get_chat_member(
                    chat_id=channel['id'],
                    user_id=user_id
                )
                
                is_joined = chat_member.status in ['member', 'administrator', 'creator']
                self.db.update_user_join_status(user_id, channel['id'], is_joined)
                
                if not is_joined:
                    not_joined.append(channel['username'])
                    
            except Exception as e:
                logging.error(f"Error checking channel membership for {channel['username']}: {e}")
                not_joined.append(channel['username'])
        
        return len(not_joined) == 0, not_joined
    
    def setup_handlers(self):
        # Command handlers
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CommandHandler("promote", self.promote))
        self.application.add_handler(CommandHandler("admin", self.admin))
        self.application.add_handler(CommandHandler("backup", self.manual_backup))
        self.application.add_handler(CommandHandler("stats", self.stats))
        self.application.add_handler(CommandHandler("check_join", self.check_join))
        self.application.add_handler(CommandHandler("health", self.health_check))
        self.application.add_handler(CommandHandler("targets", self.list_target_channels))
        
        # Callback query handlers
        self.application.add_handler(CallbackQueryHandler(self.button_handler))
        
        # Message handler for channel posts and payments
        self.application.add_handler(MessageHandler(filters.FORWARDED, self.handle_forwarded_message))
        self.application.add_handler(MessageHandler(filters.ALL, self.handle_message))
        
        # Handler for when bot is added to a channel
        self.application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, self.handle_bot_added_to_channel))
    
    async def check_join_requirement(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Check if user has joined required channels"""
        user_id = update.effective_user.id
        
        # Skip check for admins
        if self.db.is_admin(user_id):
            return True
        
        all_joined, not_joined = await self.check_user_joined_channels(user_id)
        
        if not all_joined:
            await self.show_join_required_message(update, not_joined)
            return False
        
        return True
    
    async def show_join_required_message(self, update: Update, not_joined_channels):
        """Show message asking user to join required channels"""
        message_text = "🔒 **Join Required**\n\n"
        message_text += "To use Promotion Bot, you must join our official channel first:\n\n"
        
        keyboard = []
        for channel_username in not_joined_channels:
            message_text += f"📢 @{channel_username} - Get amazing promotions and updates!\n"
            keyboard.append([InlineKeyboardButton(
                f"Join @{channel_username}", 
                url=f"https://t.me/{channel_username}"
            )])
        
        message_text += "\nAfter joining, click the button below to verify:"
        
        keyboard.append([InlineKeyboardButton("✅ I've Joined - Verify Now", callback_data="verify_join")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if update.callback_query:
            await update.callback_query.message.reply_text(message_text, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            await update.message.reply_text(message_text, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Check if user has joined required channels
        if not await self.check_join_requirement(update, context):
            return
        
        user = update.effective_user
        
        keyboard = [
            [InlineKeyboardButton("🚀 Promote Channel", callback_data="main_promote")],
            [InlineKeyboardButton("📊 View Statistics", callback_data="main_stats")],
            [InlineKeyboardButton("💰 Pricing", callback_data="main_pricing")],
            [InlineKeyboardButton("🛠️ Admin Panel", callback_data="main_admin")],
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        welcome_text = f"""
👋 Welcome {user.first_name} to Promotion Bot!

🌟 **Thanks for joining @worldwidepromotion1!**

🤖 **Bot Features:**
• Promote your Telegram channels
• Pay with Telegram Stars
• Automatic promotion across networks
• Duration-based pricing

Choose an option below to get started:
        """
        
        await update.message.reply_text(welcome_text, reply_markup=reply_markup)
    
    async def promote(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Check if user has joined required channels
        if not await self.check_join_requirement(update, context):
            return
        
        await self.show_promotion_menu(update, context)
    
    async def show_promotion_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [
            [
                InlineKeyboardButton("1 Week - 10⭐", callback_data="promo_week"),
                InlineKeyboardButton("1 Month - 30⭐", callback_data="promo_month"),
            ],
            [
                InlineKeyboardButton("3 Months - 80⭐", callback_data="promo_3months"),
                InlineKeyboardButton("6 Months - 160⭐", callback_data="promo_6months"),
            ],
            [
                InlineKeyboardButton("1 Year - 300⭐", callback_data="promo_year"),
            ],
            [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        text = "🎯 **Choose Promotion Duration**\n\nSelect how long you want to promote your channel:"
        
        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def check_join(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Manual command to check join status"""
        user_id = update.effective_user.id
        
        # Skip check for admins
        if self.db.is_admin(user_id):
            await update.message.reply_text("✅ You are an admin - no channel join required!")
            return
        
        all_joined, not_joined = await self.check_user_joined_channels(user_id)
        
        if all_joined:
            await update.message.reply_text("✅ You have joined @worldwidepromotion1! You can now use all bot features.")
        else:
            await self.show_join_required_message(update, not_joined)
    
    async def health_check(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Health check command"""
        health_status = "✅ **Bot Health Status**\n\n"
        
        # Check database
        try:
            active_channels = len(self.db.get_active_channels())
            health_status += f"• Database: ✅ Connected ({active_channels} active promotions)\n"
        except:
            health_status += "• Database: ❌ Connection failed\n"
        
        # Check GitHub backup
        try:
            if self.github_backup.load_latest_backup():
                health_status += "• GitHub Backup: ✅ Connected\n"
            else:
                health_status += "• GitHub Backup: ⚠️ No backups found\n"
        except:
            health_status += "• GitHub Backup: ❌ Connection failed\n"
        
        # Check bot status
        try:
            me = await context.bot.get_me()
            health_status += f"• Bot API: ✅ Connected (@{me.username})\n"
        except:
            health_status += "• Bot API: ❌ Connection failed\n"
        
        health_status += f"\n🕒 Uptime: {self.get_uptime()}"
        
        await update.message.reply_text(health_status, parse_mode='Markdown')
    
    def get_uptime(self):
        """Get bot uptime"""
        if hasattr(self, 'start_time'):
            uptime = datetime.now() - self.start_time
            days = uptime.days
            hours, remainder = divmod(uptime.seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            return f"{days}d {hours}h {minutes}m {seconds}s"
        return "Unknown"
    
    async def list_target_channels(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """List all target channels"""
        if not self.db.is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Admin access required.")
            return
        
        target_channels = self.db.get_target_channels()
        
        if not target_channels:
            await update.message.reply_text("📭 No target channels configured.")
            return
        
        text = "🎯 **Target Channels**\n\n"
        for channel in target_channels:
            channel_id, username, title, added_at, auto_added = channel[1], channel[2], channel[3], channel[4], channel[5]
            text += f"• {title or 'Unknown'} (@{username or 'N/A'})\n"
            text += f"  ID: {channel_id} | Auto: {'✅' if auto_added else '❌'}\n\n"
        
        await update.message.reply_text(text, parse_mode='Markdown')
    
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        user_data = context.user_data
        
        if query.data == 'verify_join':
            # User claims they've joined, verify again
            user_id = query.from_user.id
            all_joined, not_joined = await self.check_user_joined_channels(user_id)
            
            if all_joined:
                await query.edit_message_text(
                    "✅ Verification successful! You have joined @worldwidepromotion1!\n\n"
                    "You can now use all bot features. Use the buttons below to get started:",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🚀 Promote Channel", callback_data="main_promote")],
                        [InlineKeyboardButton("📊 View Statistics", callback_data="main_stats")],
                        [InlineKeyboardButton("💰 Pricing", callback_data="main_pricing")],
                    ])
                )
            else:
                await self.show_join_required_message(update, not_joined)
        
        elif query.data == 'main_menu':
            await self.show_main_menu(update, context)
        
        elif query.data == 'main_promote':
            await self.show_promotion_menu(update, context)
        
        elif query.data == 'main_stats':
            await self.stats(update, context, from_callback=True)
        
        elif query.data == 'main_pricing':
            await self.show_pricing(update, context)
        
        elif query.data == 'main_admin':
            if self.db.is_admin(query.from_user.id):
                await self.admin(update, context, from_callback=True)
            else:
                await query.answer("❌ Admin access required.", show_alert=True)
        
        elif query.data.startswith('promo_'):
            # Check join requirement for promotion
            if not await self.check_join_requirement(update, context):
                return
            
            duration = query.data.replace('promo_', '')
            user_data['selected_duration'] = duration
            
            pricing = self.pricing[duration]
            
            await query.edit_message_text(
                f"✅ **Selected: {duration.replace('months', ' Months').title()}**\n"
                f"💫 **Cost: {pricing['stars']} Stars**\n\n"
                f"Please forward a message from your channel or send your channel username (@username).",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back to Promotion", callback_data="main_promote")]
                ])
            )
        
        elif query.data == 'admin_stats':
            await self.show_admin_stats(update, context)
        elif query.data == 'admin_backup':
            await self.create_backup(update, context)
        elif query.data == 'admin_restore':
            await self.restore_backup(update, context)
        elif query.data == 'admin_targets':
            await self.list_target_channels(update, context)
    
    async def show_main_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [
            [InlineKeyboardButton("🚀 Promote Channel", callback_data="main_promote")],
            [InlineKeyboardButton("📊 View Statistics", callback_data="main_stats")],
            [InlineKeyboardButton("💰 Pricing", callback_data="main_pricing")],
        ]
        
        if self.db.is_admin(update.callback_query.from_user.id):
            keyboard.append([InlineKeyboardButton("🛠️ Admin Panel", callback_data="main_admin")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(
            "🏠 **Main Menu**\n\nChoose an option below:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    async def show_pricing(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = "💰 **Pricing Plans**\n\n"
        for duration, info in self.pricing.items():
            text += f"• {duration.replace('months', ' Months').title()}: {info['stars']} Stars\n"
        
        text += "\nUse the promotion menu to select a plan!"
        
        keyboard = [[InlineKeyboardButton("🚀 Start Promotion", callback_data="main_promote")]]
        
        if update.callback_query:
            await update.callback_query.edit_message_text(
                text, 
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(
                text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
    
    async def handle_forwarded_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Check if user has joined required channels
        if not await self.check_join_requirement(update, context):
            return
        
        user_data = context.user_data
        
        if 'selected_duration' not in user_data:
            await update.message.reply_text("Please use /promote first to select duration.")
            return
        
        forwarded_from = update.message.forward_from_chat
        
        if not forwarded_from:
            await update.message.reply_text("Please forward a message from a channel.")
            return
        
        # Check if user is admin of the channel
        try:
            chat_member = await self.application.bot.get_chat_member(
                forwarded_from.id, 
                update.effective_user.id
            )
            
            if chat_member.status not in ['creator', 'administrator']:
                await update.message.reply_text("❌ You must be an admin of this channel to promote it.")
                return
                
        except Exception as e:
            await update.message.reply_text("❌ Cannot verify channel admin status. Make sure I'm added to your channel.")
            return
        
        duration = user_data['selected_duration']
        pricing = self.pricing[duration]
        
        # For admins - free promotion
        if self.db.is_admin(update.effective_user.id):
            success = self.db.add_channel(
                forwarded_from.id,
                forwarded_from.username,
                forwarded_from.title,
                update.effective_user.id,
                pricing['days']
            )
            
            if success:
                await update.message.reply_text(
                    f"✅ **Channel Promoted!**\n\n"
                    f"📢 Channel: @{forwarded_from.username}\n"
                    f"⏰ Duration: {duration.replace('months', ' Months').title()}\n"
                    f"💰 Cost: FREE (Admin privilege)\n"
                    f"📅 Expires: {pricing['days']} days\n\n"
                    f"Your channel will be promoted across our network!",
                    parse_mode='Markdown'
                )
                
                # Backup to GitHub if configured
                if self.github_backup.token:
                    data = self.db.export_data()
                    self.github_backup.backup_database(data)
            else:
                await update.message.reply_text("❌ Error adding channel. Please try again.")
            
            return
        
        # For regular users - require stars
        bot_username = (await self.application.bot.get_me()).username
        stars_required = pricing['stars']
        
        # Create payment record
        payment_id = self.db.add_payment(
            update.effective_user.id,
            forwarded_from.id,
            stars_required,
            duration
        )
        
        payment_text = f"""
💫 **Payment Required**

📢 Channel: @{forwarded_from.username}
⏰ Duration: {duration.replace('months', ' Months').title()}
💫 Cost: {stars_required} Stars

To complete payment:
1. Go to @{bot_username}
2. Send exactly {stars_required} Stars
3. Forward the payment receipt here

Your promotion will be activated automatically after payment verification.
        """
        
        keyboard = [
            [InlineKeyboardButton("📋 Back to Menu", callback_data="main_menu")]
        ]
        
        await update.message.reply_text(
            payment_text, 
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        
        user_data['pending_payment'] = {
            'payment_id': payment_id,
            'channel_id': forwarded_from.id,
            'username': forwarded_from.username,
            'title': forwarded_from.title,
            'duration': duration,
            'stars_required': stars_required
        }
    
    async def handle_bot_added_to_channel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle when bot is added to a channel as admin"""
        try:
            for member in update.message.new_chat_members:
                if member.id == context.bot.id:
                    chat = update.message.chat
                    
                    # Check if bot is admin in the channel
                    try:
                        chat_member = await context.bot.get_chat_member(chat.id, context.bot.id)
                        if chat_member.status in ['administrator', 'creator']:
                            # Add to target channels (bot stays in channel permanently)
                            self.db.add_target_channel(
                                chat.id,
                                chat.username,
                                chat.title
                            )
                            
                            logging.info(f"✅ Auto-added channel to targets: {chat.title} (ID: {chat.id})")
                            
                            # Send confirmation (optional)
                            try:
                                await context.bot.send_message(
                                    chat_id=chat.id,
                                    text="🤖 **Promotion Bot Activated!**\n\n"
                                         "I've been automatically added to the target channels list. "
                                         "I will promote channels here regularly. "
                                         "Promotion messages will be automatically deleted after 5 hours.",
                                    parse_mode='Markdown'
                                )
                            except:
                                pass  # Silent fail if can't send message
                    except Exception as e:
                        logging.error(f"Error checking admin status: {e}")
                    
                    break
        except Exception as e:
            logging.error(f"Error handling bot addition: {e}")
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle regular messages including payment receipts"""
        # First check if this is a command that should bypass join check
        if update.message and update.message.text and update.message.text.startswith('/'):
            return
        
        # Check join requirement for non-command messages
        if not await self.check_join_requirement(update, context):
            return
        
        user_data = context.user_data
        
        # Check if this might be a payment receipt
        if (update.message and update.message.star and 
            'pending_payment' in user_data):
            
            payment_data = user_data['pending_payment']
            stars_sent = update.message.star
            
            if stars_sent == payment_data['stars_required']:
                # Payment successful
                self.db.complete_payment(payment_data['payment_id'])
                
                success = self.db.add_channel(
                    payment_data['channel_id'],
                    payment_data['username'],
                    payment_data['title'],
                    update.effective_user.id,
                    self.pricing[payment_data['duration']]['days']
                )
                
                if success:
                    await update.message.reply_text(
                        f"✅ **Payment Received!**\n\n"
                        f"📢 Channel: @{payment_data['username']}\n"
                        f"⏰ Duration: {payment_data['duration'].replace('months', ' Months').title()}\n"
                        f"💫 Stars: {payment_data['stars_required']}\n"
                        f"📅 Expires: {self.pricing[payment_data['duration']]['days']} days\n\n"
                        f"Your channel is now being promoted across our network!",
                        parse_mode='Markdown',
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("📋 Back to Menu", callback_data="main_menu")]
                        ])
                    )
                    
                    # Backup to GitHub
                    if self.github_backup.token:
                        data = self.db.export_data()
                        self.github_backup.backup_database(data)
                else:
                    await update.message.reply_text("❌ Error activating promotion. Please contact admin.")
                
                del user_data['pending_payment']
            else:
                await update.message.reply_text(
                    f"❌ Incorrect amount. Required: {payment_data['stars_required']} Stars. "
                    f"Received: {stars_sent} Stars."
                )
    
    async def admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE, from_callback=False):
        if not self.db.is_admin(update.effective_user.id):
            if from_callback:
                await update.callback_query.answer("❌ Admin access required.", show_alert=True)
                return
            else:
                await update.message.reply_text("❌ Admin access required.")
                return
        
        keyboard = [
            [InlineKeyboardButton("📊 Stats", callback_data="admin_stats")],
            [InlineKeyboardButton("🔄 Backup", callback_data="admin_backup")],
            [InlineKeyboardButton("📥 Restore", callback_data="admin_restore")],
            [InlineKeyboardButton("🎯 Target Channels", callback_data="admin_targets")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")],
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        text = "🛠️ **Admin Panel**\n\nSelect an option below:"
        
        if from_callback:
            await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def show_admin_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        active_channels = self.db.get_active_channels()
        expired_channels = self.db.get_expired_channels()
        target_channels = self.db.get_target_channels()
        
        stats_text = f"""
📊 **Bot Statistics**

✅ Active Promotions: {len(active_channels)}
❌ Expired Channels: {len(expired_channels)}
🎯 Target Channels: {len(target_channels)}

**Active Promotions:**
"""
        
        for channel in active_channels[:5]:  # Show first 5 channels
            username = channel[2] or "Private"
            title = channel[3]
            promo_end = datetime.strptime(channel[6], '%Y-%m-%d %H:%M:%S')
            days_left = (promo_end - datetime.now()).days
            
            stats_text += f"• {title} (@{username}) - {days_left} days left\n"
        
        if len(active_channels) > 5:
            stats_text += f"\n... and {len(active_channels) - 5} more channels"
        
        keyboard = [[InlineKeyboardButton("🔙 Back to Admin", callback_data="main_admin")]]
        
        await update.callback_query.message.reply_text(
            stats_text, 
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    async def create_backup(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.github_backup.token:
            await update.callback_query.message.reply_text("❌ GitHub backup not configured.")
            return
        
        await update.callback_query.message.reply_text("🔄 Creating backup...")
        
        data = self.db.export_data()
        success = self.github_backup.backup_database(data)
        
        if success:
            await update.callback_query.message.reply_text("✅ Backup created successfully on GitHub!")
        else:
            await update.callback_query.message.reply_text("❌ Backup failed!")
    
    async def restore_backup(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.github_backup.token:
            await update.callback_query.message.reply_text("❌ GitHub backup not configured.")
            return
        
        await update.callback_query.message.reply_text("🔄 Restoring from latest backup...")
        
        backup_data = self.github_backup.load_latest_backup()
        if backup_data:
            success = self.db.import_data(backup_data)
            if success:
                await update.callback_query.message.reply_text("✅ Backup restored successfully!")
            else:
                await update.callback_query.message.reply_text("❌ Restore failed!")
        else:
            await update.callback_query.message.reply_text("❌ No backup found!")
    
    async def manual_backup(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Manual backup command"""
        if not self.db.is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Admin access required.")
            return
        
        if not self.github_backup.token:
            await update.message.reply_text("❌ GitHub backup not configured.")
            return
        
        await update.message.reply_text("🔄 Creating backup...")
        
        data = self.db.export_data()
        success = self.github_backup.backup_database(data)
        
        if success:
            await update.message.reply_text("✅ Backup created successfully!")
        else:
            await update.message.reply_text("❌ Backup failed!")
    
    async def stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE, from_callback=False):
        """Show public statistics"""
        # Check join requirement
        if not await self.check_join_requirement(update, context):
            return
        
        active_channels = self.db.get_active_channels()
        
        stats_text = f"""
📊 **Public Statistics**

✅ Active Promotions: {len(active_channels)}

**Currently Promoting:**
"""
        
        for channel in active_channels[:5]:  # Show first 5 channels
            username = channel[2] or "Private"
            title = channel[3]
            stats_text += f"• {title} (@{username})\n"
        
        if len(active_channels) > 5:
            stats_text += f"\n... and {len(active_channels) - 5} more channels!"
        
        stats_text += "\nUse the promotion menu to add your channel!"
        
        keyboard = [
            [InlineKeyboardButton("🚀 Promote Channel", callback_data="main_promote")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")],
        ]
        
        if from_callback:
            await update.callback_query.edit_message_text(
                stats_text, 
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(
                stats_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
    
    async def monitor_promotions(self, context: ContextTypes.DEFAULT_TYPE):
        """Check for expired promotions"""
        expired_channels = self.db.get_expired_channels()
        
        for channel in expired_channels:
            channel_id = channel[1]
            channel_name = channel[3]
            self.db.expire_channel(channel_id)
            
            logging.info(f"Channel expired: {channel_name} (ID: {channel_id})")
    
    async def promote_channels(self, context: ContextTypes.DEFAULT_TYPE):
        """Promote channels across network - works even if bot is not admin"""
        active_channels = self.db.get_active_channels()
        
        if not active_channels:
            return
        
        promotion_message = "📢 **Promoted Channels**\n\n"
        
        for channel in active_channels:
            username = channel[2]
            title = channel[3]
            
            if username:
                promotion_message += f"• [{title}](https://t.me/{username})\n"
            else:
                promotion_message += f"• {title}\n"
        
        promotion_message += "\n💫 Promote your channel with @worldwidepromotion1_bot"
        
        # Send to all target channels (even if bot is not admin)
        target_channels = self.db.get_target_channels()
        
        successful_posts = 0
        
        for channel in target_channels:
            channel_id = channel[1]
            channel_title = channel[3] or "Unknown"
            
            try:
                # Try to send message even if bot is not admin
                sent_message = await context.bot.send_message(
                    chat_id=channel_id,
                    text=promotion_message,
                    parse_mode='Markdown',
                    disable_web_page_preview=True
                )
                
                # Store message info for deletion after 5 hours
                self.db.add_promotion_message(channel_id, sent_message.message_id)
                
                successful_posts += 1
                logging.info(f"✅ Promoted channels in: {channel_title} (ID: {channel_id})")
                
            except Exception as e:
                error_msg = str(e).lower()
                if any(x in error_msg for x in ['bot was blocked', 'chat not found', 'not enough rights', 'forbidden']):
                    # Remove inaccessible channels
                    self.db.remove_target_channel(channel_id)
                    logging.info(f"❌ Removed inaccessible target channel: {channel_title} (ID: {channel_id}) - {e}")
                else:
                    logging.warning(f"⚠️ Could not post in {channel_title} (ID: {channel_id}): {e}")
        
        logging.info(f"📊 Promotion round completed: {successful_posts}/{len(target_channels)} channels")
    
    async def delete_old_promotion_messages(self, context: ContextTypes.DEFAULT_TYPE):
        """Delete promotion messages after 5 hours"""
        messages_to_delete = self.db.get_promotion_messages_to_delete()
        
        deleted_count = 0
        error_count = 0
        
        for message in messages_to_delete:
            channel_id = message[1]
            message_id = message[2]
            
            try:
                await context.bot.delete_message(
                    chat_id=channel_id,
                    message_id=message_id
                )
                self.db.mark_message_deleted(message_id)
                deleted_count += 1
                logging.info(f"✅ Deleted old promotion message from channel: {channel_id}")
            except Exception as e:
                error_count += 1
                logging.warning(f"⚠️ Could not delete message {message_id} from {channel_id}: {e}")
                # Mark as deleted anyway to avoid retrying
                self.db.mark_message_deleted(message_id)
        
        if deleted_count > 0 or error_count > 0:
            logging.info(f"🗑️ Message cleanup: {deleted_count} deleted, {error_count} errors")
        
        # Clean up old database records
        self.db.cleanup_old_messages()
    
    async def health_monitor(self, context: ContextTypes.DEFAULT_TYPE):
        """Health monitoring task"""
        try:
            # Test database
            self.db.get_active_channels()
            
            # Test GitHub connection
            if self.github_backup.token:
                self.github_backup.load_latest_backup()
            
            # Test bot API
            await context.bot.get_me()
            
            logging.info("✅ Health check passed")
        except Exception as e:
            logging.error(f"❌ Health check failed: {e}")
    
    async def keep_alive(self, context: ContextTypes.DEFAULT_TYPE):
        """Keep alive system - sends periodic requests to prevent sleeping"""
        try:
            # Simple operation to keep the bot active
            active_channels = len(self.db.get_active_channels())
            logging.info(f"🤖 Keep alive - {active_channels} active promotions")
        except Exception as e:
            logging.error(f"Keep alive error: {e}")
    
    async def auto_backup(self, context: ContextTypes.DEFAULT_TYPE):
        """Automatically backup database"""
        try:
            data = self.db.export_data()
            success = self.github_backup.backup_database(data)
            if success:
                logging.info("✅ Auto-backup completed successfully")
            else:
                logging.error("❌ Auto-backup failed")
        except Exception as e:
            logging.error(f"Auto-backup error: {e}")
    
    async def run(self):
        self.start_time = datetime.now()
        
        # Start monitoring tasks
        self.application.job_queue.run_repeating(
            self.monitor_promotions, 
            interval=3600,  # Check every hour
            first=10
        )
        
        # Start promotion task
        self.application.job_queue.run_repeating(
            self.promote_channels,
            interval=43200,  # Promote every 12 hours
            first=30
        )
        
        # Delete old promotion messages (5 hours)
        self.application.job_queue.run_repeating(
            self.delete_old_promotion_messages,
            interval=1800,  # Check every 30 minutes
            first=60
        )
        
        # Health monitoring
        self.application.job_queue.run_repeating(
            self.health_monitor,
            interval=300,  # Every 5 minutes
            first=10
        )
        
        # Keep alive system
        self.application.job_queue.run_repeating(
            self.keep_alive,
            interval=300,  # Every 5 minutes
            first=15
        )
        
        # Auto-backup every 6 hours
        if self.github_backup.token:
            self.application.job_queue.run_repeating(
                self.auto_backup,
                interval=21600,  # 6 hours
                first=60
            )
        
        logging.info("🤖 Starting Promotion Bot with all features...")
        
        # Use the simple modern approach
        await self.application.run_polling()

def main():
    try:
        # Check required environment variables with debug info
        logging.info("🔧 Starting bot initialization...")
        
        bot_token = os.getenv('BOT_TOKEN')
        if not bot_token:
            logging.error("❌ BOT_TOKEN environment variable is required!")
            logging.info("Available environment variables:")
            for key in os.environ:
                if 'TOKEN' in key or 'BOT' in key or 'GITHUB' in key:
                    logging.info(f"  {key}: {'*' * len(os.getenv(key)) if os.getenv(key) else 'NOT SET'}")
            return
        
        logging.info("✅ BOT_TOKEN found")
        
        # Check if we're on Render
        if os.getenv('RENDER'):
            logging.info("🚀 Running on Render platform")
        
        # Initialize bot
        logging.info("🔄 Creating PromotionBot instance...")
        bot = PromotionBot()
        logging.info("✅ PromotionBot created successfully")
        
        # Run bot
        logging.info("🤖 Starting bot...")
        asyncio.run(bot.run())
        
    except Exception as e:
        logging.error(f"💥 Critical error during startup: {e}")
        logging.error(traceback.format_exc())

if __name__ == '__main__':
    main()
