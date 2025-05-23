import discord
from discord import app_commands
from discord.ext import commands
from gtts import gTTS
import asyncio
import os
import logging
from collections import deque
from discord import ui
import json
from datetime import datetime
import yt_dlp as youtube_dl

# Налаштування логування та конфігурації
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv('DISCORD_TOKEN') or "MTM3MTg2MDIyMTU5NjY2Mzk5OA.GYVvUP.sV81L9L4D_0d5-v_NHeR7JpCWmqIogJqRCLzzc"
TEMP_AUDIO_PATH = "temp_audio"
SETTINGS_FILE = 'bot_settings.json'

# Налаштування для youtube-dl
YDL_OPTIONS = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
    'extract_flat': False,
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',
        'preferredquality': '192',
    }],
}

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -nostdin',
    'options': '-vn -acodec libopus -b:a 128k -f opus'
}

youtube_dl.utils.bug_reports_message = lambda: ''
youtube_dl.YoutubeDL.params = lambda self: {'extract_flat': False}
os.makedirs(TEMP_AUDIO_PATH, exist_ok=True)

# Мюзік клас
class MusicBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        
        super().__init__(command_prefix="/", intents=intents)
        
        self.playlist = deque()
        self.now_playing = None
        self.last_played = None
        self.loop_enabled = False
        self._tts_settings = {
            'enabled': False,
            'delete_after': False,
            'channel_id': None
        }
        self.music_messages = {}
        self.track_info = {}
        
        self.load_settings()

    @property
    def tts_settings(self):
        return self._tts_settings

    def load_settings(self):
        try:
            if os.path.exists(SETTINGS_FILE):
                with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                    settings = json.load(f)
                    self._tts_settings.update(settings.get('tts', {}))
        except Exception as e:
            logger.error(f"Error loading settings: {e}")

    def save_settings(self):
        try:
            with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
                json.dump({'tts': self._tts_settings}, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error saving settings: {e}")

bot = MusicBot()

# Допоміжні функції для роботи з аудіо
async def cleanup_temp_files():
    for filename in os.listdir(TEMP_AUDIO_PATH):
        file_path = os.path.join(TEMP_AUDIO_PATH, filename)
        try:
            if os.path.isfile(file_path):
                os.unlink(file_path)
        except Exception as e:
            logger.error(f"Error deleting file {file_path}: {e}")

def create_tts_audio(text: str, language='en'):
    try:
        tts = gTTS(text=text, lang=language)
        filename = os.path.join(TEMP_AUDIO_PATH, f"tts_{datetime.now().timestamp()}.mp3")
        tts.save(filename)
        return filename
    except Exception as e:
        logger.error(f"Error creating TTS: {e}")
        raise

async def log_message(message: discord.Message):
    logger.info(f"TTS message from {message.author}: {message.content}")

async def extract_audio_info(url: str):
    try:
        with youtube_dl.YoutubeDL(YDL_OPTIONS) as ydl:
            info = ydl.extract_info(url, download=False)
            if 'entries' in info:
                info = info['entries'][0]
            
            duration = info.get('duration', 0)
            minutes, seconds = divmod(duration, 60)
            duration_str = f"{int(minutes)}:{int(seconds):02d}"
            
            return {
                'url': info['url'],
                'title': info.get('title', 'Unknown track'),
                'duration': duration_str,
                'original_url': url
            }
    except Exception as e:
        logger.error(f"Error getting audio info: {e}")
        return None

async def play_next(guild: discord.Guild):
    voice_client = guild.voice_client
    
    if not voice_client or not voice_client.is_connected():
        return
    
    if not bot.playlist:
        bot.now_playing = None
        if guild.id in bot.music_messages:
            try:
                message = bot.music_messages[guild.id]
                view = MusicView()
                embed = await view.create_music_embed()
                await message.edit(embed=embed, view=view)
            except Exception as e:
                logger.error(f"Error updating panel: {e}")
        return
    
    if voice_client.is_playing() or voice_client.is_paused():
        return
    
    bot.now_playing = bot.playlist.popleft()
    track_data = bot.track_info.get(bot.now_playing, {})
    
    try:
        if guild.id in bot.music_messages:
            try:
                message = bot.music_messages[guild.id]
                view = MusicView()
                embed = await view.create_music_embed()
                await message.edit(embed=embed, view=view)
            except Exception as e:
                logger.error(f"Error updating panel: {e}")

        if 'original_url' in track_data:
            source = discord.FFmpegOpusAudio(track_data['url'], **FFMPEG_OPTIONS)
        else:
            loop = asyncio.get_event_loop()
            filepath = await loop.run_in_executor(None, create_tts_audio, bot.now_playing)
            source = discord.FFmpegPCMAudio(filepath)
        
        def after_playing(error):
            if error:
                logger.error(f"Playback error: {error}")
            
            if 'original_url' not in track_data and hasattr(source, 'name'):
                try:
                    if os.path.exists(source.name):
                        os.remove(source.name)
                except Exception as e:
                    logger.error(f"Error deleting audio file: {e}")
            
            bot.last_played = bot.now_playing
            bot.now_playing = None
            
            if bot.loop_enabled and bot.last_played:
                bot.playlist.append(bot.last_played)
            
            asyncio.run_coroutine_threadsafe(play_next(guild), bot.loop)
        
        voice_client.play(source, after=after_playing)
        
    except Exception as e:
        logger.error(f"Music playback error: {e}")
        asyncio.run_coroutine_threadsafe(play_next(guild), bot.loop)

# Клас музичної панелі
class MusicView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    
    async def update_panel(self, interaction):
        embed = await self.create_music_embed()
        try:
            if isinstance(interaction, discord.Interaction):
                if interaction.response.is_done():
                    await interaction.edit_original_response(embed=embed, view=self)
                else:
                    await interaction.response.edit_message(embed=embed, view=self)
            else:
                await interaction.edit(embed=embed, view=self)
        except Exception as e:
            logger.error(f"Panel update error: {e}")
            try:
                await interaction.followup.send("Failed to update panel", ephemeral=True)
            except:
                pass

    async def create_music_embed(self):
        embed = discord.Embed(
            title="🎶 MUSIC PANEL 🎶",
            description="Music playback control",
            color=discord.Color.blurple()
        )
        
        embed.set_image(url="https://i.postimg.cc/htbx35Ms/834-20250522114453.png")

        if bot.now_playing:
            track_data = bot.track_info.get(bot.now_playing, {})
            duration = track_data.get('duration', 'Unknown')
            
            embed.add_field(
                name="🔊 NOW PLAYING",
                value=f"**{bot.now_playing}** {duration}",
                inline=False
            )
        
        if bot.playlist:
            playlist_text = ""
            for i, track in enumerate(bot.playlist):
                track_data = bot.track_info.get(track, {})
                duration = track_data.get('duration', 'Unknown')
                
                playlist_text += f"{i+1}. **{track}** {duration}\n"
            
            embed.add_field(
                name="📜 PLAYLIST",
                value=playlist_text[:1000] + ("..." if len(playlist_text) > 1000 else ""),
                inline=False
            )
        else:
            embed.add_field(
                name="📜 PLAYLIST",
                value="Playlist is empty",
                inline=False
            )
        
        status = "🔁 ENABLED" if bot.loop_enabled else "🔁 DISABLED"
        embed.add_field(
            name="⚙️ SETTINGS",
            value=f"{status}",
            inline=True
        )
        
        embed.set_footer(
            text=f"🕒 Last update: {datetime.now().strftime('%H:%M:%S')} | 🎵 Total tracks: {len(bot.playlist)}"
        )
        
        return embed

    @ui.button(label="🎵 Add track", style=discord.ButtonStyle.green)
    async def add_track(self, interaction: discord.Interaction, button: ui.Button):
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message(
                "You must be in a voice channel to add tracks",
                ephemeral=True
            )
            return
        
        modal = AddTrackModal()
        await interaction.response.send_modal(modal)
        await modal.wait()
        
        try:
            message = await interaction.original_response()
            await self.update_panel(message)
        except:
            pass
    
    @ui.button(label="⏸️ Pause", style=discord.ButtonStyle.blurple)
    async def pause(self, interaction: discord.Interaction, button: ui.Button):
        voice_client = interaction.guild.voice_client
        if not voice_client:
            await interaction.response.send_message(
                "Bot is not connected to a voice channel",
                ephemeral=True
            )
            return
        
        if voice_client.is_playing():
            voice_client.pause()
            button.label = "▶️ Resume"
        elif voice_client.is_paused():
            voice_client.resume()
            button.label = "⏸️ Pause"
        
        await self.update_panel(interaction)
    
    @ui.button(label="⏮️ Previous", style=discord.ButtonStyle.blurple)
    async def previous(self, interaction: discord.Interaction, button: ui.Button):
        if not bot.last_played:
            await interaction.response.send_message(
                "No previous track",
                ephemeral=True
            )
            return
        
        voice_client = interaction.guild.voice_client
        if voice_client:
            if voice_client.is_playing():
                voice_client.stop()
            bot.playlist.appendleft(bot.last_played)
            await play_next(interaction.guild)
        
        await self.update_panel(interaction)
    
    @ui.button(label="⏭️ Skip", style=discord.ButtonStyle.blurple)
    async def skip(self, interaction: discord.Interaction, button: ui.Button):
        voice_client = interaction.guild.voice_client
        if voice_client and (voice_client.is_playing() or voice_client.is_paused()):
            voice_client.stop()
            await interaction.response.send_message("Skipping current track", ephemeral=True)
        else:
            await interaction.response.send_message("Nothing is playing", ephemeral=True)
        
        await self.update_panel(interaction)
    
    @ui.button(label="🗑️ Remove last", style=discord.ButtonStyle.red)
    async def remove_last(self, interaction: discord.Interaction, button: ui.Button):
        if not bot.playlist:
            await interaction.response.send_message(
                "Queue is empty",
                ephemeral=True
            )
            return
        
        removed_track = bot.playlist.pop()
        if removed_track in bot.track_info:
            del bot.track_info[removed_track]
        
        await self.update_panel(interaction)
    
    @ui.button(label="🧹 Clear queue", style=discord.ButtonStyle.red)
    async def clear_queue(self, interaction: discord.Interaction, button: ui.Button):
        bot.playlist.clear()
        bot.track_info.clear()
        voice_client = interaction.guild.voice_client
        if voice_client and voice_client.is_playing():
            voice_client.stop()
        await self.update_panel(interaction)
    
    @ui.button(label="🔁 Loop", style=discord.ButtonStyle.green)
    async def loop(self, interaction: discord.Interaction, button: ui.Button):
        bot.loop_enabled = not bot.loop_enabled
        if bot.loop_enabled:
            button.style = discord.ButtonStyle.red
            button.label = "🔁 Looped"
        else:
            button.style = discord.ButtonStyle.green
            button.label = "🔁 Loop"
        await self.update_panel(interaction)
    
    @ui.button(label="🔄 Refresh", style=discord.ButtonStyle.grey)
    async def refresh(self, interaction: discord.Interaction, button: ui.Button):
        await self.update_panel(interaction)

# Вікно для додавання треків
class AddTrackModal(ui.Modal, title="Add track to playlist"):
    track_name = ui.TextInput(
        label="Track name or URL (YouTube/SoundCloud)",
        style=discord.TextStyle.long,
        placeholder="Enter track name or YouTube/SoundCloud link",
        max_length=200
    )

    async def on_submit(self, interaction: discord.Interaction):
        track = str(self.track_name)
        
        bot.playlist.append(track)
        
        if 'http' in track:
            track_info = await extract_audio_info(track)
            if track_info:
                bot.track_info[track_info['title']] = track_info
                bot.playlist.remove(track)
                bot.playlist.append(track_info['title'])
                track = track_info['title']
        
        try:
            await interaction.response.defer()
        except discord.errors.NotFound:
            pass
        
        voice_client = interaction.guild.voice_client
        if voice_client and not (voice_client.is_playing() or voice_client.is_paused()):
            await play_next(interaction.guild)
        
        try:
            await interaction.followup.send(
                f"Track added to playlist: **{track}**",
                ephemeral=True
            )
        except:
            pass

# Функції для відображення панелі
async def show_music_panel(interaction: discord.Interaction):
    view = MusicView()
    embed = await view.create_music_embed()
    
    if interaction.guild.id in bot.music_messages:
        try:
            old_msg = bot.music_messages[interaction.guild.id]
            await old_msg.delete()
        except:
            pass
    
    if interaction.response.is_done():
        message = await interaction.followup.send(embed=embed, view=view)
    else:
        await interaction.response.send_message(embed=embed, view=view)
        message = await interaction.original_response()
    
    bot.music_messages[interaction.guild.id] = message

# Слеш команди
@bot.tree.command(name="music", description="Show music control panel")
async def music(interaction: discord.Interaction):
    await show_music_panel(interaction)

@bot.tree.command(name="join", description="Join voice channel")
async def join(interaction: discord.Interaction):
    if not interaction.user.voice:
        embed = discord.Embed(
            title="❌ Error",
            description="You are not in a voice channel!",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    channel = interaction.user.voice.channel
    voice_client = interaction.guild.voice_client
    
    if voice_client and voice_client.is_connected():
        if voice_client.channel == channel:
            embed = discord.Embed(
                title="ℹ️ Information",
                description="I'm already connected to this channel",
                color=discord.Color.blue()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        await voice_client.move_to(channel)
    else:
        voice_client = await channel.connect()
    
    if bot.playlist and not (voice_client.is_playing() or voice_client.is_paused()):
        await play_next(interaction.guild)
    
    embed = discord.Embed(
        title="✅ Success",
        description=f"Connected to {channel.mention}",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="leave", description="Leave voice channel")
async def leave(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    
    if not voice_client or not voice_client.is_connected():
        embed = discord.Embed(
            title="❌ Error",
            description="I'm not connected to a voice channel",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    bot.playlist.clear()
    bot.track_info.clear()
    if voice_client.is_playing() or voice_client.is_paused():
        voice_client.stop()
    
    await voice_client.disconnect()
    embed = discord.Embed(
        title="✅ Success",
        description="Disconnected from voice channel",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="toggle_delete_tts", description="Toggle TTS message deletion")
async def toggle_delete_tts(interaction: discord.Interaction):
    bot.tts_settings['delete_after'] = not bot.tts_settings['delete_after']
    status = "enabled" if bot.tts_settings['delete_after'] else "disabled"
    bot.save_settings()
    
    embed = discord.Embed(
        title="✅ Settings updated",
        description=f"TTS message deletion: {status}",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="help", description="Show command help")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📘 Command help",
        description="List of available bot commands:",
        color=discord.Color.blurple()
    )
    
    embed.add_field(
        name="🔊 Voice commands",
        value="• /join - Join channel\n• /leave - Leave channel",
        inline=False
    )
    
    embed.add_field(
        name="🎶 Music",
        value="• /music - Music control panel",
        inline=False
    )
    
    embed.add_field(
        name="🗣️ TTS",
        value="• /tts_toggle - Toggle TTS\n• /set_tts_channel - Set TTS channel\n• /toggle_delete_tts - Delete messages after TTS",
        inline=False
    )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="tts_toggle", description="Toggle TTS")
async def tts_toggle(interaction: discord.Interaction):
    bot.tts_settings['enabled'] = not bot.tts_settings['enabled']
    status = "enabled" if bot.tts_settings['enabled'] else "disabled"
    bot.save_settings()
    
    embed = discord.Embed(
        title="🔊 TTS status",
        description=f"TTS {status}",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="set_tts_channel", description="Set TTS channel")
@app_commands.describe(channel="Text channel for TTS")
async def set_tts_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    bot.tts_settings['channel_id'] = channel.id
    bot.save_settings()
    
    embed = discord.Embed(
        title="✅ Settings saved",
        description=f"TTS channel set to: {channel.mention}",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

# Обробники подій
@bot.event
async def on_ready():
    logger.info(f'Bot {bot.user} is ready!')
    await cleanup_temp_files()
    
    try:
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} commands")
    except Exception as e:
        logger.error(f"Command sync error: {e}")

@bot.event
async def on_message(message):
    if message.author == bot.user or not bot.tts_settings['enabled']:
        return

    if message.channel.id == bot.tts_settings['channel_id']:
        try:
            await log_message(message)
            await process_tts_message(message)
        except Exception as e:
            logger.error(f"TTS processing error: {e}")

async def process_tts_message(message):
    voice_client = message.guild.voice_client
    
    if not voice_client or not voice_client.is_connected():
        logger.warning("Bot not connected to voice channel")
        return
    
    try:
        loop = asyncio.get_event_loop()
        filepath = await loop.run_in_executor(None, create_tts_audio, message.content)
        
        audio_source = discord.FFmpegPCMAudio(filepath)
        voice_client.play(audio_source)
        
        while voice_client.is_playing():
            await asyncio.sleep(0.1)
            
    except Exception as e:
        logger.error(f"TTS playback error: {e}")
    finally:
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception as e:
            logger.error(f"Error deleting TTS file: {e}")
            
    if bot.tts_settings['delete_after']:
        try:
            await message.delete()
        except Exception as e:
            logger.error(f"Error deleting message: {e}")

# Запуск
if __name__ == "__main__":
    if not TOKEN:
        logger.error("Bot token not found!")
        exit(1)
    
    try:
        bot.run(TOKEN)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot startup error: {e}")
    finally:
        asyncio.run(cleanup_temp_files())