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
import pyogg
import ctypes
from functools import reduce
import copy

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import ThreadingOSCUDPServer
from pythonosc.udp_client import SimpleUDPClient

from kivy.utils import platform

if platform == "android":
    from jnius import autoclass
    AUDIO_API = "audiotrack"

elif platform == "win":
    import pyaudio
    AUDIO_API = "pyaudio"

elif platform in ('linux', 'linux2', 'macos'):
    from audiostream import get_output, AudioSample
    AUDIO_API = "audiostream"



class AudioStreamer():
    """
    Handles pushing bytes to the speaker depending on which audio api / OS we are using.
    """

    def __init__(self, channels=2, rate=44_100, buffersize=256, encoding=16):

        self.channels = channels
        self.rate = rate
        self.buffersize = buffersize
        self.encoding = encoding

        if AUDIO_API == "audiotrack":
            self._android_init()
        
        elif AUDIO_API == "pyaudio":
            self._pyaudio_init()
        
        elif AUDIO_API == "audiostream":
            self._audiostream_init()
    

    def _android_init(self):
        AudioTrack = autoclass("android.media.AudioTrack")
        self.write_blocking = AudioTrack.WRITE_BLOCKING
        AudioManager = autoclass("android.media.AudioManager")
        AudioFormat = autoclass("android.media.AudioFormat")
        AudioFormatBuilder = autoclass("android.media.AudioFormat$Builder")
        AudioAttributes = autoclass("android.media.AudioAttributes")
        AudioAttributesBuilder = autoclass("android.media.AudioAttributes$Builder")

        if self.channels == 2:
            channel_mask = AudioFormat.CHANNEL_OUT_STEREO
        elif self.channels == 1:
            channel_mask = AudioFormat.CHANNEL_OUT_MONO
        
        if self.encoding == 16:
            encoding = AudioFormat.ENCODING_PCM_16BIT
        elif self.encoding == 8:
            encoding = AudioFormat.ENCODING_PCM_8BIT

        self.buffer_size = AudioTrack.getMinBufferSize(
            self.rate,
            channel_mask,
            encoding
        )
        
        self.audio_stream = AudioTrack(
            AudioAttributesBuilder().setUsage(AudioAttributes.USAGE_MEDIA).setContentType(AudioAttributes.CONTENT_TYPE_MUSIC).build(),
            AudioFormatBuilder().setChannelMask(channel_mask).setEncoding(encoding).setSampleRate(self.rate).build(),
            self.buffer_size,
            AudioTrack.MODE_STREAM,
            AudioManager.AUDIO_SESSION_ID_GENERATE
            )

        # Fill buffer with empty bytes before calling play
        self.audio_stream.write(bytes(self.buffer_size), 0, self.buffer_size)
        self.audio_stream.play()

        self.write = self._android_write


    def _pyaudio_init(self):
        p = pyaudio.PyAudio()
        self.stream = p.open(format=p.get_format_from_width(self.encoding//8),
                channels=self.channels,
                rate=self.rate,
                output=True)
        
        self.write = self._pyaudio_write
    

    def _audiostream_init(self):
        self.stream = get_output(channels=self.channels, rate=self.rate, buffersize=self.buffersize, encoding=self.encoding)
        self.sample = AudioSample()
        self.stream.add_sample(self.sample)
        self.sample.play()

        self.write = self._audiostream_write
    



    def write(self, data: bytes):
        pass

    def _pyaudio_write(self, data: bytes):
        self.stream.write(data)
    
    def _audiostream_write(self, data: bytes):
        self.sample.write(data)
    
    def _android_write(self, data: bytes):
        bytes_written = self.audio_stream.write(data, 0, len(data))

    
    def stop(self):
        """
        Only used for audiostream!!!
        """
        self.sample.stop()
    
    def play(self):
        """
        Only used for audiostream!!!
        """
        self.sample.play()





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
    zawarudo      : The player is running the zawarudo function -> time will stop
    

    Known Bugs:
        zawarudo WILL break when activated during a transition state (anything in the transitiion function)
    """

    def __init__(self, lock, default_song_file, channels=2, rate=44_100, buffersize=256, encoding=16) -> None:
        self.rate = rate
        self.pos = self.init_pos = 0
        self.frame_pos = 0
        self.seek_pos = 0
        self.lock = lock
        self.lock_status = False # Flag that prevents status value from being changed
        self.reverse_audio = False

        self._status = "idle"
        self._pause_flag = True
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

        self.volume = 1

        self.app_folder = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

        self.stream = AudioStreamer(channels, rate, buffersize, encoding)

        self.song_file = default_song_file#self.playlist[0]
        self.next_song_file = None

        # This will listen and respond to calls from the gui
        self.dispatcher = Dispatcher()
        self.dispatcher.map("/read", self.read)
        self.dispatcher.map("/write", self.write)
        self.dispatcher.map("/call", self.call_function)

        self.num_reads = 0
        self.start_read_time = time.time()

        self.osc_server = ThreadingOSCUDPServer(("127.0.0.1", 8001), self.dispatcher)
        self.osc_client = SimpleUDPClient("127.0.0.1", 8000)
        Thread(target=self.osc_server.serve_forever, daemon=True).start()
        self.osc_client.send_message("/return", "ready")
        
    

    def read(self, address: str, *args):
        """
        Function to get values in the audioplayer
        """
        try:
            self.osc_client.send_message("/return", [getattr(self, attr) for attr in args])
        except AttributeError:
            self.osc_client.send_message("/return", None)
    
    def write(self, address: str, attr: str, value):
        """
        Function to set values in the audioplayer
        """
        if address == "/write":
            setattr(self, attr, value)
    
    def call_function(self, address: str, func_name: str, *args):
        """
        This function forwards parameters to call functions and returns their values
        """
        func = reduce(getattr, func_name.split("."), self)
        self.osc_client.send_message("/return", func(*args))

    
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
            # Somehow, if start_frame is longer than the entire file, set start_frame to zero
            if start_frame > total_frames:
                start_frame = 0
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

        if self.song_file is not None:
            song_file = os.path.split(self.song_file)[-1].split('.')[0]
        else:
            song_file = None
        
        if self.next_song_file is not None:
            next_song_file = os.path.split(self.next_song_file)[-1].split('.')[0]
        else:
            next_song_file = None

        if self.end-self.start < 1_000_000:
            chunk_gen_time = "<1ms"
        else:
            chunk_gen_time = f"{(self.end-self.start)//1_000_000:.0f}ms"
            
        self.debug_string =  f"\nSong: {song_file}\nNext Song: {next_song_file}\n" +\
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
            self.stream.stop()
        while self.pause_flag == True:
            time.sleep(0.1)
        if AUDIO_API == "audiostream":
            self.stream.play()
        return
    


    def skip(self, next_song_file):
        """
        Skips to another song, cutting out the crossfade entirely
        """
        del self.chunk_generator
        if self.reverse_audio:
            next_song_len, next_song_frame_len = self.get_song_length(next_song_file)
            self.chunk_generator = self.load_chunks(next_song_file, start_pos=next_song_len)
            self.pos = next_song_len
            self.frame_pos = next_song_frame_len
        else:
            self.chunk_generator = self.load_chunks(next_song_file)
            self.pos = 0
            self.frame_pos = 0
        self.seek_pos = 0
        self.song_file = next_song_file
        self.next_song_file = None
        if AUDIO_API == "audiostream":
            self.stream.stop()
            self.stream.play()


    
    def seek(self, start_pos):
        """
        Seek to a certain position in the song
        """
        del self.chunk_generator
        start_pos = int(start_pos)
        self.chunk_generator = self.load_chunks(self.song_file, start_pos=start_pos)
        self.pos = start_pos
        self.frame_pos = round(start_pos / 1000 * self.rate)
        if AUDIO_API == "audiostream":
            self.stream.stop()
            self.stream.play()





    def transition(self, next_song_file=None):
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



        if next_song_file is None: # Repeat currently playing song
            next_song_file = self.song_file
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
            self.chunk_generator = self.load_chunks(self.song_file, start_pos=self.pos)
        
        next_song_len, next_song_frame_len = self.get_song_length(next_song_file)

        if self.reverse_audio:
            next_chunk_generator = self.load_chunks(next_song_file, start_pos=next_song_len)
        else:
            next_chunk_generator = self.load_chunks(next_song_file)
        
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
                                                   self.load_chunks(self.song_file, start_pos=self.pos, gain=end_chunk_db), 
                                                   self.load_chunks(next_song_file, start_pos=new_pos, gain=start_chunk_db))
                self.transition(self.next_song_file)
                return
            
            elif self.status == "skip": # If we change songs via a skip during a transition, just go to the next song
                if (self.reverse_audio and next_song_len - new_pos <= fade_duration/2) or (not self.reverse_audio and new_pos <= fade_duration/2):
                    self.skip(next_song_file)
                    #self.playlist.set_song(next_song_file) # Resync playlist pointer, otherwise we will be one song ahead/behind
                elif self.next_song_file is not None:
                    self.skip(self.next_song_file)
                else:
                    self.skip(self.song_file)
                return
            
            elif self.status == "seek": # If we seek during a transition, override the transition and play the currently fading out song as normal
                self.seek(self.seek_pos)
                self.next_song_file = None
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

        if next_song_file is not None:
            self.song_file = next_song_file
        self.next_song_file = None

        self.chunk_generator = self.load_chunks(next_song_file, start_pos=self.pos)

        self.lock_status = True


    def write_to_buffer(self, audio: AudioSegment):
        """
        This will handle checking if the pause button has been pressed (which activates the pause flag), 
        writes the raw audio byte data to the audio buffer (type of which depends on our audio api), will 
        also deal with speeding up and slowing down the audio.
        """
        if self.volume < 1:
            audio = audio + amp_to_db(self.volume)
        data = audio.data
        if self.speed != 1:
            data, self.filter_state = change_speed(data, self.speed, self.sos, zf=self.filter_state, dt=audio.dt)
        self.get_debug_info()
        if self.pause_flag == True:
            self.pause()
        self.stream.write(data)
        self.start = time.time_ns()

        # with open("./audio/audioplayer_out.raw", "ab") as fp:
        #     fp.write(data)

    

    def zawarudo(self):
        """
        Is that a fucking JoJo reference???
        """
        speed_list = np.linspace(self.speed, 0, round(1800 / self.chunk_len), endpoint=False)
        start_pos = self.pos
        data_len = 0

        #print(self.pos, self.pos-start_pos, frames_read, frames_read / 44.1)

        with wave.open(f"{self.app_folder}/assets/audio/zawarudo.wav", "rb") as zwfp:

            zw = zwfp.readframes(round(self.chunk_len*self.rate/1000))

            min_db = -5

            amp_list = np.linspace(1, db_to_amp(min_db), round(600 / self.chunk_len))
            
            db_list = [(amp_to_db(amp_list[index]), amp_to_db(amp_list[index+1])) for index in range(len(amp_list)-1)]

            extra_song_audio = AudioSegment(data=bytes(0), frame_rate=self.rate, channels=2, sample_width=2)

            i = 0
            while len(zw) != 0:

                data = next(self.chunk_generator).data

                #data = fp.readframes(round(self.chunk_len*self.rate/1000*self.speed))
                data, _ = change_speed(data, self.speed)
                zw_audio = AudioSegment(data=zw, frame_rate=self.rate, channels=2, sample_width=2) + + amp_to_db(self.volume)
                if i < len(db_list):
                    song_audio = AudioSegment(data=data, frame_rate=self.rate, channels=2, sample_width=2).fade(
                        from_gain=db_list[i][0], to_gain=db_list[i][1], start=0, duration=len(data)//4) + + amp_to_db(self.volume)

                else:
                    song_audio = AudioSegment(data=data, frame_rate=self.rate, channels=2, sample_width=2) + min_db + + amp_to_db(self.volume)


                if len(song_audio) == len(zw_audio):
                    self.stream.write((song_audio * zw_audio).data)
                else:
                    extra_song_audio = copy.copy(song_audio)[len(zw_audio):]
                    self.stream.write((song_audio[0:len(zw_audio)] * zw_audio).data)

                self.get_debug_info()
                if self.reverse_audio:
                    self.pos -= len(zw_audio) * self.speed
                    self.frame_pos -= round(self.chunk_len*self.rate/1000)
                else:
                    self.pos += len(zw_audio) * self.speed
                    self.frame_pos += round(self.chunk_len*self.rate/1000)
                zw = zwfp.readframes(round(self.chunk_len*self.rate/1000))

                i += 1

        with wave.open(f"{self.app_folder}/assets/audio/time_stop.wav", "rb") as zwfp:

            zwfp.setpos(21_563) # Set

            for i, speed in enumerate(speed_list):
                chunk_len = speed * self.chunk_len
                num_frames = round(self.rate*chunk_len / 1000)

                if len(extra_song_audio) < chunk_len:
                    audio_sum = extra_song_audio + next(self.chunk_generator)
                else:
                    audio_sum = extra_song_audio

                extra_song_audio = copy.copy(audio_sum)[chunk_len:]
                data = audio_sum[:chunk_len].data


                data, _ = change_speed(data, speed)

                zw = zwfp.readframes(round(self.chunk_len*self.rate/1000))
                
                
                song_audio = AudioSegment(data=data, frame_rate=self.rate, channels=2, sample_width=2) + (min_db + amp_to_db(self.volume))
                zw_audio = AudioSegment(data=zw, frame_rate=self.rate, channels=2, sample_width=2) + amp_to_db(self.volume)

                if i == 0:
                    self.osc_client.send_message("/call/main/main_display", "_start_time_effect")
                self.stream.write((song_audio * zw_audio).data)
                self.get_debug_info()
                if self.reverse_audio:
                    self.pos -= chunk_len
                    self.frame_pos -= num_frames
                else:
                    self.pos += chunk_len
                    self.frame_pos += num_frames
            
            zw = zwfp.readframes(round(self.chunk_len*self.rate/1000))
            while len(zw) != 0:
                zw_audio = AudioSegment(data=zw, frame_rate=self.rate, channels=2, sample_width=2) + amp_to_db(self.volume)
                self.stream.write(zw_audio.data)
                zw = zwfp.readframes(round(self.chunk_len*self.rate/1000))

        time.sleep(5)
        #self.pause()

        db_list.reverse()
        
        with wave.open(f"{self.app_folder}/assets/audio/time_resume.wav", "rb") as zwfp:

            speed_list = np.linspace(0.1, self.speed, round(1800 / self.chunk_len), endpoint=True)
            end_offset = (len(speed_list) - len(db_list))
            for i, speed in enumerate(speed_list):
                chunk_len = speed * self.chunk_len
                num_frames = round(self.rate*chunk_len / 1000)

                if len(extra_song_audio) < chunk_len:
                    audio_sum = extra_song_audio + next(self.chunk_generator)
                else:
                    audio_sum = extra_song_audio

                extra_song_audio = copy.copy(audio_sum)[chunk_len:]
                data = audio_sum[:chunk_len].data

                zw = zwfp.readframes(round(self.chunk_len*self.rate/1000))

                data, _ = change_speed(data, speed)
                if i >= end_offset:
                    song_audio = AudioSegment(data=data, frame_rate=self.rate, channels=2, sample_width=2).fade(
                        from_gain=db_list[i - end_offset][1], to_gain=db_list[i - end_offset][0], start=0, duration=len(data)//4) + amp_to_db(self.volume)
                else:
                    song_audio = AudioSegment(data=data, frame_rate=self.rate, channels=2, sample_width=2) + (min_db + amp_to_db(self.volume))
                zw_audio = AudioSegment(data=zw, frame_rate=self.rate, channels=2, sample_width=2) + amp_to_db(self.volume)
                self.stream.write((song_audio * zw_audio).data)
                self.get_debug_info()
                if self.reverse_audio:
                    self.pos -= chunk_len
                    self.frame_pos -= num_frames
                else:
                    self.pos += chunk_len
                    self.frame_pos += num_frames

        
        

        self.chunk_generator = self.load_chunks(self.song_file, start_pos=self.pos)

        self.status = "playing"

    
    def run(self):
        """
        Starts the audioplayer program, handles the mainloop of the audio player (playing, repeating and transitioning songs)
        """
        while self.status == "idle": # Wait for the program to start
            time.sleep(0.05)
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
                    self.transition(self.next_song_file)
                    break

                elif (self.pos >= (self.song_length - self.fade_duration - self.chunk_len) or self.status == "change_song") and not self.reverse_audio:
                    self.transition(self.next_song_file) 
                    break

                elif self.status == "skip": # Check if we need to skip to another song
                    self.skip(self.next_song_file)
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

if __name__ == "__main__" or __name__ == "<run_path>":
    songs = glob(f"{common_vars.app_folder}/audio/*.wav") + glob(f"{common_vars.app_folder}/audio/*.ogg")

    audioplayer = AudioPlayer(lock=Lock(), default_song_file=songs[0], rate=44_100)
    audio_thread = Thread(target=audioplayer.run, daemon=True)
    audio_thread.start()
    audio_thread.join()