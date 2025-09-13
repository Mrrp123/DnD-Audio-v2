import os

audioplayer_thread_lock = None
audioplayer_thread = None
audioplayer = None

app_folder = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
music_database_path = f"{app_folder}/music_data.db"
default_font_name = f"{app_folder}/assets/fonts/noto-sans-jp-japanese-600-normal.ttf"