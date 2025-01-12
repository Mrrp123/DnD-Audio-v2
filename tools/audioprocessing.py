from math import log10, ceil
import time
import numpy as np
from scipy.signal import sosfilt

def make_chunks(audio_segment, chunk_length):
    """
    This just cuts audio into chunk_length millisecond pieces (the first chunk is shorter if chunks don't quite match up)
    """
    number_of_chunks = ceil(len(audio_segment) / float(chunk_length))
    # Why does list comprehension have to be faster...
    # This is just painful to read
    chunk_list = [audio_segment[len(audio_segment) - (i + 1) * chunk_length:len(audio_segment) - i * chunk_length] if len(audio_segment) - (i + 1) * chunk_length > 0 
        else audio_segment[0:len(audio_segment) - i * chunk_length] for i in range(int(number_of_chunks))]
    return list(reversed(chunk_list))

def amp_to_db(amp_ratio):
    """
    Converts normalized wave amplitude value (volume) into decibels
    """
    if amp_ratio <= 0:
        return -120
    else:
        return 20 * log10(amp_ratio)


def db_to_amp(db):
    """
    Converts decibels to normalized wave amplitude value
    """
    if db <= -120:
        return 0
    else:
        return 10**(db / 20) 

# def step_fade_old(chunk, next_chunk=None, max_fade=100, fade_type="fade_out"):
#         """
#         Fades a single audio track or crossfades two audio tracks together.
#         Used as a generator
#         """

#         # len(chunk) == fade_duration in player (say 12000)
#         # len(segment) == 100 (ish)

#         fade_segments = make_chunks(chunk, max_fade)
#         fade_list = [len(segment) / len(chunk) for segment in fade_segments]

#         db_list = [(amp_to_db(j[0]), amp_to_db(j[1]))
#             for j in [(1 - sum(fade_list[:i]), 1 - sum(fade_list[:i+1])) for i in range(len(fade_list))]]
        
#         print(sum(fade_list), end="\n\n\n")
#         #print(db_list)
        
#         for x, segment in enumerate(fade_segments):
#             if fade_type == "fade_out" or fade_type == "crossfade":
#                 fade_out_chunk = fade_out_chunk = segment.fade(to_gain=db_list[x][1], from_gain=db_list[x][0], start=0, duration=len(segment))
#                 fade_out_chunk_db = db_list[x][0]
#             else:
#                 fade_out_chunk = 1
#                 fade_out_chunk_db = 0
#             if fade_type == "fade_in" or fade_type == "crossfade":
#                 if fade_type == "fade_in":
#                     next_chunk = chunk
#                 if x > 0:
#                     fade_in_chunk = next_chunk[max_fade*(x-1) + len(fade_segments[0]):max_fade*x + len(fade_segments[0])].fade(to_gain=db_list[-(x+1)][0], 
#                         from_gain=db_list[-(x+1)][1], start=0, duration=len(segment))
#                 else:
#                     fade_in_chunk = next_chunk[0:len(segment)].fade(to_gain=db_list[-(x+1)][0], 
#                         from_gain=db_list[-(x+1)][1], start=0, duration=len(segment))
#                 fade_in_chunk_db = db_list[-(x+1)][1]
#             else:
#                 fade_in_chunk = 1
#                 fade_in_chunk_db = 0
#             chunk = fade_out_chunk * fade_in_chunk
#             yield chunk, fade_out_chunk_db, fade_in_chunk_db

def step_fade(chunk_gen, next_chunk_gen=None, fade_duration=3000, chunk_len=120, fade_type="fade_out"):
    """
    Fades audio from one or two AudioSegment *generators*. Note: fade_duration MUST be >= chunk_len
    """
    # Defines what proportion of the crossfade duration each chunk should make up
    fade_list = [chunk_len / fade_duration] * (fade_duration // chunk_len) + [fade_duration % chunk_len / fade_duration]
    
    # Using fade_list, creates a map of what volume (in decibels) the starts and ends of each chunk should be at
    db_list = [(amp_to_db(j[0]), amp_to_db(j[1]))
            for j in [(1 - sum(fade_list[:i]), 1 - sum(fade_list[:i+1])) for i in range(len(fade_list))]]
    x = 0 # loop index
    while x < ceil(fade_duration / chunk_len):
        chunk = next(chunk_gen)
        
        if fade_type == "fade_out" or fade_type == "crossfade":
            fade_out_chunk = chunk.fade(to_gain=db_list[x][1], from_gain=db_list[x][0], start=0, duration=len(chunk))
            fade_out_chunk_db = db_list[x][0]
        else:
            fade_out_chunk = 1
            fade_out_chunk_db = 0
        if fade_type == "fade_in" or fade_type == "crossfade":
            if fade_type == "fade_in":
                next_chunk = chunk
            else:
                next_chunk = next(next_chunk_gen)
            if len(next_chunk) > len(chunk):
                next_chunk = next_chunk[0:len(chunk)]
            fade_in_chunk = next_chunk.fade(to_gain=db_list[-(x+1)][0], from_gain=db_list[-(x+1)][1], start=0, duration=len(next_chunk))
            fade_in_chunk_db = db_list[-(x+1)][1]
        else:
            fade_in_chunk = 1
            fade_in_chunk_db = 0
        try:
            output_chunk = fade_out_chunk * fade_in_chunk
        except TypeError:
            output_chunk = fade_in_chunk * fade_out_chunk
    
        x += 1
        yield output_chunk, fade_out_chunk_db, fade_in_chunk_db

def resample(channel_samples, scale=1.0):
        """
        Written by Nathan Whitehead
        Resample a sound to be a different length
        Sample must be mono.  May take some time for longer sounds
        sampled at 44100 Hz.

        Keyword arguments:
        scale - scale factor for length of sound (2.0 means double length)
        """
        # f*ing cool, numpy can do this with one command
        # calculate new length of sample
        n = round(len(channel_samples) * scale)
        # use linear interpolation
        # endpoint keyword means than linspace doesn't go all the way to 1.0
        # If it did, there are some off-by-one errors
        # e.g. scale=2.0, [1,2,3] should go to [1,1.5,2,2.5,3,3]
        # but with endpoint=True, we get [1,1.4,1.8,2.2,2.6,3]
        # Both are OK, but since resampling will often involve
        # exact ratios (i.e. for 44100 to 22050 or vice versa)
        # using endpoint=False gets less noise in the resampled sound
        return np.interp(
            np.linspace(0.0, 1.0, n, endpoint=False), # where to interpret
            np.linspace(0.0, 1.0, len(channel_samples), endpoint=False), # known positions
            channel_samples, # known data points
            )

def change_speed(song_data, speed, sos=None, zf=np.zeros(shape=(15, 2, 2)), dt=np.int16):
    """
    This function manipulates the raw audio data to speed up or slow down a song by averaging audio samples
    or by cutting out audio samples. This process is slow when upsampling
    """
    
    def filter_signal(data, sos, zf):
        y, zf = sosfilt(sos, data, axis=0, zi=zf)

        # This prevents pops and cracking, sosfilt returns a np.float64 array 
        # which sometimes exceeds the 16 bit signed int limit.
        # This needs to be constrained to fit in our range.

        return np.clip(y, a_min=-32768, a_max=32767), zf
    
    channels = np.fromstring(bytes(song_data), dtype=dt) # Convert bytes to numpy array (faster to deal with)
    channels.shape = (len(song_data)//4, 2)

    channels = np.apply_along_axis(resample, axis=0, arr=channels, scale=1/speed)

    if sos is not None and speed < 1: # If we're slowing down, we need to apply a low pass filter to the outgoing audio
        channels, zf = filter_signal(channels, sos, zf)


    return channels.astype(dt).tobytes(), zf # return as bytes and get last filter state