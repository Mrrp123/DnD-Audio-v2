#from pydub import AudioSegment
from tools.audiosegment import AudioSegment
from tools.audioprocessing import *
import tools.common_vars as common_vars
import numpy as np
from math import ceil
import os
import wave
from glob import glob
from threading import Thread, Lock
import time
from scipy.signal import iirfilter
from kivy import platform
import pyogg
import ctypes


try:
    from audiostream import get_output, AudioSample
    AUDIO_API = "audiostream"
except ImportError:
    import pyaudio
    AUDIO_API = "pyaudio"



class MusicPlaylist():
    """
    Creates a playlist that keeps track of songs
    """

    def __init__(self, playlist) -> None:
        self.playlist = [{"file" : file, 
                          "song" : os.path.split(file)[-1].split(".")[0],
                          "cover" : self.get_song_cover(file)} 
                          for file in playlist]
 
        self._pos = 0 # position of the pointer
    
    def __getitem__(self, i):
        return self.playlist[i]
    
    def __len__(self):
        return len(self.playlist)
    
    def __lshift__(self, left):
        self._pos = (self._pos - (left % len(self))) % len(self)
        return self.playlist[self._pos]
        
    def __rshift__(self, right):
        self._pos = (self._pos + (right % len(self))) % len(self)
        return self.playlist[self._pos]
    
    @staticmethod
    def get_song_cover(file):
        asset_folder = f"{common_vars.app_folder}/assets/covers"
        name = os.path.split(file)[-1].split(".")[0]

        if os.path.isfile(f"{asset_folder}/{name}.png"):
            return f"{asset_folder}/{name}.png"
        
        elif os.path.isfile(f"{asset_folder}/{name}.jpg"):
            return f"{asset_folder}/{name}.jpg"
        
        else:
            return f"{asset_folder}/default_cover.png"

    def append(self, file, cover=None):
        self.playlist.append(
                        {"file" : file, 
                         "song" : os.path.split(file)[-1].split(".")[0],
                         "cover" : cover}
                          )
    
    def set_song(self, file):
        if file is not None:
            for i, data in enumerate(self.playlist):
                if file == data["file"]:
                    self._pos = i
                    return self[i]



class AudioPlayer():
    """
    Object that handles anything and everything related to playing audio and music.


    All possible statuses:

    idle          : Player is not running
    playing       : Player is playing music sequentially as normal
    paused        : Player is paused, no function can execute in this state
    stopped       : Player is stopped, all functions running the player will return, can't undo this action
    change_song   : A new song has been selected and the player will change to it
    skip          : The player will skip to the next song (no fading)
    transition    : The player will crossfade into the next song
    repeat        : The player will crossfade into the beginning of the current song
    seek          : The player will jump to a point in the song defined by a slider on the main window
    

    Known Bugs:
        None
    """

    def __init__(self, lock, channels=2, rate=44_100, buffersize=128, encoding=16) -> None:
        self.rate = rate
        self.pos = self.init_pos = 0
        self.frame_pos = 0
        self.seek_pos = 0
        self.lock = lock
        self.lock_status = False # Flag that prevents status value from being changed
        self.reverse_audio = False

        self._status = "idle"
        self._pause_flag = False
        self._speed = 1 # Speed at which the audio plays

        self.start = 0 # debug
        self.end = 0 # debug

        self.base_fade_duration = 3000 # Keep original value for when we change speeds and need to adjust this
        self.fade_duration = self.base_fade_duration
        self.chunk_len = 50 # sets a size for how large audio chunks are (ms)
        pyogg.pyoggSetStreamBufferSize(round(self.rate * encoding//8 * self.chunk_len / 1000))

        self.sos = None
        self.filter_state = (np.zeros(shape=(15, 2)), np.zeros(shape=(15, 2)))
        self.ease_filter = False

        self.app_folder = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

        if AUDIO_API == "audiostream":
            self.stream = get_output(channels=channels, rate=rate, buffersize=buffersize, encoding=encoding)
            self.sample = AudioSample()
            self.stream.add_sample(self.sample)
            self.sample.play()
        elif AUDIO_API == "pyaudio":
            p = pyaudio.PyAudio()
            self.stream = p.open(format=p.get_format_from_width(encoding//8),
                    channels=channels,
                    rate=rate,
                    output=True)
            self.sample = self.stream

        self.reload_songs()
        self.song_data = self.playlist[0]
        self.next_song_data = None
    
    @property
    def status(self):
        #with self.lock:
            return self._status
        
    
    @status.setter
    def status(self, new_status):
        #with self.lock:
            if not self.lock_status:
                self._status = new_status

    
    @property
    def pause_flag(self):
        with self.lock:
            return self._pause_flag
        
    
    @pause_flag.setter
    def pause_flag(self, value):
        value = bool(value) 
        with self.lock:
            self._pause_flag = value
    

    @property
    def speed(self):
        return self._speed
    
    @speed.setter
    def speed(self, value):
        if value < 1:
            self.sos = iirfilter(30, round(value*self.rate/2), fs=self.rate, btype="lowpass", analog=False, ftype="butter", output="sos")
            self.filter_state = (np.zeros(shape=(15, 2)), np.zeros(shape=(15, 2)))
        else:
            self.sos = None
        self.fade_duration = int(self.base_fade_duration * value)
        self._speed = value

    
    def reload_songs(self):
        """
        Updates the currently available audio files in the audio folder. Only looks for .wav files for now
        """
        # if platform == "win" or platform == "linux":
        loaded_songs = glob(f"{self.app_folder}/audio/*.wav") + glob(f"{self.app_folder}/audio/*.ogg")
        # elif platform == "android":
        #     loaded_songs = glob(f"sdcard/music/*.wav")
        self.playlist = MusicPlaylist(loaded_songs)



    
    def load_chunks(self, file, start_pos=0, end_pos=None, gain=0, chunk_size=None):
        """
        This function will load a portion of a song defined by self.chunk_len (set to 120ms).
        Chunks may be shorter than 120ms if at EOF and the song length is not a multiple of self.chunk_len

        file: PathLike: location where the audio file is stored

        start_pos: int: start reading at time = start_pos milliseconds, can be negative.
        If start_pos is negative, start reading at start_pos milliseconds away from end_pos if end_pos is not None
        or EOF if end_pos is None.

        end_pos: int or None: stop reading at time = end_pos milliseconds, if None, read until EOF

        gain: increase (or decrease) volume of outgoing audiosegment (in dB)
        """

        _, file_type = os.path.splitext(file)

        num_frames = total_frames = self.get_song_length(file)[1]
        self.next_song_length = (total_frames / self.rate)*1000
        self.next_total_frames = total_frames
        if self.bootup:
            self.song_length = self.next_song_length
            self.total_frames = self.next_total_frames
            if self.reverse_audio and not self.init_pos:
                self.pos = self.song_length
                self.frame_pos = self.total_frames

        if end_pos is not None and end_pos / 1000 * self.rate < num_frames:
            num_frames -= (num_frames - end_pos)
            end_frame = round(end_pos / 1000 * self.rate)

        if start_pos >= 0:
            start_frame = round(start_pos / 1000 * self.rate)
            if self.reverse_audio:
                num_frames -= (total_frames - start_frame)
            else:
                num_frames -= start_frame
        elif end_pos is None:
            start_frame = total_frames - 1
            
        # NEGATIVE VALUES NOT FULLY IMPLEMENTED
        # elif end_pos is None:
        #     start_frame = round(1)
        #     num_frames = round(-start_pos / 1000 * self.rate)
        # elif end_pos / 1000 * self.rate < num_frames:
        #     start_frame = end_frame - round(-start_pos / 1000 * self.rate)
        #     num_frames -= start_frame
            
            
        num_chunks = ceil(num_frames / (self.rate/1000*self.chunk_len))
        self.num_chunks = num_chunks
        chunk_frame_len = round(self.rate * (self.chunk_len / 1000))

        if file_type == ".wav":
            audio_generator = self._load_wav(file, start_frame, gain, num_chunks, chunk_frame_len)
        
        elif file_type == ".ogg":
            audio_generator = self._load_ogg(file, start_frame, gain, num_chunks, chunk_frame_len)

        for audio in audio_generator:
            yield audio


    def _load_wav(self, file, start_frame, gain, num_chunks, chunk_frame_len):

        reverse_audio = self.reverse_audio # set local variable in case we reverse audio during a transition

        with wave.open(file, "rb") as fp:

            for chunk_index in range(num_chunks):
                if reverse_audio:
                    frame_pos = -chunk_frame_len*(chunk_index+1) + start_frame
                    if frame_pos < 0:
                        chunk_frame_len += frame_pos # frame pos is negative here, so we're subtracting from chunk_frame_len
                        frame_pos = 0
                    fp.setpos(frame_pos)
                else:
                    fp.setpos(chunk_frame_len*chunk_index + start_frame)
                byte_data = fp.readframes(chunk_frame_len)
                audio = AudioSegment(data=byte_data, frame_rate=self.rate, channels=2, sample_width=2) + gain
                if reverse_audio:
                    yield audio.reverse()
                else:
                    yield audio
    
    def _load_ogg(self, file, start_frame, gain, num_chunks, chunk_frame_len):

        data_stream = pyogg.VorbisFileStream(file)
        
        if start_frame != 0:
            pyogg.vorbis.ov_pcm_seek(ctypes.byref(data_stream.vf), start_frame)

        byte_data = bytes([0])

        reverse_audio = self.reverse_audio # set local variable in case we reverse audio during a transition
        cut = False

        for chunk_index in range(num_chunks):
            if reverse_audio:
                frame_pos = -chunk_frame_len*(chunk_index+1) + start_frame
                if frame_pos < 0:
                    chunk_frame_len += frame_pos # frame pos is negative here, so we're subtracting from chunk_frame_len
                    cut = True # Set flag indicating that we have to cut out a part of the AudioSegment
                    frame_pos = 0
                pyogg.vorbis.ov_pcm_seek(ctypes.byref(data_stream.vf), frame_pos)

            byte_data, _ = data_stream.get_buffer()

            if byte_data is None:
                break

            audio = AudioSegment(data=byte_data, frame_rate=self.rate, channels=2, sample_width=2) + gain
            if reverse_audio:
                if cut:
                    yield audio[0:chunk_frame_len].reverse()
                else:
                    yield audio.reverse()
            else:
                yield audio


    

    def get_song_length(self, song):
        _, file_type = os.path.splitext(song)

        if file_type == ".wav":
            with wave.open(song, "rb") as fp:
                num_frames = fp.getnframes()

        elif file_type == ".ogg":
            with open(song, "rb") as fp:
                i = 0
                while True:
                    fp.seek(-4-i, 2)
                    data = fp.read(4)
                    if data == bytes([79, 103, 103, 83]):
                        fp.read(2)
                        num_frames = int.from_bytes(fp.read(8), "little")
                        break
                    i += 1

        return (num_frames / self.rate * 1000), num_frames

    
    def get_debug_info(self):
        self.end = time.time_ns()
        sep = "\\"
        if self.next_song_data is None:
            next_song_data = {"song" : None}
        else:
            next_song_data = self.next_song_data

        if self.end-self.start < 1_000_000:
            chunk_gen_time = "<1ms"
        else:
            chunk_gen_time = f"{(self.end-self.start)//1_000_000:.0f}ms"
            
        self.debug_string =  f"\nSong: {self.song_data['song']}\nNext Song: {next_song_data['song']}\n" +\
                        f"Song Pos: {self.pos/1000/self.speed:.3f}/{self.song_length/1000/self.speed:.3f}s " +\
                            f"({self.pos/1000:.3f}/{self.song_length/1000:.3f}s) | " +\
                            f"<{self.frame_pos:,}/{self.total_frames:,}> frames\n" +\
                        f"Seek Pos: {self.seek_pos/1000/self.speed:.3f}s\n" +\
                        f"Audio Player Speed: {self.speed}x, Fade Duration: {self.base_fade_duration//1000}s\n" +\
                        f"Audio Player Status: {self.status}\n" +\
                        f"Chunk Index: {self.chunk_index}/{self.num_chunks-1}, Chunk Length: {len(self.chunk)/self.speed:.3f}ms\n" +\
                        f"Audio chunk generation time: {chunk_gen_time}"
            #print(self.debug_string)


    
    def pause(self):
        """
        Pauses the audio player. Note: this will block the player from doing anything
        """
        if AUDIO_API == "audiostream":
            self.sample.stop()
        while self.pause_flag == True:
            time.sleep(0.1)
        if AUDIO_API == "audiostream":
            self.sample.play()
        return
    


    def skip(self, next_song_data):
        """
        Skips to another song, cutting out the crossfade entirely
        """
        del self.chunk_generator
        if self.reverse_audio:
            next_song_len, next_song_frame_len = self.get_song_length(next_song_data["file"])
            self.chunk_generator = self.load_chunks(next_song_data["file"], start_pos=next_song_len)
            self.pos = next_song_len
            self.frame_pos = next_song_frame_len
        else:
            self.chunk_generator = self.load_chunks(next_song_data["file"])
            self.pos = 0
            self.frame_pos = 0
        self.seek_pos = 0
        self.song_data = next_song_data
        self.next_song_data = None
        if AUDIO_API == "audiostream":
            self.sample.stop()
            self.sample.play()


    
    def seek(self, start_pos):
        """
        Seek to a certain position in the song
        """
        del self.chunk_generator
        start_pos = int(start_pos)
        self.chunk_generator = self.load_chunks(self.song_data["file"], start_pos=start_pos)
        self.pos = start_pos
        self.frame_pos = round(start_pos / 1000 * self.rate)
        if AUDIO_API == "audiostream":
            self.sample.stop()
            self.sample.play()





    def transition(self, next_song_data=None):
        """
        Handles the smooth crossfading between songs or the crossfading for when a song repeats
        Allows for the user to interuppt this process to skip, change songs (again), or to seek to some other point in the current song
        """

        def n_generator(audio_len, chunk_len=120, *args):
            """
            If we override the transition with another fade, combine all the generators that would have
            been used to play the music into a single object.
            """
            i = 0
            while i < ceil(audio_len / chunk_len):
                output_chunk = next(args[0])
                for audio_generator in args[1:]:
                    output_chunk *= next(audio_generator)
                yield output_chunk
                i += 1



        if next_song_data is None: # Repeat currently playing song
            next_song_data = self.song_data
            self.status = "repeat"

        else: # Transition to next song
            self.status = "transition"

        if self.reverse_audio:
            time_remaining = round(self.pos)
        else:
            time_remaining = round(self.song_length - self.pos) # Remaining time we have left in the song

        if time_remaining > self.fade_duration and self.status == "repeat": # If we aren't exactly fade_duration away from end, play last remaining bit of song

            chunk = next(self.chunk_generator)[0:time_remaining-self.fade_duration]
            self.write_to_buffer(chunk)
            if self.reverse_audio:
                self.pos -= len(chunk)
                self.frame_pos -= chunk.get_num_frames()
            else:
                self.pos += len(chunk)
                self.frame_pos += chunk.get_num_frames()
            self.chunk_generator = self.load_chunks(self.song_data["file"], start_pos=self.pos)
        
        next_song_len, next_song_frame_len = self.get_song_length(next_song_data["file"])

        if self.reverse_audio:
            next_chunk_generator = self.load_chunks(next_song_data["file"], start_pos=next_song_len)
        else:
            next_chunk_generator = self.load_chunks(next_song_data["file"])
        
        if time_remaining <= 0 and self.reverse_audio:
            self.seek(int(self.song_length))
            return
        elif time_remaining == 0:
            self.seek(0)
            return
        
        if next_song_len < min(self.fade_duration, time_remaining):
            fade_duration = int(next_song_len) - self.chunk_len
        else:
            fade_duration = min(self.fade_duration, time_remaining)
        
        #print(next_song_len)
        if self.reverse_audio:
            new_pos = next_song_len
            new_frame_pos = next_song_frame_len
        else:
            new_pos = 0
            new_frame_pos = 0
        for chunk, end_chunk_db, start_chunk_db in step_fade(self.chunk_generator, next_chunk_generator, 
                                                             fade_duration=fade_duration, chunk_len=self.chunk_len, fade_type="crossfade"): # Begin crossfade

            if self.status == "change_song": # If we decide to change songs during a transition, crossfade the (already crossfading) audio with the next song
                self.status = "override_transition"

                self.chunk_generator = n_generator(min(self.fade_duration, time_remaining), self.chunk_len, 
                                                   self.load_chunks(self.song_data["file"], start_pos=self.pos, gain=end_chunk_db), 
                                                   self.load_chunks(next_song_data["file"], start_pos=new_pos, gain=start_chunk_db))
                self.transition(self.next_song_data)
                return
            
            elif self.status == "skip": # If we change songs via a skip during a transition, just go to the next song
                if (self.reverse_audio and next_song_len - new_pos <= fade_duration/2) or (not self.reverse_audio and new_pos <= fade_duration/2):
                    self.skip(next_song_data)
                    self.playlist.set_song(next_song_data["file"]) # Resync playlist pointer, otherwise we will be one song ahead/behind
                elif self.next_song_data is not None:
                    self.skip(self.next_song_data)
                else:
                    self.skip(self.song_data)
                return
            
            elif self.status == "seek": # If we seek during a transition, override the transition and play the currently fading out song as normal
                self.seek(self.seek_pos)
                self.next_song_data = None
                return

            self.write_to_buffer(chunk)
            if self.reverse_audio:
                self.pos -= len(chunk)
                self.frame_pos -= chunk.get_num_frames()
                new_pos -= len(chunk)
                new_frame_pos -= chunk.get_num_frames()
            else:
                self.pos += len(chunk)
                self.frame_pos += chunk.get_num_frames()
                new_pos += len(chunk)
                new_frame_pos += chunk.get_num_frames()

        # Once we are done with crossfades, update various variables
        self.pos = new_pos
        self.frame_pos = new_frame_pos
        self.seek_pos = 0
        self.song_length = self.next_song_length
        self.total_frames = self.next_total_frames

        if next_song_data is not None:
            self.song_data = next_song_data
        self.next_song_data = None

        self.chunk_generator = self.load_chunks(next_song_data["file"], start_pos=self.pos)

        self.lock_status = True


    def write_to_buffer(self, audio: AudioSegment):
        """
        This will handle checking if the pause button has been pressed (which activates the pause flag), 
        writes the raw audio byte data to the audio buffer (type of which depends on our audio api), will 
        also deal with speeding up and slowing down the audio.
        """
        data = audio.data
        if self.speed != 1:
            data, self.filter_state = change_speed(data, self.speed, self.sos, zf=self.filter_state)
        self.get_debug_info()
        if self.pause_flag == True:
            self.pause()
        self.sample.write(data)
        self.start = time.time_ns()

        # with open("./audio/audioplayer_out.raw", "ab") as fp:
        #     fp.write(data)

    

    def zawarudo(self):
        """
        Is that a fucking JoJo reference???
        """
        speed_list = np.linspace(self.speed, 0, 15, endpoint=False)
        start_pos = self.pos
        data_len = 0

        #print(self.pos, self.pos-start_pos, frames_read, frames_read / 44.1)

        with wave.open(self.song_data["file"], "rb") as fp:

            with wave.open(f"{self.app_folder}/assets/audio/zawarudo.wav", "rb") as zwfp:

                fp.setpos(round(self.rate*self.pos / 1000))

                zw = zwfp.readframes(round(self.chunk_len*self.rate/1000))

                min_db = -5

                amp_list = np.linspace(1, db_to_amp(min_db), 5)
                
                db_list = [(amp_to_db(amp_list[index]), amp_to_db(amp_list[index+1])) for index in range(len(amp_list)-1)]

                i = 0
                while len(zw) != 0:
                    data = fp.readframes(round(self.chunk_len*self.rate/1000*self.speed))
                    data, _ = change_speed(data, self.speed)
                    zw_audio = AudioSegment(data=zw, frame_rate=self.rate, channels=2, sample_width=2)
                    if i < 4:
                        song_audio = AudioSegment(data=data, frame_rate=self.rate, channels=2, sample_width=2).fade(
                            from_gain=db_list[i][0], to_gain=db_list[i][1], start=0, duration=len(data)//4)

                    else:
                        song_audio = AudioSegment(data=data, frame_rate=self.rate, channels=2, sample_width=2) + min_db

                    if len(song_audio) == zw_audio:
                        self.sample.write((song_audio * zw_audio)._data)
                    else:
                        self.sample.write((song_audio[0:len(zw_audio)] * zw_audio)._data)

                    self.get_debug_info()
                    if self.reverse_audio:
                        self.pos -= len(zw_audio) * self.speed
                        self.frame_pos -= round(self.chunk_len*self.rate/1000)
                    else:
                        self.pos += len(zw_audio) * self.speed
                        self.frame_pos += round(self.chunk_len*self.rate/1000)
                    zw = zwfp.readframes(round(self.chunk_len*self.rate/1000))

                    i += 1
        
        with wave.open(self.song_data["file"], "rb") as fp:

            fp.setpos(round(self.rate*self.pos / 1000))

            with wave.open(f"{self.app_folder}/assets/audio/time_stop.wav", "rb") as zwfp:

                zwfp.setpos(21_563) # Set

                for i, speed in enumerate(speed_list):
                    chunk_len = speed * self.chunk_len
                    num_frames = round(self.rate*chunk_len / 1000)
                    data = fp.readframes(num_frames)

                    zw = zwfp.readframes(round(self.chunk_len*self.rate/1000))
                    
                    data, _ = change_speed(data, speed)
                    song_audio = AudioSegment(data=data, frame_rate=self.rate, channels=2, sample_width=2) + min_db
                    zw_audio = AudioSegment(data=zw, frame_rate=self.rate, channels=2, sample_width=2)

                    self.sample.write((song_audio * zw_audio)._data)
                    self.get_debug_info()
                    if self.reverse_audio:
                        self.pos -= chunk_len
                        self.frame_pos -= num_frames
                    else:
                        self.pos += chunk_len
                        self.frame_pos += num_frames
                
                zw = zwfp.readframes(round(self.chunk_len*self.rate/1000))
                while len(zw) != 0:
                    zw_audio = AudioSegment(data=zw, frame_rate=self.rate, channels=2, sample_width=2)
                    self.sample.write(zw_audio._data)
                    zw = zwfp.readframes(round(self.chunk_len*self.rate/1000))

            time.sleep(5)
            #self.pause()

            with wave.open(f"{self.app_folder}/assets/audio/time_resume.wav", "rb") as zwfp:

                speed_list = np.linspace(0.1, self.speed, 15, endpoint=True)

                for i, speed in enumerate(speed_list):
                    chunk_len = speed * self.chunk_len
                    num_frames = round(self.rate*chunk_len / 1000)

                    data = fp.readframes(num_frames)

                    zw = zwfp.readframes(round(self.chunk_len*self.rate/1000))

                    data, _ = change_speed(data, speed)
                    if i >= 11:
                        song_audio = AudioSegment(data=data, frame_rate=self.rate, channels=2, sample_width=2).fade(
                            from_gain=db_list[-(i%11)+1][0], to_gain=db_list[-(i%11)+2][1], start=0, duration=len(data)//4)
                    else:
                        song_audio = AudioSegment(data=data, frame_rate=self.rate, channels=2, sample_width=2) + min_db
                    zw_audio = AudioSegment(data=zw, frame_rate=self.rate, channels=2, sample_width=2)
                    
                    self.sample.write((song_audio * zw_audio)._data)
                    self.get_debug_info()
                    if self.reverse_audio:
                        self.pos -= chunk_len
                        self.frame_pos -= num_frames
                    else:
                        self.pos += chunk_len
                        self.frame_pos += num_frames

        
        

        self.chunk_generator = self.load_chunks(self.song_data["file"], start_pos=self.pos)

        self.status = "playing"



    
    def run(self):
        """
        Starts the audioplayer program, handles the mainloop of the audio player (playing, repeating and transitioning songs)
        """
        self.bootup = True
        if self.init_pos:
            start_pos = self.init_pos
            self.bootup = False
        elif self.reverse_audio:
            start_pos = -1
        else:
            start_pos = 0
        self.chunk_generator = None # initial value
        self.seek(start_pos)
        self.status = "playing"
        while self.status != "stopped":
        
            # Loads next block of audio
            for chunk_index, chunk in enumerate(self.chunk_generator):
                self.chunk = chunk
                self.chunk_index = chunk_index
                self.bootup = False
                self.song_length = self.next_song_length
                self.total_frames = self.next_total_frames

                self.write_to_buffer(self.chunk)
                if self.reverse_audio:
                    self.pos -= len(self.chunk)
                    self.frame_pos -= chunk.get_num_frames()
                else:
                    self.pos += len(self.chunk)
                    self.frame_pos += chunk.get_num_frames()
                # Check current status
                if self.status == "stopped":
                    break

                # If song needs to change or restart, handle fading/crossfading
                if (self.pos <= (self.fade_duration + self.chunk_len) or self.status == "change_song") and self.reverse_audio:
                    self.transition(self.next_song_data)
                    break

                elif (self.pos >= (self.song_length - self.fade_duration - self.chunk_len) or self.status == "change_song") and not self.reverse_audio:
                    self.transition(self.next_song_data) 
                    break

                elif self.status == "skip": # Check if we need to skip to another song
                    self.skip(self.next_song_data)
                    break

                elif self.status == "seek": # Check if we need to seek within current song
                    self.seek(self.seek_pos)
                    break

                elif self.status == "zawarudo":
                    self.zawarudo()
                    break

            if self.status != "stopped":
                self.lock_status = False
                self.status = "playing"
