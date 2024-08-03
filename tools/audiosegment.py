import numpy as np
from tools.audioprocessing import db_to_amp

class AudioSegment():

    def __init__(self, data: bytes, frame_rate: int = 44_100, channels: int = 2, sample_width: int = 2):

        match sample_width:
            case 1:
                self.dt = np.int8
            case 2:
                self.dt = np.int16
            case 4:
                self.dt = np.int32
            case _:
                raise ValueError(f"sample width of {sample_width} not supported!")
        
        self.min_val = np.iinfo(self.dt).min
        self.max_val = np.iinfo(self.dt).max
            
        self._data: np.ndarray = np.fromstring(data, dtype=self.dt)
        self._data.shape = (self._data.size//channels, channels)

        self.frame_rate = frame_rate
        self.channels = channels
        self.sample_width = sample_width
    
    def __add__(self, arg):
        if isinstance(arg, AudioSegment):
            self._data = np.concatenate((self._data, arg._data))
            return self
        elif isinstance(arg, float | int | np.integer):
            self._data = np.clip(self._data * db_to_amp(arg), a_min=self.min_val, a_max=self.max_val).astype(self.dt)
            return self
        else:
            raise TypeError(f"unsupported operand type(s) for +: 'AudioSegment' and {type(arg)}")
    
    def __sub__(self, arg):
        if isinstance(arg, float | int | np.integer):
            return self.__add__(-arg)
        else:
            raise TypeError(f"unsupported operand type(s) for -: 'AudioSegment' and {type(arg)}")
    
    def __mul__(self, arg):
        if isinstance(arg, AudioSegment):

            self._data = self._data.astype(np.float64) # Cast to float64 incase we overflow from the sum of the signals

            # data_max = np.max(self._data)
            # arg_max = np.max(self._data)

            try:
                self._data = self._data + arg._data
            except ValueError:
                # If audio samples are close enough, add silence to fill the hole
                if (frame_diff := self.get_num_frames() - arg.get_num_frames()) < np.ceil(self.frame_rate / 1000)\
                    and frame_diff > -np.ceil(self.frame_rate / 1000):
                    
                    if frame_diff > 0:
                        self._data = self._data + np.concatenate((arg._data, np.zeros(shape=(frame_diff, arg.channels))))
                    else:
                        self._data = np.concatenate((self._data, np.zeros(shape=(-frame_diff, self.channels)))) + arg._data
                else:
                    raise

            max_val = np.max(np.abs(self._data))
            if max_val > self.max_val: # If the sum of our signals would clip, normalize the signal to our dtype range
                # norm_factor = ((data_max + arg_max)/2) / max_val
                self._data = self._data * (self.max_val / max_val)
            
            self._data = self._data.astype(self.dt)

            return self

        elif isinstance(arg, int | np.integer):
            self._data = np.asarray([self._data]*arg).astype(self.dt)
            return self
        
        else:
            raise TypeError(f"unsupported operand type(s) for *: 'AudioSegment' and {type(arg)}")
    
    def __len__(self):
        """
        Returns audio length in milliseconds
        """
        return round(1000 * (self.get_num_frames() / self.frame_rate))
    
    def __getitem__(self, ms):
        if isinstance(ms, slice):
            if ms.step:
                raise ValueError("slicing in steps is not supported!")
            
            start = ms.start if ms.start is not None else 0
            end = ms.stop if ms.stop is not None else len(self.data)

            start = min(start, len(self.data))
            end = min(end, len(self.data))

            start_frame = round(start * self.frame_rate / 1000)
            end_frame = round(end * self.frame_rate / 1000)

            data = self._data[start_frame:end_frame]

            return AudioSegment(data.tobytes(), self.frame_rate, self.channels, self.sample_width)
        else:
            raise ValueError("indexing is not supported!")

    
    def get_num_frames(self):
        return self._data.shape[0]
    
    @property
    def data(self):
        """
        Get data in the form of the raw byte data
        """
        return self._data.tobytes()
    
    def fade(self, to_gain=0, from_gain=0, start=0, duration="end"):

        if to_gain == 0 and from_gain == 0:
            return self

        to_amp = db_to_amp(to_gain)
        from_amp = db_to_amp(from_gain)

        start_frame = round(start * self.frame_rate / 1000)

        if duration == "end":
            end_frame = self._data.shape[0]
        else:
            end_frame = min(start_frame + round(duration * self.frame_rate / 1000), self._data.shape[0])
        
        frame_duration = end_frame - start_frame
        
        amp_array = np.linspace(from_amp, to_amp, num=frame_duration, endpoint=True)

        self._data[start_frame:end_frame] = np.clip(self._data[start_frame:end_frame].T * amp_array, a_min=self.min_val, a_max=self.max_val).T.astype(self.dt)

        return self
    
    def reverse(self):
        self._data = np.flip(self._data, axis=0)
        return self