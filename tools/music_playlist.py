import os
from threading import Thread
import json
import yaml
import wave
from datetime import datetime
import ffmpeg
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

    def load_json(self, database_file):
        """
        Loads the json database into the playlist
        """
        try:
            with open(database_file, "r") as fp:
                json_object = json.load(fp)
                # yaml_object = yaml.safe_load(fp)
        except FileNotFoundError:
            pass

        # a = datetime.now().timestamp()
        # print(a, type(a))
        # exit()

        new_obj = {"tracks" : {i : 
                   {"id" : i,
                    "file" : os.path.normpath(dict_obj["file"]),
                    "length" : self.get_song_length(dict_obj["file"])[1],
                    "size" : os.path.getsize(dict_obj["file"]),
                    "rate" : 44_100,
                    "date_added" : datetime.now().timestamp(),
                    "date_modified" : datetime.now().timestamp(),
                    "bit_rate" : round((os.path.getsize(dict_obj["file"])-44) * 8 / (self.get_song_length(dict_obj["file"])[1] / 44_100) / 1000),
                    "name" : dict_obj["song"],
                    "artist" : "",
                    "cover" : dict_obj["cover"],
                    "album" : "",
                    "genre" : "",
                    "year" : None,
                    "bpm" : None,
                    "play_count" : 0,
                    "play_date" : datetime.now().timestamp()
                    } 
                   for i, dict_obj in enumerate(json_object)}}
        with open("test_music_data.yaml", "w") as fp:
            fp.write(yaml.safe_dump(new_obj, sort_keys=False))
        print(json_object[0])
        return json_object
    
    def load_yaml(self, database_file):
        with open(database_file, "r") as fp:
            yaml_object = yaml.safe_load(fp)
        return yaml_object

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

        if name == "":
            name = os.path.split(file)[-1].split(".")[0]
        if cover == "":
            cover = self.get_song_cover(file)

        self.data["tracks"][new_id] = {
            "id" : new_id,
            "file" : os.path.normpath(file),
            "length" : self.get_track_length(file)[1],
            "size" : os.path.getsize(file),
            "rate" : 44_100,
            "date_added" : datetime.now().timestamp(),
            "date_modified" : datetime.now().timestamp(),
            "bit_rate" : round((os.path.getsize(file)-44) * 8 / (self.get_track_length(file)[1] / 44_100) / 1000),
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
    def get_track_length(song):
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
        
        elif file_type == ".mp3":
            ffmpeg_process = (ffmpeg
                    .input(song)
                    .output("-", format="s16le", acodec="pcm_s16le", ac=2, ar="44100")
                    .global_args('-loglevel', 'error')
                    .global_args('-y')
                    .run_async(pipe_stdout=True)
            )
            bytes_read = 0
            eof = False
            while not eof:
                byte_data = ffmpeg_process.stdout.read(8820)
                if byte_data == bytes(0):
                    eof = True
                    break
                bytes_read += len(byte_data)

            num_frames = bytes_read // 4
            if num_frames % 4 != 0:
                raise ValueError(f"Incorrect format for audio data for {song}")

        return (num_frames / 44100 * 1000), num_frames