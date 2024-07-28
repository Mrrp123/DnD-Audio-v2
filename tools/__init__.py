# from tools.audioplayer import AudioPlayer
# from tools.audioprocessing import *
# import tools.common_vars as common_vars
# from threading import Thread, Lock
# from glob import glob

# def start_audio_player():
#     common_vars.audioplayer.run()


# files = glob(f"{common_vars.app_folder}/audio/*.wav") + glob(f"{common_vars.app_folder}/audio/*.ogg")

# common_vars.audioplayer_thread_lock = Lock()
# common_vars.audioplayer_thread = Thread(target=start_audio_player, daemon=True)
# common_vars.audioplayer = AudioPlayer(lock=common_vars.audioplayer_thread_lock, default_song_file=files[0], rate=44_100)