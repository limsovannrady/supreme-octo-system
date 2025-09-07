import os
import logging
from pathlib import Path
import asyncio
import sys
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import yt_dlp
import tempfile
import shutil
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import time

# Enable detailed logging for debugging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('bot.log')
    ]
)

logger = logging.getLogger(__name__)

# Bot token from environment variable with validation
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
if not BOT_TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN environment variable is not set!")
    logger.info("Available environment variables: %s", list(os.environ.keys()))
    sys.exit(1)

logger.info(f"Bot token loaded: {BOT_TOKEN[:10]}...{BOT_TOKEN[-4:] if len(BOT_TOKEN) > 14 else 'short'}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    try:
        if update.effective_user and update.message:
            username = update.effective_user.first_name or update.effective_user.username or "មិត្តភក្តិ"
            await update.message.reply_text(
                f'សួស្ដី {username} ផ្ញើតំណភ្ជាប់ YouTube មកខ្ញុំ'
            )
            logger.info(f"Start command from user: {username}")
    except Exception as e:
        logger.error(f"Error in start command: {e}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /help is issued."""
    try:
        if update.message:
            await update.message.reply_text(
                'គ្រាន់តែផ្ញើតំណភ្ជាប់ YouTube មកខ្ញុំ ហើយខ្ញុំនឹងដកយកអូឌីយ៉ូ និងផ្ញើជាឯកសារ MP3 មកវិញ។'
            )
            logger.info("Help command executed")
    except Exception as e:
        logger.error(f"Error in help command: {e}")

def is_youtube_url(url: str) -> bool:
    """Check if the URL is a valid YouTube URL."""
    youtube_domains = ['youtube.com', 'youtu.be', 'www.youtube.com', 'm.youtube.com']
    return any(domain in url.lower() for domain in youtube_domains)

async def download_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Download audio from YouTube link and send it as MP3."""
    if not update.message or not update.message.text:
        return
    
    message = update.message.text.strip()
    
    # Check if this is a reply to a message with a YouTube link
    if update.message.reply_to_message and update.message.reply_to_message.text:
        replied_message = update.message.reply_to_message.text.strip()
        if is_youtube_url(replied_message):
            message = replied_message
        elif not is_youtube_url(message):
            await update.message.reply_text('សូមផ្ញើតំណភ្ជាប់ YouTube ត្រឹមត្រូវ ឬឆ្លើយតបទៅកាន់សារដែលមានតំណនោះ។')
            return
    elif not is_youtube_url(message):
        await update.message.reply_text('សូមផ្ញើតំណភ្ជាប់ YouTube ត្រឹមត្រូវ ឬឆ្លើយតបទៅកាន់សារដែលមានតំណនោះ។')
        return
    
    # Send "processing" message
    processing_msg = await update.message.reply_text('🎵 កំពុងដំណើរការសំណើរបស់អ្នក...')
    
    try:
        # Create temporary directory for downloads
        with tempfile.TemporaryDirectory() as temp_dir:
            # Configure yt-dlp options for audio extraction
            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),  # Use full title, we'll rename after parsing
                'noplaylist': True,
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # Extract info to get the title and metadata
                info = ydl.extract_info(message, download=False)
                original_title = info.get('title', 'Unknown Title') if info else 'Unknown Title'
                duration = info.get('duration', 0) if info else 0
                
                # Smart artist and title parsing
                def parse_title_and_artist(title_str):
                    """Parse artist and title from YouTube title string"""
                    # Common patterns for artist - title separation
                    separators = [' - ', ' – ', ' | ', ' ｜ ', ': ']
                    
                    # Try to parse from title, looking for the main artist after any label/brand
                    for sep in separators:
                        if sep in title_str:
                            parts = title_str.split(sep)
                            # If we have multiple parts, find the actual artist name
                            if len(parts) >= 2:
                                # Skip the first part if it looks like a label/brand (like "GANZBERG Beer")
                                artist_part = parts[0].strip()
                                remaining_parts = sep.join(parts[1:]).strip()
                                
                                # If first part contains common label words, use second part as artist
                                label_words = ['beer', 'music', 'records', 'entertainment', 'studio', 'production']
                                if any(word in artist_part.lower() for word in label_words) and len(parts) >= 3:
                                    artist_part = parts[1].strip()
                                    remaining_parts = sep.join(parts[2:]).strip()
                                
                                # Check if artist part looks reasonable
                                if len(artist_part) < 50 and any(c.isalpha() for c in artist_part):
                                    return artist_part, remaining_parts
                    
                    # If no clear pattern found, try to extract from metadata
                    if info:
                        meta_artist = info.get('artist') or info.get('creator') or info.get('uploader', 'Various Artists')
                        if meta_artist not in ['NA', 'Unknown', 'Various Artists']:
                            return meta_artist, title_str
                    
                    return 'Various Artists', title_str
                
                artist, title = parse_title_and_artist(original_title)
                
                # Create clean filename (remove special characters, emojis, etc.)
                def clean_filename(text):
                    # Keep letters, numbers, spaces, hyphens, underscores, and common language chars
                    cleaned = ""
                    for c in text:
                        if c.isalnum() or c in (' ', '-', '_') or ord(c) > 127:  # Keep unicode chars
                            cleaned += c
                        else:
                            cleaned += " "  # Replace special chars with space
                    return " ".join(cleaned.split())  # Remove extra spaces
                
                safe_artist = clean_filename(artist)[:50].strip()
                safe_title = clean_filename(title)[:80].strip()
                
                # Format duration
                minutes = duration // 60
                seconds = duration % 60
                duration_str = f"{minutes}:{seconds:02d}" if duration > 0 else "Unknown"
                
                # Update processing message with clean title
                # Download message removed as requested
                
                # Download the audio
                ydl.download([message])
                
                # Find the downloaded file and rename it properly
                downloaded_files = list(Path(temp_dir).glob('*'))
                if not downloaded_files:
                    await processing_msg.edit_text('❌ កំហុស៖ គ្មានឯកសារបានទាញយកទេ។')
                    return
                
                audio_file = downloaded_files[0]
                
                # Rename file with proper artist - title format
                new_filename = f"{safe_artist} - {safe_title}.mp3"
                new_path = audio_file.parent / new_filename
                audio_file = audio_file.rename(new_path)
                
                # Check file size (Telegram has a 50MB limit for bots)
                file_size = audio_file.stat().st_size
                if file_size > 50 * 1024 * 1024:  # 50MB
                    await processing_msg.edit_text('❌ កំហុស៖ ឯកសារធំពេក (លើសពី 50MB)។')
                    return
                
                # Update processing message
                await processing_msg.edit_text('📤 កំពុងផ្ទុកឯកសារអូឌីយ៉ូ...')
                
                # Create proper filename for Telegram
                filename = f"{safe_artist} - {safe_title}.mp3"
                
                # Comprehensive quote functionality - covers all scenarios
                with open(audio_file, 'rb') as audio:
                    # Determine which message to reply to for perfect quoting
                    if update.message.reply_to_message:
                        # If user replied to a message, quote that original message
                        reply_to_id = update.message.reply_to_message.message_id
                    else:
                        # If user sent direct link, quote their message
                        reply_to_id = update.message.message_id
                    
                    # Clean caption format as requested
                    caption = f"{safe_artist} - {safe_title}"
                    
                    await update.message.reply_audio(
                        audio=audio,
                        title=safe_title,
                        performer=safe_artist,
                        duration=duration,
                        filename=filename,
                        reply_to_message_id=reply_to_id,
                        caption=caption
                    )
                
                # Delete the processing message
                await processing_msg.delete()
                
    except Exception as e:
        logger.error(f"Error processing YouTube link: {e}")
        await processing_msg.edit_text(f'❌ កំហុស៖ {str(e)}')

# Health check handler for Render
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'OK')
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        pass  # Suppress default logging

def start_health_server():
    """Start health check server for Render monitoring"""
    port = int(os.environ.get('PORT', 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    logger.info(f"Health check server starting on port {port}")
    server.serve_forever()

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle errors caused by Updates."""
    logger.error(f"Exception while handling an update: {context.error}")
    
    # Try to send error message to user if possible
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text(
                'សូមអភ័យទោស! មានបញ្ហាបន្តិចបន្តួច។ សូមព្យាយាមម្តងទៀត។'
            )
    except Exception as e:
        logger.error(f"Could not send error message to user: {e}")

def main() -> None:
    """Start the bot with error handling and health check."""
    try:
        logger.info("Starting Telegram YouTube Bot...")
        
        # Start health check server in background thread
        health_thread = threading.Thread(target=start_health_server, daemon=True)
        health_thread.start()
        logger.info("Health check server started")
        
        # Create the Application with error handling
        application = Application.builder().token(BOT_TOKEN).build()
        
        # Add error handler
        application.add_error_handler(error_handler)
        
        # Add command handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, download_audio))
        
        logger.info("Bot handlers registered successfully")
        
        # Run the bot with polling (better for this use case)
        logger.info("Starting bot polling...")
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
            close_loop=False
        )
        
    except Exception as e:
        logger.error(f"Critical error starting bot: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()