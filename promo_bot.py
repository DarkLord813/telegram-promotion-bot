import os
import sqlite3
import json
import base64
import logging
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
import requests

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

class Database:
    def __init__(self):
        self.db_path = "promotion_bot.db"
        self.init_db()
    
    def init_db(self):
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
        
        # Insert default admin if specified
        admin_ids = os.getenv('ADMIN_USER_IDS', '')
        if admin_ids:
            for admin_id in admin_ids.split(','):
                if admin_id.strip():
                    cursor.execute('''
                        INSERT OR IGNORE INTO admins (user_id, username) 
                        VALUES (?, ?)
                    ''', (int(admin_id.strip()), 'default_admin'))
        
        conn.commit()
        conn.close()
    
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
            print(f"Error adding channel: {e}")
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
            print(f"Error adding payment: {e}")
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
            print(f"Error updating join status: {e}")
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
        
        conn.close()
        
        return {
            'channels': channels,
            'admins': admins,
            'payments': payments,
            'user_joins': user_joins,
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
            
            conn.commit()
            return True
        except Exception as e:
            print(f"Error importing data: {e}")
            return False
        finally:
            conn.close()

class GitHubBackup:
    def __init__(self, token, repo_owner, repo_name):
        self.token = token
        self.repo_owner = repo_owner
        self.repo_name = repo_name
        self.base_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/contents"
    
    def backup_database(self, database_export):
        try:
            # Convert data to JSON
            data_json = json.dumps(database_export, indent=2, default=str)
            data_bytes = data_json.encode('utf-8')
            data_b64 = base64.b64encode(data_bytes).decode('utf-8')
            
            # Create filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"backup_{timestamp}.json"
            
            # Prepare API request
            headers = {
                "Authorization": f"token {self.token}",
                "Accept": "application/vnd.github.v3+json"
            }
            
            data = {
                "message": f"Database backup {timestamp}",
                "content": data_b64,
                "branch": "main"
            }
            
            response = requests.put(
                f"{self.base_url}/{filename}",
                headers=headers,
                json=data
            )
            
            return response.status_code == 201
            
        except Exception as e:
            print(f"Backup error: {e}")
            return False
    
    def load_latest_backup(self):
        try:
            headers = {
                "Authorization": f"token {self.token}",
                "Accept": "application/vnd.github.v3+json"
            }
            
            # Get repository contents
            response = requests.get(self.base_url, headers=headers)
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
            print(f"Load backup error: {e}")
            return None

class PromotionBot:
    def __init__(self):
        self.token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.required_channels = self.get_required_channels()
        self.db = Database()
        
        # GitHub backup configuration
        github_token = os.getenv('GITHUB_TOKEN')
        repo_owner = os.getenv('GITHUB_REPO_OWNER')
        repo_name = os.getenv('GITHUB_REPO_NAME')
        
        if all([github_token, repo_owner, repo_name]):
            self.github_backup = GitHubBackup(github_token, repo_owner, repo_name)
            # Auto-load latest backup on startup
            self.load_backup_on_startup()
        else:
            self.github_backup = None
            logging.warning("GitHub backup not configured")
        
        # Pricing configuration
        self.pricing = {
            'week': {'stars': 10, 'days': 7},
            'month': {'stars': 30, 'days': 30},
            '3months': {'stars': 80, 'days': 90},
            '6months': {'stars': 160, 'days': 180},
            'year': {'stars': 300, 'days': 365}
        }
        
        self.application = Application.builder().token(self.token).build()
        self.setup_handlers()
    
    def get_required_channels(self):
        """Get list of channels that users must join"""
        # Hardcoded channel - @worldwidepromotion1
        # You'll need to get the channel ID for @worldwidepromotion1
        # You can get it by forwarding a message from the channel to @userinfobot
        # Or use your own method to get the channel ID
        channels = [
            {
                'id': '-1002140264322',  # Replace with actual channel ID for @worldwidepromotion1
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
        
        # Callback query handlers
        self.application.add_handler(CallbackQueryHandler(self.button_handler))
        
        # Message handler for channel posts and payments
        self.application.add_handler(MessageHandler(filters.FORWARDED, self.handle_forwarded_message))
        self.application.add_handler(MessageHandler(filters.ALL, self.handle_message))
    
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
        welcome_text = f"""
👋 Welcome {user.first_name} to Promotion Bot!

🌟 **Thanks for joining @worldwidepromotion1!**

🤖 **Bot Features:**
• Promote your Telegram channels
• Pay with Telegram Stars
• Automatic promotion across networks
• Duration-based pricing

💰 **Pricing:**
• 🕐 1 Week - 10 Stars
• 📅 1 Month - 30 Stars  
• 🗓️ 3 Months - 80 Stars
• 📆 6 Months - 160 Stars
• 🎊 1 Year - 300 Stars

Use /promote to start promoting your channel!
        """
        
        await update.message.reply_text(welcome_text)
    
    async def promote(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Check if user has joined required channels
        if not await self.check_join_requirement(update, context):
            return
        
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
            ]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "🎯 Choose promotion duration:\n\n"
            "After selection, forward a message from your channel or send your channel username.",
            reply_markup=reply_markup
        )
    
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
                    "You can now use all bot features. Use /promote to start promoting your channel!"
                )
            else:
                await self.show_join_required_message(update, not_joined)
        
        elif query.data.startswith('promo_'):
            # Check join requirement for promotion
            if not await self.check_join_requirement(update, context):
                return
            
            duration = query.data.replace('promo_', '')
            user_data['selected_duration'] = duration
            
            pricing = self.pricing[duration]
            
            await query.edit_message_text(
                f"✅ Selected: {duration.replace('months', ' Months').title()} - {pricing['stars']} Stars\n\n"
                f"Please forward a message from your channel or send your channel username (@username)."
            )
        
        elif query.data == 'admin_stats':
            await self.show_admin_stats(update, context)
        elif query.data == 'admin_backup':
            await self.create_backup(update, context)
        elif query.data == 'admin_restore':
            await self.restore_backup(update, context)
    
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
                    f"✅ Channel @{forwarded_from.username} promoted for {duration} (FREE - Admin privilege)!\n\n"
                    f"Promotion will expire in {pricing['days']} days."
                )
                
                # Backup to GitHub if configured
                if self.github_backup:
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
💫 Payment Required

Channel: @{forwarded_from.username}
Duration: {duration.replace('months', ' Months').title()}
Cost: {stars_required} Stars

To complete payment:
1. Go to @{bot_username}
2. Send exactly {stars_required} Stars
3. Forward the payment receipt here

Your promotion will be activated automatically after payment verification.
        """
        
        await update.message.reply_text(payment_text)
        user_data['pending_payment'] = {
            'payment_id': payment_id,
            'channel_id': forwarded_from.id,
            'username': forwarded_from.username,
            'title': forwarded_from.title,
            'duration': duration,
            'stars_required': stars_required
        }
    
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
                        f"✅ Payment received! Channel @{payment_data['username']} "
                        f"promoted for {payment_data['duration']}!\n\n"
                        f"Promotion will expire in {self.pricing[payment_data['duration']]['days']} days."
                    )
                    
                    # Backup to GitHub
                    if self.github_backup:
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
    
    async def admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.db.is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Admin access required.")
            return
        
        keyboard = [
            [InlineKeyboardButton("📊 Stats", callback_data="admin_stats")],
            [InlineKeyboardButton("🔄 Backup", callback_data="admin_backup")],
            [InlineKeyboardButton("📥 Restore", callback_data="admin_restore")],
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "🛠️ Admin Panel",
            reply_markup=reply_markup
        )
    
    async def show_admin_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        active_channels = self.db.get_active_channels()
        expired_channels = self.db.get_expired_channels()
        
        stats_text = f"""
📊 **Bot Statistics**

✅ Active Channels: {len(active_channels)}
❌ Expired Channels: {len(expired_channels)}
        
**Active Promotions:**
"""
        
        for channel in active_channels:
            username = channel[2] or "Private"
            title = channel[3]
            promo_end = datetime.strptime(channel[6], '%Y-%m-%d %H:%M:%S')
            days_left = (promo_end - datetime.now()).days
            
            stats_text += f"• {title} (@{username}) - {days_left} days left\n"
        
        await update.callback_query.message.reply_text(stats_text)
    
    async def create_backup(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.github_backup:
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
        if not self.github_backup:
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
        
        if not self.github_backup:
            await update.message.reply_text("❌ GitHub backup not configured.")
            return
        
        await update.message.reply_text("🔄 Creating backup...")
        
        data = self.db.export_data()
        success = self.github_backup.backup_database(data)
        
        if success:
            await update.message.reply_text("✅ Backup created successfully!")
        else:
            await update.message.reply_text("❌ Backup failed!")
    
    async def stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        
        for channel in active_channels[:10]:  # Show first 10 channels
            username = channel[2] or "Private"
            title = channel[3]
            stats_text += f"• {title} (@{username})\n"
        
        if len(active_channels) > 10:
            stats_text += f"\n... and {len(active_channels) - 10} more channels!"
        
        stats_text += "\nUse /promote to add your channel!"
        
        await update.message.reply_text(stats_text)
    
    async def monitor_promotions(self, context: ContextTypes.DEFAULT_TYPE):
        """Check for expired promotions"""
        expired_channels = self.db.get_expired_channels()
        
        for channel in expired_channels:
            channel_id = channel[1]
            channel_name = channel[3]
            self.db.expire_channel(channel_id)
            
            logging.info(f"Channel expired: {channel_name} (ID: {channel_id})")
    
    async def promote_channels(self, context: ContextTypes.DEFAULT_TYPE):
        """Promote channels across network"""
        active_channels = self.db.get_active_channels()
        
        if not active_channels:
            return
        
        promotion_message = "📢 **Promoted Channels:**\n\n"
        
        for channel in active_channels:
            username = channel[2]
            title = channel[3]
            
            if username:
                promotion_message += f"• [{title}](https://t.me/{username})\n"
            else:
                promotion_message += f"• {title}\n"
        
        promotion_message += "\nUse /promote to add your channel!"
        
        # Send to all target channels
        target_channels = os.getenv('TARGET_CHANNELS', '').split(',')
        
        for channel_id in target_channels:
            if channel_id.strip():
                try:
                    await context.bot.send_message(
                        chat_id=channel_id.strip(),
                        text=promotion_message,
                        parse_mode='Markdown',
                        disable_web_page_preview=True
                    )
                except Exception as e:
                    logging.error(f"Error promoting in channel {channel_id}: {e}")
    
    async def run(self):
        # Start monitoring task
        self.application.job_queue.run_repeating(
            self.monitor_promotions, 
            interval=3600,  # Check every hour
            first=10
        )
        
        # Start promotion task
        self.application.job_queue.run_repeating(
            self.promote_channels,
            interval=86400,  # Promote once daily
            first=30
        )
        
        # Auto-backup every 6 hours
        if self.github_backup:
            self.application.job_queue.run_repeating(
                self.auto_backup,
                interval=21600,  # 6 hours
                first=60
            )
        
        await self.application.run_polling()
    
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

def main():
    # Check required environment variables
    if not os.getenv('TELEGRAM_BOT_TOKEN'):
        logging.error("❌ TELEGRAM_BOT_TOKEN environment variable is required!")
        return
    
    bot = PromotionBot()
    
    logging.info("🤖 Starting Promotion Bot...")
    logging.info("🔒 Channel join requirement: @worldwidepromotion1")
    asyncio.run(bot.run())

if __name__ == '__main__':
    main()
