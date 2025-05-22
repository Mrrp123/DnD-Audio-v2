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
from functools import reduce
import yaml

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import ThreadingOSCUDPServer
from pythonosc.udp_client import SimpleUDPClient

from kivy.utils import platform

if platform == "android":
    from jnius import autoclass
    AUDIO_API = "audiotrack"


if platform in ('win', 'linux', 'linux2', 'macos'):
    import miniaudio
    from miniaudio import lib, ffi, _get_filename_bytes
    AUDIO_API = "miniaudio"



class AudioStreamer():
    """
    Handles pushing bytes to the speaker depending on which audio api / OS we are using.
    """

    def __init__(self, channels=2, rate=44_100, buffersize_ms=50, encoding=16):

        self.channels = channels
        self.rate = rate
        self.buffersize_ms = buffersize_ms
        self.encoding = encoding

        if AUDIO_API == "audiotrack":
            self._android_init()
        

        elif AUDIO_API == "miniaudio":
            self._miniaudio_init()
    

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
    

    def _miniaudio_init(self):
        if self.encoding == 16:
            self.playback_device = miniaudio.PlaybackDevice(
                miniaudio.SampleFormat.SIGNED16,
                self.channels,
                self.rate,
                self.buffersize_ms,
                callback_periods=2
                )
        else:
            raise ValueError("8 bit encoding not supported for miniaudio!")
        
        def miniaudio_generator():
            yield b""
            while self.miniaudio_running:
                data = self.audio_buffer[0:self.req_audio_buffer_size]
                del self.audio_buffer[0:self.req_audio_buffer_size]
                yield data
        
        self.req_audio_buffer_size = (self.channels * self.rate * self.encoding//8) // round(1000/self.buffersize_ms)
        self.audio_buffer = bytearray(self.req_audio_buffer_size)
        self.miniaudio_running = False
        self.data_generator = miniaudio_generator()

        self.write = self._miniaudio_write


    def write(self, data: bytes):
        pass

    def _pyaudio_write(self, data: bytes):
        self.stream.write(data)
    
    def _android_write(self, data: bytes):
        bytes_written = self.audio_stream.write(data, 0, len(data))
    
    def _miniaudio_write(self, data: bytes):
        if not self.miniaudio_running:
            self.miniaudio_running = True
            next(self.data_generator)
            self.playback_device.start(self.data_generator)
        while data:
            if len(self.audio_buffer) < self.req_audio_buffer_size:
                self.audio_buffer.extend(data)
                break
            else:
                time.sleep(0.01)


class AudioDecoder():

    def __init__(self):

        self.app_folder = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

        if AUDIO_API == "audiotrack":
            self._android_init()
        
        elif AUDIO_API == "miniaudio":
            self._miniaudio_init()
    
    def _miniaudio_init(self):
        self.load_ogg = self._miniaudio_load_ogg
        self.load_mp3 = self._miniaudio_load_mp3
        self.mp3_to_wav = self._miniaudio_mp3_to_wav


    def _android_init(self):
        self.load_ogg = self._android_load_ogg
        self.load_mp3 = self._android_load_mp3
        self.mp3_to_wav = self._android_mp3_to_wav
    
    def load_ogg(self, file, start_frame, num_chunks, chunk_frame_len, reverse_audio=False, frame_rate=44_100):
        pass

    def load_mp3(self, file, track_id, start_frame, num_chunks, chunk_frame_len, frame_rate=44_100):
        pass

    def mp3_to_wav(self, file, track_id, frame_rate=44_100):
        pass


    class MiniaudioVorbisFileStream():
        """
        The generator for a vorbis file provided by miniaudio:
          a) Doesn't allow us to set a fixed chunk length
          b) Doesn't expose the actual stb_vorbis* pointer, which doesn't allow us to seek (for reversing)
        Therefore, I made my own class that acts a lot like the pyogg variant to decode bytes and seek
        """

        def __init__(self, file, start_frame=0, frames_to_read=1024):
            filenamebytes = _get_filename_bytes(file)
            with ffi.new("int *") as error:
                self.vorbis = lib.stb_vorbis_open_filename(filenamebytes, error, ffi.NULL)
                if not self.vorbis:
                    raise miniaudio.DecodeError("Could not open/decode file")
            self.info = lib.stb_vorbis_get_info(self.vorbis)
            self.decode_buffer = ffi.new("short[]", frames_to_read * self.info.channels)
            self.decodebuf_ptr = ffi.cast("short *", self.decode_buffer)
            self.bytes_per_sample = 2 * self.info.channels
            if start_frame > 0:
                self.seek(start_frame)
            self.frames_to_read = frames_to_read
        
        def __enter__(self):
            return self
        
        def get_buffer(self):
            num_samples = lib.stb_vorbis_get_samples_short_interleaved(self.vorbis, self.info.channels, self.decodebuf_ptr,
                                                                                self.frames_to_read * self.info.channels)
            if num_samples <= 0:
                return bytes(0)
                    
            buffer = ffi.buffer(self.decode_buffer, num_samples * self.bytes_per_sample)
            return bytes(buffer)
        
        def seek(self, seek_frame):
            result = lib.stb_vorbis_seek(self.vorbis, seek_frame)
            if result <= 0:
                raise miniaudio.DecodeError(f"Can't seek to frame {seek_frame}")
            
        def close(self):
            self.__exit__()

        def __exit__(self, *args):
            ffi.release(self.decode_buffer)
            lib.stb_vorbis_close(self.vorbis)
    
    class AndroidFileStream():

        def __init__(self, file, start_frame=0, frames_to_read=1024):
            MediaExtractor = autoclass("android.media.MediaExtractor")
            FileInputStream = autoclass("java.io.FileInputStream")
            File = autoclass("java.io.File")
            MediaCodec = autoclass("android.media.MediaCodec")
            MediaFormat = autoclass("android.media.MediaFormat")
            BufferInfo = autoclass("android.media.MediaCodec$BufferInfo")

            self.BUFFER_FLAG_END_OF_STREAM = MediaCodec.BUFFER_FLAG_END_OF_STREAM

            self.media_extractor = MediaExtractor()
            file_input_stream = FileInputStream(File(file))
            fd = file_input_stream.getFD()
            self.media_extractor.setDataSource(fd)
            file_input_stream.close()

            self.media_extractor.selectTrack(0)
            self.track_format = self.media_extractor.getTrackFormat(0)

            mime = self.track_format.getString("mime")
            self.sample_rate = self.track_format.getInteger("sample-rate")
            self.channel_count = self.track_format.getInteger("channel-count")

            self.decoder = MediaCodec.createDecoderByType(mime)
            self.decoder.configure(self.track_format, None, None, 0)
            self.decoder.start()

            self.buffer_info = BufferInfo()
            self.extra_pcm_buffer = bytearray(0)
            
            if start_frame > 0:
                self.seek(start_frame)
            self.frames_to_read = frames_to_read
            self.bytes_per_sample = 2 * self.channel_count
        
        def __enter__(self):
            return self
        
        def get_buffer(self):
            pcm_output_buffer = self.extra_pcm_buffer
            eof = False

            if len(pcm_output_buffer) >= self.frames_to_read * self.bytes_per_sample:
                self.extra_pcm_buffer = pcm_output_buffer[self.frames_to_read * self.bytes_per_sample:]
                return bytes(pcm_output_buffer[:self.frames_to_read * self.bytes_per_sample])


            while len(pcm_output_buffer) < self.frames_to_read * self.bytes_per_sample:

                if not eof:
                    input_buffer_id = self.decoder.dequeueInputBuffer(100_000)

                    if input_buffer_id >= 0:
                        input_buffer = self.decoder.getInputBuffer(input_buffer_id)
                        total_sample_bytes = 0
                        presentation_time_us = 0

                        while total_sample_bytes < 2048:
                            num_encoded_bytes = self.media_extractor.readSampleData(input_buffer, total_sample_bytes)

                            if num_encoded_bytes < 0:
                                eof = True
                                self.decoder.queueInputBuffer(input_buffer_id, 0, 0, 0, self.BUFFER_FLAG_END_OF_STREAM)
                                break
                        
                            else:
                                total_sample_bytes += num_encoded_bytes
                                presentation_time_us = self.media_extractor.getSampleTime()
                                self.media_extractor.advance()
                            
                            if total_sample_bytes >= 2048:
                                self.decoder.queueInputBuffer(input_buffer_id, 0, total_sample_bytes, presentation_time_us, 0)
                                break
                                    
                output_buffer_id = self.decoder.dequeueOutputBuffer(self.buffer_info, 100_000)

                if output_buffer_id >= 0:
                    output_buffer = self.decoder.getOutputBuffer(output_buffer_id)
                    buffer_format = self.decoder.getOutputFormat(output_buffer_id)

                    pcm_chunk = bytearray(self.buffer_info.size)
                    output_buffer.get(pcm_chunk)
                    pcm_output_buffer.extend(pcm_chunk)

                    self.decoder.releaseOutputBuffer(output_buffer_id, False)

                if eof:
                    break
                
            
            if len(pcm_output_buffer) > self.frames_to_read * self.bytes_per_sample:
                self.extra_pcm_buffer = pcm_output_buffer[self.frames_to_read * self.bytes_per_sample:]

            return bytes(pcm_output_buffer[:self.frames_to_read * self.bytes_per_sample])

        
        def seek(self, seek_frame):
            """
            Note, while android seekTo takes in a value in microseconds, this function
            takes in a specific frame, not time.
            """
            seek_time_us = (seek_frame / self.sample_rate) * 1_000_000
            self.media_extractor.seekTo(seek_time_us, self.media_extractor.SEEK_TO_CLOSEST_SYNC)
            
        def close(self):
            self.__exit__()

        def __exit__(self, *args):
            self.media_extractor.release()
            self.decoder.stop()
            self.decoder.release()


    def load_wav(self, file, start_frame, num_chunks, chunk_frame_len, reverse_audio=False, frame_rate=44_100):

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
                audio = AudioSegment(data=byte_data, frame_rate=frame_rate, channels=2, sample_width=2)
                if reverse_audio:
                    yield audio.reverse()
                else:
                    yield audio

    def _miniaudio_load_ogg(self, file, start_frame, num_chunks, chunk_frame_len, reverse_audio=False, frame_rate=44_100):

        miniaudio_stream = self.MiniaudioVorbisFileStream(file, start_frame, frames_to_read=chunk_frame_len)

        cut = False

        for chunk_index in range(num_chunks):
            if reverse_audio:
                frame_pos = -chunk_frame_len*(chunk_index+1) + start_frame
                if frame_pos < 0:
                    chunk_frame_len += frame_pos # frame pos is negative here, so we're subtracting from chunk_frame_len
                    cut = True # Set flag indicating that we have to cut out a part of the AudioSegment
                    frame_pos = 0
                miniaudio_stream.seek(frame_pos)

            byte_data = miniaudio_stream.get_buffer()

            if not byte_data:
                break

            audio = AudioSegment(data=byte_data, frame_rate=frame_rate, channels=2, sample_width=2)
            if reverse_audio:
                if cut:
                    audio._data = audio._data[:chunk_frame_len]
                yield audio.reverse()
            else:
                yield audio
    
    def _miniaudio_load_mp3(self, file, track_id, start_frame, num_chunks, chunk_frame_len, frame_rate=44_100):

        try:
            miniaudio_stream = miniaudio.mp3_stream_file(file, frames_to_read=chunk_frame_len, seek_frame=start_frame)
            for samples in miniaudio_stream:

                byte_data = samples.tobytes()
                audio = AudioSegment(data=byte_data, frame_rate=frame_rate, channels=2, sample_width=2)

                yield audio

        except miniaudio.DecodeError:
            # Miniaudio on windows is fucking stupid and can't resolve file names with non-ascii characters
            # Create a hard link here with a set (ASCII) name that we can read from instead
            hard_link_path = f"{self.app_folder}/cache/audio/{track_id}.mp3"
            if not os.path.exists(hard_link_path):
                os.link(file, hard_link_path)

            miniaudio_stream = miniaudio.mp3_stream_file(hard_link_path, frames_to_read=chunk_frame_len, seek_frame=start_frame)
            for samples in miniaudio_stream:

                byte_data = samples.tobytes()
                audio = AudioSegment(data=byte_data, frame_rate=frame_rate, channels=2, sample_width=2)

                yield audio
    
    def _miniaudio_mp3_to_wav(self, file, track_id, frame_rate=44_100):
                
        # Try to delete files if there are too many (>3) in the cache
        current_cached_files = glob(f"{self.app_folder}/cache/audio/*_reversed.wav")
        if len(current_cached_files) > 3:
            num_to_delete = len(current_cached_files) - 3
            # Sort everything by when they were last modfied to choose deletion order
            current_cached_files.sort(key=lambda path: os.path.getmtime(path))
            for wav_file in current_cached_files[0:num_to_delete]:
                try:
                    os.remove(wav_file)
                except OSError:
                    # If we encounter an OSError, then it's likely the file is still open
                    # Just leave it alone in this case
                    pass
                
        # Windows path reading shenanigans
        if not file.isascii():
            hard_link_path = f"{self.app_folder}/cache/audio/{track_id}.mp3"
            if not os.path.exists(hard_link_path):
                os.link(file, hard_link_path)
            file_stream = miniaudio.mp3_stream_file(hard_link_path)
        else:
            file_stream = miniaudio.mp3_stream_file(file)

        with wave.open(f"{self.app_folder}/cache/audio/{track_id}_reversed.wav", "wb") as fp:
            fp.setparams((2, 2, frame_rate, 0, "NONE", "NONE"))
            for samples in file_stream:
                fp.writeframes(samples)

    def _android_load_ogg(self, file, start_frame, num_chunks, chunk_frame_len, reverse_audio=False, frame_rate=44_100):

        with self.MiniaudioVorbisFileStream(file, start_frame, frames_to_read=chunk_frame_len) as stream:
            cut = False

            for chunk_index in range(num_chunks):
                if reverse_audio:
                    frame_pos = -chunk_frame_len*(chunk_index+1) + start_frame
                    if frame_pos < 0:
                        chunk_frame_len += frame_pos # frame pos is negative here, so we're subtracting from chunk_frame_len
                        cut = True # Set flag indicating that we have to cut out a part of the AudioSegment
                        frame_pos = 0
                    stream.seek(frame_pos)

                byte_data = stream.get_buffer()

                if not byte_data:
                    break

                audio = AudioSegment(data=byte_data, frame_rate=frame_rate, channels=2, sample_width=2)
                if reverse_audio:
                    if cut:
                        audio._data = audio._data[:chunk_frame_len]
                    yield audio.reverse()
                else:
                    yield audio

    def _android_load_mp3(self, file, track_id, start_frame, num_chunks, chunk_frame_len, frame_rate=44_100):

        with self.AndroidFileStream(file, start_frame, frames_to_read=chunk_frame_len) as stream:
            for chunk_index in range(num_chunks):

                byte_data = stream.get_buffer()

                if not byte_data:
                    break

                audio = AudioSegment(data=byte_data, frame_rate=frame_rate, channels=2, sample_width=2)
                yield audio
    
    def _android_mp3_to_wav(self, file, track_id, frame_rate=44_100):

        # Try to delete files if there are too many (>3) in the cache
        current_cached_files = glob(f"{self.app_folder}/cache/audio/*_reversed.wav")
        if len(current_cached_files) > 3:
            num_to_delete = len(current_cached_files) - 3
            # Sort everything by when they were last modfied to choose deletion order
            current_cached_files.sort(key=lambda path: os.path.getmtime(path))
            for wav_file in current_cached_files[0:num_to_delete]:
                try:
                    os.remove(wav_file)
                except OSError:
                    # If we encounter an OSError, then it's likely the file is still open
                    # Just leave it alone in this case
                    pass
                
        with self.AndroidFileStream(file) as stream:

            with wave.open(f"{self.app_folder}/cache/audio/{track_id}_reversed.wav", "wb") as fp:
                fp.setparams((2, 2, frame_rate, 0, "NONE", "NONE"))
                while (samples := stream.get_buffer()):
                    fp.writeframes(samples)

# class FFMPEGProcessHandler():
#     """
#     Ensure ffmpeg processes are "gracefully" killed and don't throw any errors
#     on their way out
#     """

#     def __init__(self, ffmpeg_obj: ffmpeg):
#         self.ffmpeg_process = ffmpeg_obj.run_async(pipe_stdout=True, pipe_stderr=True)

#     def __del__(self, *args, **kwargs):
#         self.ffmpeg_process.terminate()

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
        None
    """

    def __init__(self, lock, channels=2, rate=44_100, buffersize_ms=50, encoding=16) -> None:
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

        self.sos = None
        self.filter_state = np.zeros(shape=(15, 2, 2))
        self.ease_filter = False

        self.volume = 1

        self.app_folder = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

        self.stream = AudioStreamer(channels, rate, buffersize_ms, encoding)
        self.decoder = AudioDecoder()

        self.song_file = f"{common_vars.app_folder}/assets/audio/silence.wav"
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

        with open(common_vars.music_database_path) as fp:
            music_data = yaml.safe_load(fp)

        # Create file -> database LUT since it's more convienient to use the file as the key
        self.track_data = {music_data["tracks"][track]["file"] : music_data["tracks"][track]
                           for track in music_data["tracks"].keys()}

        
    

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
    
    def call_music_database_func(self, func_name, *args):
        self.osc_client.send_message("/call/music_database", (func_name, *args))

    
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
            self.filter_state = np.zeros(shape=(15, 2, 2))
        else:
            self.sos = None
        self.fade_duration = int(self.base_fade_duration * value)
        self._speed = value

    
    def load_chunks(self, file, start_pos=0, end_pos=None):
        """
        This function will load a portion of a song defined by self.chunk_len (set to 120ms).
        Chunks may be shorter than 120ms if at EOF and the song length is not a multiple of self.chunk_len

        file: PathLike: location where the audio file is stored

        start_pos: int: start reading at time = start_pos milliseconds, can be negative.
        If start_pos is negative, start reading at start_pos milliseconds away from end_pos if end_pos is not None
        or EOF if end_pos is None.

        end_pos: int or None: stop reading at time = end_pos milliseconds, if None, read until EOF
        """

        _, file_type = os.path.splitext(file)

        num_frames = total_frames = self.get_track_length(file)[1]
        self.next_song_length = (total_frames / self.track_data[file]["rate"])*1000
        self.next_total_frames = total_frames
        if self.bootup:
            self.song_length = self.next_song_length
            self.total_frames = self.next_total_frames
            if self.reverse_audio and not self.init_pos:
                self.pos = self.song_length
                self.frame_pos = self.total_frames

        if end_pos is not None and end_pos / 1000 * self.track_data[file]["rate"] < num_frames:
            num_frames -= (num_frames - end_pos)
            end_frame = round(end_pos / 1000 * self.track_data[file]["rate"])

        if start_pos >= 0:
            start_frame = round(start_pos / 1000 * self.track_data[file]["rate"])
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
            
            
        num_chunks = ceil(num_frames / (self.track_data[file]["rate"]/1000*self.chunk_len))
        self.num_chunks = num_chunks
        chunk_frame_len = round(self.track_data[file]["rate"] * (self.chunk_len / 1000))

        if file_type == ".wav":
            audio_generator = self.decoder.load_wav(file, start_frame, num_chunks, chunk_frame_len, self.reverse_audio, self.track_data[file]["rate"])
        
        elif file_type == ".ogg":
            audio_generator = self.decoder.load_ogg(file, start_frame, num_chunks, chunk_frame_len, self.reverse_audio, self.track_data[file]["rate"])
        
        elif file_type == ".mp3":
            track_id = self.track_data[file]["id"]
            # Streaming an mp3 in reverse is impossible, so we will instead create a wav file from the mp3 and read that instead
            if self.reverse_audio:
                if not os.path.exists(f"{self.app_folder}/cache/audio/{track_id}_reversed.wav"):
                    self.decoder.mp3_to_wav(file, track_id)
                reversed_file = f"{self.app_folder}/cache/audio/{track_id}_reversed.wav"
                audio_generator = self.decoder.load_wav(reversed_file, start_frame, num_chunks, chunk_frame_len, True, self.track_data[file]["rate"])
            else:
                audio_generator = self.decoder.load_mp3(file, track_id, start_frame, num_chunks, chunk_frame_len, self.track_data[file]["rate"])
        print(f"Using chunk_frame_len of {chunk_frame_len} for file {file}")
        for audio in audio_generator:
            yield audio


    def get_track_length(self, file):
        num_frames = self.track_data[file]["length"]
        return (num_frames / self.track_data[file]["rate"] * 1000), num_frames

    
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
        
        if not hasattr(self, "chunk_index"):
            chunk_index = 0
            num_chunks = 0
            chunk = []
        else:
            chunk_index = self.chunk_index
            num_chunks = self.num_chunks
            chunk = self.chunk
            
        self.debug_string =  f"\nSong: {song_file}\nNext Song: {next_song_file}\n" +\
                        f"Song Pos: {self.pos/1000/self.speed:.3f}/{self.song_length/1000/self.speed:.3f}s " +\
                            f"({self.pos/1000:.3f}/{self.song_length/1000:.3f}s) | " +\
                            f"<{self.frame_pos:,}/{self.total_frames:,}> frames\n" +\
                        f"Seek Pos: {self.seek_pos/1000/self.speed:.3f}s\n" +\
                        f"Audio Player Speed: {self.speed:.3g}x, Fade Duration: {self.base_fade_duration//1000}s\n" +\
                        f"Audio Player Status: {self.status}\n" +\
                        f"Chunk Index: {chunk_index}/{num_chunks-1}, Chunk Length: {len(chunk)/self.speed:.3f}ms, " +\
                        f"Chunk Frame Length: {round(len(chunk._data)/self.speed)} frames\n" +\
                        f"Audio chunk generation time: {chunk_gen_time}"
            #print(self.debug_string)


    
    def pause(self):
        """
        Pauses the audio player. Note: this will block the player from doing anything
        """
        self.stream.write( # This helps clear out any remaining data in the audio buffer (prevents popping sounds)
            bytes(round(self.rate * self.stream.channels * self.stream.encoding // 8 * self.chunk_len / 1000))) 
        while self.pause_flag == True:
            time.sleep(0.05)
        return
    


    def skip(self, next_song_file):
        """
        Skips to another song, cutting out the crossfade entirely
        """
        del self.chunk_generator
        if self.reverse_audio:
            next_song_len, next_song_frame_len = self.get_track_length(next_song_file)
            self.chunk_generator = self.load_chunks(next_song_file, start_pos=next_song_len)
            self.pos = next_song_len
            self.frame_pos = next_song_frame_len
        else:
            self.chunk_generator = self.load_chunks(next_song_file)
            self.pos = 0
            self.frame_pos = 0
        self.seek_pos = 0
        self.song_file = next_song_file
        # This sets the next_song_file to be whatever's next in the database
        self.call_music_database_func("peek_right", 1, "file", "&next_song_file")


    
    def seek(self, start_pos):
        """
        Seek to a certain position in the song
        """
        del self.chunk_generator
        start_pos = int(start_pos)
        self.chunk_generator = self.load_chunks(self.song_file, start_pos=start_pos)
        self.pos = start_pos
        self.frame_pos = round(start_pos / 1000 * self.track_data[self.song_file]["rate"])





    def transition(self, next_song_file=None):
        """
        Handles the smooth crossfading between songs or the crossfading for when a song repeats
        Allows for the user to interuppt this process to skip, change songs (again), or to seek to some other point in the current song
        """

        def n_generator(audio_len, chunk_len=120, *args):
            """
            If we override the transition with another fade, combine all the generators that would have
            been used to play the music into a single object.
            args are of the form (audio_generator1, gain1), (audio_generator2, gain2), ...
            """
            for i in range(ceil(audio_len / chunk_len)):
                for j in range(len(args)):
                    try:
                        output_chunk = next(args[j][0]) + args[j][1]
                        break
                    except StopIteration: # Ignore generators that are empty
                        pass
                try:
                    for (audio_generator, gain) in args[j+1:]:
                        try:
                            mix_chunk = next(audio_generator) + gain
                            output_chunk *= mix_chunk + gain
                        except ValueError:
                            #mix_chunk = next(audio_generator) + gain
                            output_chunk = (output_chunk * mix_chunk[:len(output_chunk)]) + mix_chunk[len(output_chunk):]
                        except StopIteration:
                            pass
                except IndexError: # Handles case if args[j+1] doesn't exist (all but 1 generator empty)
                    pass
                yield output_chunk
        
        def prepend_chunk_generator(chunk_generator, *chunks):
            """
            Instead of calling load_chunks whenever we read a partial chunk,
            we can just prepend it to the existing chunk generator
            """
            for chunk in chunks:
                yield chunk
            yield from chunk_generator

        # Don't add playcounts for anything in the assets (ie /assets/audio/silence.wav)
        if self.status == "playing" and not os.path.samefile(os.path.split(self.song_file)[0], f"{self.app_folder}/assets/audio/"):
            add_playcount = True
        else:
            add_playcount = False

        if next_song_file is None: # Repeat currently playing song
            next_song_file = self.song_file
            self.status = "repeat"

        elif self.status != "fade_in": # Transition to next song
            # Make sure our database pointer is synced
            self.call_music_database_func("set_track", next_song_file)
            self.status = "transition"
        
        next_song_len, next_song_frame_len = self.get_track_length(next_song_file)
        
        if self.status != "fade_in":

            fade_type = "crossfade"
        
            if self.reverse_audio:
                time_remaining = round(self.pos)
            else:
                time_remaining = round(self.song_length - self.pos) # Remaining time we have left in the song

            if time_remaining > self.fade_duration and self.status == "repeat": # If we aren't exactly fade_duration away from end, play last remaining bit of song

                chunk = next(self.chunk_generator)
                chunk, extra_chunk = chunk[0:time_remaining-self.fade_duration], chunk[time_remaining-self.fade_duration:]
                self.write_to_buffer(chunk)
                if self.reverse_audio:
                    self.pos -= len(chunk)
                    self.frame_pos -= chunk.get_num_frames()
                else:
                    self.pos += len(chunk)
                    self.frame_pos += chunk.get_num_frames()
                if len(extra_chunk) != 0: # Sometimes lengths match up perfectly and extra chunk ends up being a zero len chunk
                    self.chunk_generator = prepend_chunk_generator(self.chunk_generator, extra_chunk)
            

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
        
        else:

            fade_type = "fade_in"

            next_chunk_generator = None

            if self.reverse_audio:
                self.chunk_generator = self.load_chunks(next_song_file, start_pos=next_song_len)
                self.pos = next_song_len
                self.frame_pos = next_song_frame_len
            else:
                self.chunk_generator = self.load_chunks(next_song_file)
                self.pos = 0
                self.frame_pos = 0

            self.song_length = next_song_len
            self.total_frames = next_song_frame_len
            
            if next_song_len < self.fade_duration:
                fade_duration = int(next_song_len) - self.chunk_len
            else:
                fade_duration = self.fade_duration
        
        #print(next_song_len)
        if self.reverse_audio:
            new_pos = next_song_len
            new_frame_pos = next_song_frame_len
        else:
            new_pos = 0
            new_frame_pos = 0

        if fade_duration > 0:
            for chunk, end_chunk_db, start_chunk_db in step_fade(self.chunk_generator, next_chunk_generator, 
                                                                fade_duration=fade_duration, chunk_len=self.chunk_len, fade_type=fade_type): # Begin crossfade
                chunk: AudioSegment
                if self.status == "change_song": # If we decide to change songs during a transition, crossfade the (already crossfading) audio with the next song
                    self.status = "override_transition"

                    if self.reverse_audio:
                        time_remaining = round(self.pos)
                    else:
                        time_remaining = round(self.song_length - self.pos) # Remaining time we have left in the song

                    if next_chunk_generator is not None:
                        self.chunk_generator = n_generator(min(self.fade_duration, time_remaining), self.chunk_len, 
                                                        (self.chunk_generator, end_chunk_db), 
                                                        (next_chunk_generator, start_chunk_db))
                    else:
                        self.chunk_generator = n_generator(min(self.fade_duration, time_remaining), self.chunk_len, 
                                                        (self.chunk_generator, end_chunk_db))
                    self.transition(self.next_song_file)
                    return
                
                elif self.status == "skip": # If we change songs via a skip during a transition, just go to the next song
                    if (self.reverse_audio and next_song_len - new_pos <= fade_duration/2) or (not self.reverse_audio and new_pos <= fade_duration/2):
                        self.skip(next_song_file)
                        self.call_music_database_func("set_track", next_song_file) # Resync music_database pointer, otherwise we will be one song ahead/behind
                    elif self.next_song_file is not None:
                        self.skip(self.next_song_file)
                    else:
                        self.skip(self.song_file)
                    return
                
                elif self.status == "seek": # If we seek during a transition, override the transition and play the currently fading out song as normal
                    self.seek(self.seek_pos)
                    self.call_music_database_func("set_track", next_song_file) # Resync music_database pointer
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
        
        # This is really fucked up way of setting the next_song_file to be whatever's next in the database
        self.call_music_database_func("peek_right", 1, "file", "&next_song_file")

        if add_playcount:
            self.call_music_database_func("__add__", 1)

        if next_chunk_generator is not None:
            self.chunk_generator = next_chunk_generator

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
        if self.speed != 1 or audio.frame_rate != self.rate:
            data, self.filter_state = change_speed(data, self.speed * audio.frame_rate/self.rate, self.sos, zf=self.filter_state, dt=audio.dt)
        self.get_debug_info()
        self.stream.write(data)
        if self.pause_flag == True:
            self.pause()
        self.start = time.time_ns()

        # with open("./audio/audioplayer_out.raw", "ab") as fp:
        #     fp.write(data)

    

    def zawarudo(self):
        """
        Is that a fucking JoJo reference???
        """
        speed_list = np.linspace(self.speed, 0, round(1800 / self.chunk_len), endpoint=False)

        extra_song_audio = None # set this for later

        with wave.open(f"{self.app_folder}/assets/audio/zawarudo.wav", "rb") as zwfp:

            zw = zwfp.readframes(round(self.chunk_len*44_100/self.speed/1000)) # Read 50 ms of audio from zawarudo.wav

            min_db = -5

            amp_list = np.linspace(1, db_to_amp(min_db), round(600 / self.chunk_len))
            
            db_list = [(amp_to_db(amp_list[index]), amp_to_db(amp_list[index+1])) for index in range(len(amp_list)-1)]

            i = 0
            while len(zw) != 0:

                data = next(self.chunk_generator).data # Get song data bytes

                # Change the speed of the audio to whatever the user has set, taking into account the sampling rate of the output data
                data, _ = change_speed(data, self.speed * self.track_data[self.song_file]["rate"]/self.rate)

                # Frame rate is 44.1 kHz because the audio we use in the assets is 44.1 kHz, not necessaryily self.rate
                zw_audio = AudioSegment(data=zw, frame_rate=44_100, channels=2, sample_width=2) + amp_to_db(self.volume)

                # Even though song_audio could be any sampling rate, the above change_speed function will have resampled it to self.rate (44.1 kHz)
                song_audio = AudioSegment(data=data, frame_rate=self.rate, channels=2, sample_width=2)

                # For the first few chunks, we want to fade down to the desired ducking volume (5 dB)
                if i < len(db_list):
                    song_audio = song_audio.fade(from_gain=db_list[i][0], to_gain=db_list[i][1], 
                                                 start=0, duration=len(data)//4) + amp_to_db(self.volume)
                else:
                    song_audio = song_audio + min_db + amp_to_db(self.volume)

                # If chunk lengths match, mix together and play, otherwise cut off remaining zw audio and add extra audio data to extra_song_audio
                if len(song_audio) == len(zw_audio):
                    self.stream.write((song_audio * zw_audio).data)
                else:
                    extra_song_audio = song_audio[len(zw_audio):] # Note, this has already been resampled to self.rate
                    self.stream.write((song_audio[0:len(zw_audio)] * zw_audio).data)

                self.get_debug_info()
                # Change frame_pos by how many frames *would have* been read before accounting for resampling rather than self.rate
                if self.reverse_audio:
                    self.pos -= len(zw_audio) * self.speed
                    self.frame_pos -= round(self.chunk_len*self.track_data[self.song_file]["rate"]/1000)
                else:
                    self.pos += len(zw_audio) * self.speed
                    self.frame_pos += round(self.chunk_len*self.track_data[self.song_file]["rate"]/1000)
                
                # Read in more zawarudo.wav audio
                zw = zwfp.readframes(round(self.chunk_len*44_100/self.speed/1000))

                i += 1

        with wave.open(f"{self.app_folder}/assets/audio/time_stop.wav", "rb") as zwfp:

            zwfp.setpos(21_563) # Set position to a specific point in the file

            if extra_song_audio is None:
                extra_song_audio = AudioSegment(data=bytes(0), frame_rate=self.rate, channels=2, sample_width=2)

            for i, speed in enumerate(speed_list):
                chunk_len = speed * self.chunk_len
                # Change frame_pos by how many frames *would have* been read before accounting for resampling rather than self.rate
                num_frames = round(self.track_data[self.song_file]["rate"]*chunk_len / 1000)

                while len(extra_song_audio) < chunk_len:
                    extra_song_audio = extra_song_audio + next(self.chunk_generator) # This action will automatically resample the audio to self.rate

                data = extra_song_audio[:chunk_len].data # We cut based on milliseconds here, so sample rate shouldn't affect these
                extra_song_audio = extra_song_audio[chunk_len:]
                
                data, _ = change_speed(data, speed) # No need to resample here since extra_song_audio will have done that automatically

                zw = zwfp.readframes(round(self.chunk_len*44_100/1000))
                
                # Again, resampling for song_audio should already be handled by the above code; set it to self.rate
                song_audio = AudioSegment(data=data, frame_rate=self.rate, channels=2, sample_width=2) + (min_db + amp_to_db(self.volume))
                zw_audio = AudioSegment(data=zw, frame_rate=44_100, channels=2, sample_width=2) + amp_to_db(self.volume)

                # This should always be the same length
                self.stream.write((song_audio * zw_audio).data)

                # Tell the main display to run the shaders to do the time stop effect
                if i == 1:
                    self.osc_client.send_message("/call/main/main_display", "_start_time_effect")

                self.get_debug_info()
                if self.reverse_audio:
                    self.pos -= chunk_len
                    self.frame_pos -= num_frames
                else:
                    self.pos += chunk_len
                    self.frame_pos += num_frames
            
            zw = zwfp.readframes(round(self.chunk_len*self.rate/1000))

            # After the slow down effect has been done, read the rest of the time_stop.wav
            while len(zw) != 0:
                zw_audio = AudioSegment(data=zw, frame_rate=44_100, channels=2, sample_width=2) + amp_to_db(self.volume)
                self.stream.write(zw_audio.data)
                zw = zwfp.readframes(round(self.chunk_len*self.rate/1000))

        time.sleep(5)
        #self.pause()

        # Reverse our volume list to undo the previous ducking
        db_list.reverse()
        
        with wave.open(f"{self.app_folder}/assets/audio/time_resume.wav", "rb") as zwfp:

            speed_list = np.linspace(0.1, self.speed, round(1800 / self.chunk_len), endpoint=True)
            end_offset = (len(speed_list) - len(db_list))
            for i, speed in enumerate(speed_list):
                chunk_len = speed * self.chunk_len
                # Change frame_pos by how many frames *would have* been read before accounting for resampling rather than self.rate
                num_frames = round(self.track_data[self.song_file]["rate"]*chunk_len / 1000)

                while len(extra_song_audio) < chunk_len:
                    extra_song_audio = extra_song_audio + next(self.chunk_generator) # This action will automatically resample the audio to self.rate
                
                data = extra_song_audio[:chunk_len].data # We cut based on milliseconds here, so sample rate shouldn't affect these
                extra_song_audio = extra_song_audio[chunk_len:]

                data, _ = change_speed(data, speed) # No need to resample here since extra_song_audio will have done that automatically

                zw = zwfp.readframes(round(self.chunk_len*44_100/1000))

                # Frame rate is 44.1 kHz because the audio we use in the assets is 44.1 kHz, not necessaryily self.rate
                zw_audio = AudioSegment(data=zw, frame_rate=44_100, channels=2, sample_width=2) + amp_to_db(self.volume)

                # Again, even though song_audio could be any sampling rate, the above change_speed function will have resampled it to self.rate
                song_audio = AudioSegment(data=data, frame_rate=self.rate, channels=2, sample_width=2)

                if i >= end_offset:
                    song_audio = song_audio.fade(from_gain=db_list[i - end_offset][1], to_gain=db_list[i - end_offset][0], 
                                                 start=0, duration=len(data)//4) + amp_to_db(self.volume)
                else:
                    song_audio = song_audio + (min_db + amp_to_db(self.volume))

                self.stream.write((song_audio * zw_audio).data)
                self.get_debug_info()
                if self.reverse_audio:
                    self.pos -= chunk_len
                    self.frame_pos -= num_frames
                else:
                    self.pos += chunk_len
                    self.frame_pos += num_frames
                        
        # If there's any left over data, play it
        if len(extra_song_audio):
            self.stream.write((extra_song_audio + amp_to_db(self.volume)).data)

        # We can keep using self.chunk_generator, just keep playing the rest of the song
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


                if self.status == "seek": # Check if we need to seek within current song
                    self.seek(self.seek_pos)
                    break

                elif self.status == "skip": # Check if we need to skip to another song
                    self.skip(self.next_song_file)
                    break

                elif self.status == "zawarudo":
                    self.zawarudo()
                    break

                elif self.status == "fade_in":
                    self.transition(self.next_song_file)
                    break

                # If song needs to change or restart, handle fading/crossfading
                elif (self.pos <= (self.fade_duration + self.chunk_len) or self.status == "change_song") and self.reverse_audio:
                    self.transition(self.next_song_file)
                    break

                elif (self.pos >= (self.song_length - self.fade_duration - self.chunk_len) or self.status == "change_song") and not self.reverse_audio:
                    self.transition(self.next_song_file) 
                    break

            if self.status != "stopped":
                self.lock_status = False
                self.status = "playing"

if __name__ == "__main__" or __name__ == "<run_path>":
    try:
        audioplayer = AudioPlayer(lock=Lock(), rate=44_100)
        audioplayer.run()
    # No matter what happens, try to make sure that if the audioplayer dies, so too does the UI
    except Exception as err:
        import traceback
        traceback.print_exception(err)
        audioplayer.osc_client.send_message("/kill", str(err))
        exit()
