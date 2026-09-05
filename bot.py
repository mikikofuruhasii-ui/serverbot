import discord
from discord.ext import commands, tasks
import yaml
import os
import socket
import subprocess
import json
import aiohttp
import asyncio

CONFIG_PATH = "config.yml"

# 設定ファイルの読み込みと自動生成
def load_config():
    if not os.path.exists(CONFIG_PATH):
        default_config = {
            "bot": {"token": "YOUR_BOT_TOKEN_HERE"},
            "minecraft": {
                "ip": "127.0.0.1",
                "port": 25565,
                "server_dir": "C:/path/to/peparserver",
                "start_command": "start.cmd",
                "log_file_path": "logs/latest.log"
            },
            "console": {
                "enable": False,
                "channel_id": 123456789012345678
            }
        }
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(default_config, f, allow_unicode=True)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

config = load_config()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)

# TCP Pingによるオンラインチェック
def check_minecraft_server(ip, port):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        result = sock.connect_ex((ip, port))
        sock.close()
        return result == 0
    except:
        return False

# --------------------------------------------------
# タスク①：ステータス更新 (Online / Offline / Ping)
# --------------------------------------------------
@tasks.loop(seconds=30)
async def update_status():
    ip = config["minecraft"].get("ip", "127.0.0.1")
    port = int(config["minecraft"].get("port", 25565))
    
    is_online = check_minecraft_server(ip, port)
    status_emoji = "🟢" if is_online else "🔴"
    ping = round(bot.latency * 1000)
    
    status_text = f"Server: {'Online' if is_online else 'Offline'} {status_emoji} | Ping: {ping}ms"
    await bot.change_presence(activity=discord.Game(name=status_text))

@update_status.before_loop
async def before_update_status():
    await bot.wait_until_ready()

# --------------------------------------------------
# タスク②：サーバーログ(latest.log)をDiscordに転送
# --------------------------------------------------
last_log_pos = 0

@tasks.loop(seconds=5)
async def tail_server_log():
    global last_log_pos
    if not config["console"].get("enable", False):
        return

    channel_id = config["console"].get("channel_id")
    if not channel_id:
        return
        
    channel = bot.get_channel(int(channel_id))
    if not channel:
        return

    server_dir = config["minecraft"].get("server_dir", "")
    log_path = os.path.join(server_dir, config["minecraft"].get("log_file_path", "logs/latest.log"))

    if not os.path.exists(log_path):
        return

    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            f.seek(last_log_pos)
            new_lines = f.readlines()
            last_log_pos = f.tell()

            if new_lines:
                text_chunk = "".join(new_lines[-20:]) # 最新20行に制限して送信
                if text_chunk.strip():
                    await channel.send(f"```text\n{text_chunk[:1900]}\n```")
    except Exception as e:
        print(f"Log read error: {e}")

@tail_server_log.before_loop
async def before_tail_log():
    global last_log_pos
    await bot.wait_until_ready()
    # 起動時はファイルの末尾にシークしておく
    server_dir = config["minecraft"].get("server_dir", "")
    log_path = os.path.join(server_dir, config["minecraft"].get("log_file_path", "logs/latest.log"))
    if os.path.exists(log_path):
        last_log_pos = os.path.getsize(log_path)

# --------------------------------------------------
# UI: 初期設定・設定変更用モーダル
# --------------------------------------------------
class ServerSetupModal(discord.ui.Modal, title="マイクラサーバー設定"):
    ip_input = discord.ui.TextInput(label="サーバーIPアドレス / ドメイン", placeholder="127.0.0.1", required=True)
    dir_input = discord.ui.TextInput(label="サーバーフォルダのパス", placeholder="C:/path/to/peparserver", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        config["minecraft"]["ip"] = self.ip_input.value.strip()
        config["minecraft"]["server_dir"] = self.dir_input.value.strip()

        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True)

        await interaction.response.send_message(
            f"✅ 設定を保存しました！\n• IP: `{config['minecraft']['ip']}`\n• パス: `{config['minecraft']['server_dir']}`",
            ephemeral=True
        )

# UI: サーバー管理パネル
class ServerPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="⚙️ 設定変更", style=discord.ButtonStyle.primary)
    async def config_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ServerSetupModal())

    @discord.ui.button(label="🚀 起動 (start.cmd)", style=discord.ButtonStyle.success)
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        server_dir = config["minecraft"]["server_dir"]
        start_cmd = config["minecraft"]["start_command"]
        
        if not os.path.exists(server_dir):
            await interaction.response.send_message("⚠️ 設定されたサーバーフォルダが見つかりません。", ephemeral=True)
            return

        await interaction.response.send_message("🚀 マイクラサーバーを起動しています...", ephemeral=True)
        try:
            subprocess.Popen(f'start cmd /k "{start_cmd}"', cwd=server_dir, shell=True)
        except Exception as e:
            await interaction.followup.send(f"❌ エラー: {e}", ephemeral=True)

# --------------------------------------------------
# イベント & コマンド定義
# --------------------------------------------------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    if not update_status.is_running(): update_status.start()
    if not tail_server_log.is_running(): tail_server_log.start()

# チャットで "start" と打つと起動
@bot.event
async def on_message(message):
    if message.author.bot: return
    
    if message.content.strip().lower() == "start":
        server_dir = config["minecraft"]["server_dir"]
        start_cmd = config["minecraft"]["start_command"]
        if os.path.exists(server_dir):
            await message.channel.send("🚀 チャットを検知しました。サーバーを起動します...")
            subprocess.Popen(f'start cmd /k "{start_cmd}"', cwd=server_dir, shell=True)
        else:
            await message.channel.send("⚠️ フォルダパスが正しくありません。`/setup` で設定してください。")

    await bot.process_commands(message)

# コマンド: /serverpanel
@bot.command(name="serverpanel")
async def serverpanel(ctx):
    await ctx.send("🎛️ **サーバー管理パネル**\nボタンから設定や起動を行えます。", view=ServerPanelView())

# コマンド: /setup (UIモーダルを呼ぶ)
@bot.command(name="setup")
async def setup_cmd(ctx):
    view = discord.ui.View()
    btn = discord.ui.Button(label="⚙️ 設定画面を開く", style=discord.ButtonStyle.success)
    async def callback(interaction): await interaction.response.send_modal(ServerSetupModal())
    btn.callback = callback
    view.add_item(btn)
    await ctx.send("以下のボタンからIPやパスを設定してください。", view=view)

# コマンド: /whitelistadd <名前>
@bot.command(name="whitelistadd")
async def whitelistadd(ctx, player_name: str):
    server_dir = config["minecraft"].get("server_dir", "")
    whitelist_path = os.path.join(server_dir, "whitelist.json")

    await ctx.send(f"🔍 `{player_name}` のUUIDをMojangから検索中...")
    async with aiohttp.ClientSession() as session:
        async with session.get(f"https://api.mojang.com/users/profiles/minecraft/{player_name}") as resp:
            if resp.status != 200:
                return await ctx.send("❌ プレイヤーが見つかりませんでした。")
            data = await resp.json()
            uuid, actual_name = data.get("id"), data.get("name")
            fmt_uuid = f"{uuid[0:8]}-{uuid[8:12]}-{uuid[12:16]}-{uuid[16:20]}-{uuid[20:]}"

    wl_data = []
    if os.path.exists(whitelist_path):
        with open(whitelist_path, "r", encoding="utf-8") as f:
            wl_data = json.load(f)

    for entry in wl_data:
        if entry.get("name", "").lower() == actual_name.lower():
            return await ctx.send(f"⚠️ `{actual_name}` は既に登録されています。")

    wl_data.append({"uuid": fmt_uuid, "name": actual_name})
    with open(whitelist_path, "w", encoding="utf-8") as f:
        json.dump(wl_data, f, indent=4)
        
    await ctx.send(f"✅ **`{actual_name}`** をホワイトリストに追加しました！")

# コマンド: /list (画像でステータス表示)
@bot.command(name="list")
async def server_list(ctx):
    ip = config["minecraft"].get("ip", "127.0.0.1")
    port = config["minecraft"].get("port", 25565)
    
    # 外部APIを使ってサーバー状態を画像化
    image_url = f"https://api.mcsrvstat.us/image/{ip}:{port}"
    embed = discord.Embed(title="🌐 サーバー情報 & プレイヤーリスト", color=discord.Color.blurple())
    embed.set_image(url=image_url)
    
    await ctx.send(embed=embed)

# Render環境変数対応のトークン読み込み
TOKEN = os.environ.get("DISCORD_TOKEN") or config["bot"]["token"]
bot.run(TOKEN)
