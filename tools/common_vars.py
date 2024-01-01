import os

audioplayer_thread_lock = None
audioplayer_thread = None
audioplayer = None

app_folder = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))