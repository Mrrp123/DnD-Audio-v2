import os
from threading import Thread
import yaml
from datetime import datetime
from mutagen.mp3 import MP3
from mutagen.oggvorbis import OggVorbis
from mutagen.wave import WAVE
from ast import literal_eval
import random

from kivy.utils import platform

if platform == "android":
    from jnius import autoclass
    AUDIO_API = "audiotrack"


if platform in ('win', 'linux', 'linux2', 'macos'):
    import miniaudio
    AUDIO_API = "miniaudio"

import tools.common_vars as common_vars

class UpdatingDict(dict):
    """
    Dictionary that updates the database whenever modified
    """
    def __init__(self, *args, parent=None, parent_key=None, **kwargs):
        self.setup = True
        if (parent is None) != (parent_key is None):
            raise ValueError("parent and parent_key must be either both be None, or neither be None!")
        elif parent is not None:
            if isinstance(parent, UpdatingDict) and parent_key in parent.keys():
                self.parent: UpdatingDict = parent
                self.parent_key = parent_key
            else:
                raise TypeError(f"parent must be an UpdatingDict with key of {parent_key}!")
        else:
            self.parent = None
            self.parent_key = None
            
        super().__init__(*args, **kwargs)
        for key in self.keys():
            if isinstance(self[key], dict):
                self[key] = UpdatingDict(self[key], parent=self, parent_key=key)
        self.setup = False
        self.locked = True # Won't cause any writes if locked

    def __setitem__(self, key, value):
        if value:
            super().__setitem__(key, value)
            if not self.setup:
                if isinstance(self.parent, UpdatingDict):
                    self.parent.__setitem__(self.parent_key, self)
                elif not self.locked:
                    Thread(target=self.update(), daemon=False).start()
        else:
            raise ValueError("Value is empty!")
            
    def update(self):
        with open(common_vars.music_database_path, "w") as fp:
            yaml.safe_dump(literal_eval(str(self)), stream=fp, sort_keys=False)

class MusicDatabase():
    """
    Creates a database that keeps track of songs, playlists, etc.

    Format:

    {
        "tracks" : {
            id (int) : {
                "id" : unique numerical id (int)
                "file" : Path to audio file (str),
                "length" : Length of audio file (int) [frames],
                "size" : File size of audio file (int) [bytes]
                "rate" : Sampling rate of file (int) [Hz]
                "date_added" : UTC timestamp when song was first added to database (float) [sec],
                "date_modified" : UTC timestamp of the last time the file was modified (float) [sec],
                "bit_rate" : Bit rate of file if applicable (int) [kbps]
                "name" : Song title (str),
                "artist" : Artist name (str),
                "cover" : Path to album cover file (str)
                "album" : Album name (str),
                "genre" : Music genre (str),
                "year" : Release year (int),
                "bpm" : Beats per minute (int),
                "play_count" : Number of time the song has been listened to (int),
                "play_date" : UTC timestamp of when the song was last played (float) [sec]
                }
            }
    }
    """

    def __init__(self, database_file) -> None:
        self.data = UpdatingDict(self.load_yaml(database_file))
        self.track_pointer = list(self.data["tracks"].keys())[0] # id pointer to a song in self.database["tracks"]
        self.valid_pointers = list(self.data["tracks"].keys())
        
        # We don't want to modify self.valid_pointers directly when we shuffle, make an identical copy that we can use for later
        self.shuffled_valid_pointers = list(self.data["tracks"].keys())
        self.repeat = False
        self._shuffle = False # Don't modify this directly
    
    def __len__(self):
        return len(self.data["tracks"].keys())
    
    def __lshift__(self, left):
        if self._shuffle:
            valid_pointers = self.shuffled_valid_pointers
        else:
            valid_pointers = self.valid_pointers
        if not self.repeat:
            current_pos = valid_pointers.index(self.track_pointer)
            self.track_pointer = valid_pointers[(current_pos - (left % len(valid_pointers))) % len(valid_pointers)]
        return self.data["tracks"][self.track_pointer]
        
    def __rshift__(self, right):
        if self._shuffle:
            valid_pointers = self.shuffled_valid_pointers
        else:
            valid_pointers = self.valid_pointers
        if not self.repeat:
            current_pos = valid_pointers.index(self.track_pointer)
            self.track_pointer = valid_pointers[(current_pos + (right % len(valid_pointers))) % len(valid_pointers)]
        return self.data["tracks"][self.track_pointer]
    
    def update_play_info(self, track_id):
        self.data.locked = True
        self.data["tracks"][track_id]["play_count"] += 1
        self.data.locked = False
        self.data["tracks"][track_id]["play_date"] = datetime.now().timestamp()
        self.data.locked = True
    
    def load_yaml(self, database_file):
        with open(database_file, "r") as fp:
            yaml_object = yaml.safe_load(fp)

        if yaml_object is None:
            raise OSError(f"No yaml data in {database_file}")
        if (err := self._verify_format(yaml_object)):
            raise ValueError(f"Incorrectly formatted MusicDatabase: {err}")
        
        return yaml_object
    
    @staticmethod
    def _verify_format(yaml_object):

        required_base_keys = ("tracks",)

        required_track_keys = ("id", "file", "length", "size", "rate", 
                               "date_added", "date_modified", "bit_rate",
                               "name", "artist", "cover", "album", "genre", 
                               "year", "bpm", "play_count", "play_date")

        if not isinstance(yaml_object, dict):
            return "MusicDatabase is not a dictionary at the tree level"
        
        for key in yaml_object.keys():
            if key not in required_base_keys:
                return f"MusicDatabase is missing required tree level key: {key}"
            if not isinstance(yaml_object[key], dict):
                return f"MusicDatabase['tracks'][{key}] is not a dictionary"
            
        for track_id in yaml_object["tracks"].keys():
            if not isinstance(track_id, int):
                return f"MusicDatabase['tracks'] key: {track_id} is not an integer"
            if not all(key in yaml_object["tracks"][track_id].keys() for key in required_track_keys):
                return f"MusicDatabase is missing required track_id level key: {key}"
            
        return False # no errors

    def set_shuffle_state(self, shuffle):
        self._shuffle = shuffle
        if shuffle:
            random.shuffle(self.shuffled_valid_pointers)

    def next(self):
        return self >> 1

    def peek_right(self, right, key=None):
        """
        Check what the pointer value or key (advanced 'right' many times) is without actively changing to it
        """
        if self._shuffle:
            valid_pointers = self.shuffled_valid_pointers
        else:
            valid_pointers = self.valid_pointers
        if not self.repeat:
            current_pos = valid_pointers.index(self.track_pointer)
            track_pointer = valid_pointers[(current_pos + (right % len(valid_pointers))) % len(valid_pointers)]
        else:
            track_pointer = self.track_pointer
        if key is None:
            return track_pointer
        else:
            return self.data["tracks"][track_pointer][key]

    def previous(self):
        return self << 1
    
    def peek_left(self, left, key=None):
        """
        Check what the pointer value or key (advanced backwards 'left' many times) is without actively changing to it
        """
        if self._shuffle:
            valid_pointers = self.shuffled_valid_pointers
        else:
            valid_pointers = self.valid_pointers
        if not self.repeat:
            current_pos = valid_pointers.index(self.track_pointer)
            track_pointer = valid_pointers[(current_pos - (left % len(valid_pointers))) % len(valid_pointers)]
        else:
            track_pointer = self.track_pointer
        if key is None:
            return track_pointer
        else:
            return self.data["tracks"][track_pointer][key]
    

    def add_track(
            self, 
            file,
            name="",
            artist="",
            cover="",
            album="",
            genre="",
            year=None,
            bpm=None,
            ):
        new_id = max(self.valid_pointers) + 1
        track_info = self.get_track_info(file)

        if name != "":
            track_info["name"] = name
        if album != "":
            track_info["artist"] = artist
        if cover != "":
            track_info["cover"] = cover
        if album != "":
            track_info["album"] = album
        if genre != "":
            track_info["genre"] = genre
        if year is not None:
            track_info["year"] = year
        if bpm is not None:
            track_info["bpm"] = bpm

        self.data.locked = True
        self.data["tracks"][new_id] = 1 # Add new key to UpdatingDict with bogus entry
        self.data.locked = False
        self.data["tracks"][new_id] = UpdatingDict(
        {
            "id" : new_id,
            "file" : os.path.normpath(file),
            "length" : track_info["length"],
            "size" : os.path.getsize(file),
            "rate" : track_info["rate"],
            "date_added" : datetime.now().timestamp(),
            "date_modified" : datetime.now().timestamp(),
            "bit_rate" : track_info["bit_rate"],
            "name" : track_info["name"],
            "artist" : track_info["artist"],
            "cover" : track_info["cover"],
            "album" : track_info["album"],
            "genre" : track_info["genre"],
            "year" : track_info["year"],
            "bpm" : track_info["bpm"],
            "play_count" : 0,
            "play_date" : -1.0
        },
        parent=self.data["tracks"], parent_key=new_id)
        self.data.locked = True

        # Reload list of pointers
        self.valid_pointers = list(self.data["tracks"].keys())
        self.shuffled_valid_pointers = list(self.data["tracks"].keys())
    
    def set_track(self, file):
        if file is not None:
            for track in self.data["tracks"].keys():
                if os.path.samefile(file, self.data["tracks"][track]["file"]):
                    self.track_pointer = track
    
    def get_track(self):
        return self.data["tracks"][self.track_pointer]
    
    def get_track_info(self, file: str):
        if AUDIO_API == "audiotrack":
            track_info, metadata = self._android_get_track_info(file)
        elif AUDIO_API == "miniaudio":
            track_info, metadata = self._miniaudio_get_track_info(file)
        
        if isinstance(metadata, (MP3, WAVE)):
            if metadata.get("APIC:") is not None:
                track_info["cover"] = file
            if metadata.get("TIT2") is not None:
                track_info["name"] = metadata.get("TIT2").text[0]
            if metadata.get("TPE1") is not None:
                track_info["artist"] = metadata.get("TPE1").text[0]
            if metadata.get("TALB") is not None:
                track_info["album"] = metadata.get("TALB").text[0]
            if metadata.get("TCON") is not None:
                track_info["genre"] = metadata.get("TCON").genres[0]
            if metadata.get("TDRC") is not None:
                track_info["year"] = metadata.get("TDRC").text[0].year
            if metadata.get("TBPM") is not None:
                track_info["bpm"] = +metadata.get("TBPM")
        
        elif isinstance(metadata, OggVorbis):
            if metadata.get("METADATA_BLOCK_PICTURE") is not None:
                track_info["cover"] = file
            if metadata.get("title") is not None:
                track_info["name"] = metadata.get("title")[0]
            if metadata.get("artist") is not None:
                track_info["artist"] = metadata.get("artist")[0]
            if metadata.get("album") is not None:
                track_info["album"] = metadata.get("album")[0]
            if metadata.get("genre") is not None:
                track_info["genre"] = metadata.get("genre")[0]
            if metadata.get("date") is not None:
                try:
                    track_info["year"] = datetime.fromisoformat(metadata.get("date")[0]).year
                except ValueError:
                    try:
                        track_info["year"] = int(metadata.get("date")[0])
                    except ValueError:
                        pass
            if metadata.get("bpm") is not None: # This isn't typical to have in a vorbis file, but try anyway
                track_info["bpm"] = metadata.get("bpm")[0]

        return track_info

    def _miniaudio_get_track_info(self, file: str):
        file_ext = os.path.splitext(file)[1].lower()
        hard_link_path = f"{common_vars.app_folder}/cache/audio/temp"

        if not file.isascii():
            if os.path.exists(hard_link_path):
                os.remove(hard_link_path)
            os.link(file, hard_link_path)
            file = hard_link_path
        
        # Base dict to fill known info into
        track_info = {
            "length" : None,
            "rate" : None,
            "bit_rate" : None,
            "name" : os.path.split(file)[-1],
            "artist" : "",
            "cover" : "",
            "album" : "",
            "genre" : "",
            "year" : None,
            "bpm" : None,
        }

        try:
            if file_ext == ".wav":
                file_info = miniaudio.wav_get_file_info(file)
                metadata = WAVE(file)

            elif file_ext == ".ogg":
                file_info = miniaudio.vorbis_get_file_info(file)
                metadata = OggVorbis(file)
            
            elif file_ext == ".mp3":
                file_info = miniaudio.mp3_get_file_info(file)
                metadata = MP3(file)
            
            else:
                raise ValueError("Unsupported file type!")
            
        except miniaudio.DecodeError:
            raise miniaudio.DecodeError(f"Failed to get frame count for {file}")
        
        finally:
            if os.path.exists(hard_link_path):
                os.remove(hard_link_path)
                
        track_info["rate"] = file_info.sample_rate
        track_info["length"] = file_info.num_frames
        track_info["bit_rate"] = metadata.info.bitrate // 1000
        
        return track_info, metadata
    
    def _android_get_track_info(self, file: str):
        # For some *obscene* reason, Android's MediaMetadataRetriever class 
        # didn't support something as fucking simple as SAMPLE RATE until 
        # api 31, so we use mutagen for metadata, and MediaExtractor 
        # for sample rate, length and bitrate
        MediaExtractor = autoclass("android.media.MediaExtractor")

        self.media_extractor = MediaExtractor()
        self.media_extractor.setDataSource(file)
        self.track_format = self.media_extractor.getTrackFormat(0)

        # Base dict to fill known info into
        track_info = {
            "length" : None,
            "rate" : None,
            "bit_rate" : None,
            "name" : os.path.split(file)[-1],
            "artist" : "",
            "cover" : "",
            "album" : "",
            "genre" : "",
            "year" : None,
            "bpm" : None,
        }

        # rate and length absolutely must exist, if they don't these will throw errors
        track_info["rate"] = self.track_format.getInteger("sample-rate")
        # This will be off by at most 0.5 us (2 or 3 frames @ 48kHz), which should be good enough, but if it causes problems then this will need to change.
        track_info["length"] = round(self.track_format.getLong("durationUs") * track_info["rate"] / 1_000_000)
        track_info["bit_rate"] = self.track_format.getInteger("bitrate") // 1000
        self.media_extractor.release()

        file_ext = os.path.splitext(file)[1].lower()

        if file_ext == ".wav":
            metadata = WAVE(file)

        elif file_ext == ".ogg":
            metadata = OggVorbis(file)
        
        elif file_ext == ".mp3":
            metadata = MP3(file)
        
        else:
            raise ValueError("Unsupported file type!")
        
        
        return track_info, metadata