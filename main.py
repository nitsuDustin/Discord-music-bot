import discord
from discord.ext import commands
import os
import asyncio
import yt_dlp
from googleapiclient.discovery import build
from dotenv import load_dotenv
import logging
from collections import deque
from keep_alive import keep_alive

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("youtube_api.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('youtube_api')

discord.opus.load_opus('libopus.so.0')

load_dotenv()

DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
YOUTUBE_API_KEY = os.getenv('YOUTUBE_API_KEY')

# Create YouTube API client with logging
try:
    youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)
    logger.info("YouTube API client initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize YouTube API client: {str(e)}")

intent = discord.Intents.default()
intent.message_content = True

bot = commands.Bot(command_prefix='!', intents=intent)

@bot.event
async def on_ready():
    print('Bot is ready')

# YouTube Search
def search_youtube(query):
    try:
        logger.info(f"YouTube API search request: query='{query}'")
        youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
        req = youtube.search().list(q=query, part="snippet", type="video", maxResults=1)
        res = req.execute()
        
        if not res.get('items'):
            logger.warning(f"YouTube API search returned no results for query: '{query}'")
            return None, "No results found"
            
        video_id = res['items'][0]['id']['videoId']
        title = res['items'][0]['snippet']['title']
        logger.info(f"YouTube API search success: found video '{title}' (ID: {video_id})")
        return f"https://www.youtube.com/watch?v={video_id}", title
    except Exception as e:
        logger.error(f"YouTube API search error for query '{query}': {str(e)}")
        return None, f"Error: {str(e)}"

ytdl_format_options = {
    'format': 'bestaudio/best',
    'quiet': True,
    'noplaylist': True,
    'extract_flat': False
}
ffmpeg_options = {
    'options': '-vn'
}
ytdl = yt_dlp.YoutubeDL(ytdl_format_options)

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.25):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=True):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
        if 'entries' in data:
            data = data['entries'][0]
        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)

# Join voice channel
@bot.command()
async def join(ctx):
    if ctx.author.voice:
        await ctx.author.voice.channel.connect()
    else:
        await ctx.send("You're not in a voice channel!")

# Leave voice channel
@bot.command()
async def leave(ctx):
    if ctx.voice_client:
        await ctx.voice_client.disconnect()


# Song queue for each server
queues = {}

@bot.command()
async def play(ctx, *, query):
    # Check if bot is in a voice channel
    if not ctx.voice_client:
        if ctx.author.voice:
            await ctx.author.voice.channel.connect()
        else:
            await ctx.send("You're not in a voice channel!")
            return
    
    logger.info(f"Play command received from {ctx.author} with query: '{query}'")
    url, title = search_youtube(query)
    if not url:
        await ctx.send(title)  # Send error message
        return
    
    # Get the guild id
    guild_id = ctx.guild.id
    
    # Create a queue for this guild if it doesn't exist
    if guild_id not in queues:
        queues[guild_id] = deque()
        
    # Add the song to the queue
    queues[guild_id].append((url, title))
    
    # If nothing is playing, start playing
    if not ctx.voice_client.is_playing():
        await play_next(ctx)
    else:
        await ctx.send(f"🎵 Added to queue: **{title}**")

# Add this to track currently playing songs
currently_playing = {}

# Modify the play_next function to update currently_playing
async def play_next(ctx):
    guild_id = ctx.guild.id
    
    if guild_id in queues and queues[guild_id]:
        # Get the next song from the queue
        url, title = queues[guild_id].popleft()
        
        # Update currently playing
        currently_playing[guild_id] = (url, title)
        
        async with ctx.typing():
            player = await YTDLSource.from_url(url, loop=bot.loop, stream=True)
            ctx.voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(
                play_next(ctx), bot.loop).result() if not e else print(f'Error: {e}'))
        
        await ctx.send(f"🎶 Now playing: **{title}**")
        logger.info(f"Now playing '{title}' requested by {ctx.author}")
    else:
        # Queue is empty
        if guild_id in queues:
            del queues[guild_id]
        if guild_id in currently_playing:
            del currently_playing[guild_id]

@bot.command()
async def nowplaying(ctx):
    """Shows the currently playing song"""
    guild_id = ctx.guild.id
    
    if guild_id in currently_playing:
        url, title = currently_playing[guild_id]
        await ctx.send(f"🎵 Now playing: **{title}**\n{url}")
    else:
        await ctx.send("Nothing is playing right now!")

# Add a command to show the current queue
@bot.command()
async def queue(ctx):
    guild_id = ctx.guild.id
    
    if guild_id not in queues or not queues[guild_id]:
        await ctx.send("The queue is empty!")
        return
    
    # Create a message with all songs in the queue
    queue_list = "\n".join([f"{i+1}. {title}" for i, (_, title) in enumerate(queues[guild_id])])
    await ctx.send(f"**Current Queue:**\n{queue_list}")

# Add a skip command
@bot.command()
async def skip(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send("⏭️ Skipped to the next song!")
        await play_next(ctx)
    else:
        await ctx.send("Nothing is playing right now!")

# Pause/resume/stop (optional)
@bot.command()
async def pause(ctx):
    if ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("⏸️ Paused!")

@bot.command()
async def resume(ctx):
    if ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send("▶️ Resumed!")

@bot.command()
async def stop(ctx):
    if ctx.voice_client:
        ctx.voice_client.stop()
        await ctx.send("⏹️ Stopped!")

@bot.command()
async def volume(ctx, volume: int):
    """Changes the player's volume"""
    
    if not ctx.voice_client:
        return await ctx.send("Not connected to a voice channel.")
    
    if volume < 0 or volume > 100:
        return await ctx.send("Volume must be between 0 and 100.")
    
    ctx.voice_client.source.volume = volume / 100
    await ctx.send(f"Volume set to {volume}%")

@bot.event
async def on_command_error(ctx, error):
    """Handles command errors"""
    if isinstance(error, commands.CommandNotFound):
        await ctx.send("Command not found. Use `!help` to see available commands.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Missing required argument. Use `!help` to see command usage.")
    else:
        logger.error(f"Command error: {str(error)}")
        await ctx.send(f"An error occurred: {str(error)}")

keep_alive()
bot.run(DISCORD_TOKEN)