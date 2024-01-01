from tools.audioplayer import AudioPlayer
from tools.audioprocessing import *
import tools.common_vars as common_vars
from threading import Thread, Lock

def start_audio_player():
    common_vars.audioplayer.run()


common_vars.audioplayer_thread_lock = Lock()
common_vars.audioplayer_thread = Thread(target=start_audio_player, daemon=True)
common_vars.audioplayer = AudioPlayer(lock=common_vars.audioplayer_thread_lock, rate=44_100)