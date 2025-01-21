import os
from threading import Thread
import yaml
from datetime import datetime
import miniaudio
from mutagen.id3 import ID3
from mutagen.id3._util import ID3NoHeaderError
from ast import literal_eval

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

    def __setitem__(self, key, value):
        if value:
            super().__setitem__(key, value)
            if not self.setup:
                if isinstance(self.parent, UpdatingDict):
                    self.parent.__setitem__(self.parent_key, self)
                else:
                    Thread(target=self.update(), daemon=False).start()
        else:
            raise ValueError("Value is empty!")
            
    def update(self):
        with open(common_vars.music_database_path, "w") as fp:
            fp.write(yaml.safe_dump(literal_eval(str(self)), sort_keys=False))

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
    
    def __len__(self):
        return len(self.data["tracks"].keys())
    
    def __lshift__(self, left):
        current_pos = self.valid_pointers.index(self.track_pointer)
        self.track_pointer = self.valid_pointers[(current_pos - (left % len(self.valid_pointers))) % len(self.valid_pointers)]
        return self.data["tracks"][self.track_pointer]
        
    def __rshift__(self, right):
        current_pos = self.valid_pointers.index(self.track_pointer)
        self.track_pointer = self.valid_pointers[(current_pos + (right % len(self.valid_pointers))) % len(self.valid_pointers)]
        return self.data["tracks"][self.track_pointer]
    
    def __add__(self, value):
        if isinstance(value, int):
            self.data["tracks"][self.track_pointer]["play_count"] += 1
    
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

    def next(self):
        return self >> 1

    def previous(self):
        return self << 1
    
    @staticmethod
    def get_song_cover(file):
        asset_folder = f"{common_vars.app_folder}/assets/covers"
        name = os.path.split(file)[-1].split(".")[0]
        try:
            metadata = ID3(file)
            if metadata.getall("APIC") != []:
                return file
        except ID3NoHeaderError:
            pass

        if os.path.isfile(f"{asset_folder}/{name}.png"):
            return f"{asset_folder}/{name}.png"
        
        elif os.path.isfile(f"{asset_folder}/{name}.jpg"):
            return f"{asset_folder}/{name}.jpg"
        
        else:
            return f"{asset_folder}/default_cover.png"

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

        if name == "":
            name = os.path.split(file)[-1].split(".")[0]
        if cover == "":
            cover = self.get_song_cover(file)

        self.data["tracks"][new_id] = {
            "id" : new_id,
            "file" : os.path.normpath(file),
            "length" : track_info.num_frames,
            "size" : os.path.getsize(file),
            "rate" : track_info.sample_rate,
            "date_added" : datetime.now().timestamp(),
            "date_modified" : datetime.now().timestamp(),
            "bit_rate" : round((os.path.getsize(file)-44) * 8 / (track_info.num_frames / track_info.sample_rate) / 1000),
            "name" : name,
            "artist" : artist,
            "cover" : cover,
            "album" : album,
            "genre" : genre,
            "year" : year,
            "bpm" : bpm,
            "play_count" : 0,
            "play_date" : datetime.now().timestamp()
        }
    
    def set_track(self, file):
        if file is not None:
            for track in self.data["tracks"].keys():
                if os.path.samefile(file, self.data["tracks"][track]["file"]):
                    self.track_pointer = track
    
    def get_track(self):
        return self.data["tracks"][self.track_pointer]

    @staticmethod
    def get_track_info(file: str):
        file_ext = os.path.splitext(file)[1].lower()

        if not file.isascii():
            hard_link_path = f"{common_vars.app_folder}/cache/audio/temp"
            if os.path.exists(hard_link_path):
                os.remove(hard_link_path)
            os.link(file, hard_link_path)
            file = hard_link_path

        try:
            if file_ext.lower() == ".wav":
                info = miniaudio.wav_get_file_info(file)

            elif file_ext.lower() == ".ogg":
                info = miniaudio.vorbis_get_file_info(file)
            
            elif file_ext.lower() == ".mp3":
                info = miniaudio.mp3_get_file_info(file)
            
            else:
                raise ValueError("Unsupported file type!")
            
        except miniaudio.DecodeError:
            raise miniaudio.DecodeError(f"Failed to get frame count for {file}")
        
        finally:
            if os.path.exists(hard_link_path):
                os.remove(hard_link_path)
        
        return info