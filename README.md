# 🤖 Telegram Promotion Bot

A powerful Telegram bot for promoting channels across multiple Telegram networks. Users can promote their channels using Telegram Stars with various duration options, while the bot automatically posts promotion lists across target channels.

## ⚠️ Important Notice

This bot is designed for **LEGITIMATE PROMOTION SERVICES ONLY**. It does NOT:
- Artificially inflate member counts
- Violate Telegram's Terms of Service
- Use unauthorized automation
- Engage in spam activities

## ✨ Features

- **💰 Star-Based Payments**: Accept Telegram Stars for promotions (10-300 stars)
- **⏰ Flexible Durations**: 1 week to 1 year promotion periods
- **🔒 Force Channel Join**: Users must join @worldwidepromotion1 to use the bot
- **📢 Cross-Channel Promotion**: Automatically promotes channels across multiple target channels
- **🔄 Auto Message Cleanup**: Deletes promotion messages after 5 hours
- **☁️ GitHub Backup**: Automatic database backup to GitHub
- **🏥 Health Monitoring**: Built-in health check system
- **🛠️ Admin Panel**: Comprehensive admin controls and statistics
- **⌨️ Inline Keyboard**: User-friendly interface with buttons

## Pricing Plans

| Subscribers | Stars Required|
|-------------|---------------|
| 500         | 50 ⭐         |
| 1,000       | 100 ⭐        |
| 3,000       | 150 ⭐        |
| 5,000       | 200 ⭐        |
| 10,000      | 350 ⭐        |  

## 🚀 Quick Start

### Prerequisites

- Python 3.8+
- Telegram Bot Token from [@BotFather](https://t.me/BotFather)
- GitHub Personal Access Token
- Telegram channel @worldwidepromotion1

### Environment Variables

Create a `.env` file with the following variables:

```env
BOT_TOKEN=your_telegram_bot_token_here
GITHUB_TOKEN=ghp_your_github_token_here
GITHUB_REPO_OWNER=your_github_username
GITHUB_REPO_NAME=your_repository_name
GITHUB_BACKUP_PATH=backups/promotion_bot.db
GITHUB_BACKUP_BRANCH=main
ADMIN_USER_IDS=123456789,987654321
REQUIRED_CHANNELS=-1003429273795:worldwidepromotion1
TARGET_CHANNELS=-100123456789,-100987654321

## Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/telegram-promotion-bot.git
cd telegram-promotion-bot

## Contributing 🤝
1. Fork the repository 
2. Create a feature branch
3. Test thoroughly
4. Submit a pull request
