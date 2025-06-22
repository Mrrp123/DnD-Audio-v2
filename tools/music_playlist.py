import os
from threading import Thread
import yaml
from datetime import datetime
from mutagen.mp3 import MP3
from mutagen.oggvorbis import OggVorbis
from mutagen.wave import WAVE
from mutagen.id3 import ID3
from mutagen.id3._util import ID3NoHeaderError
from PIL import Image, ImageOps, UnidentifiedImageError
from io import BytesIO
from ast import literal_eval
import random
import hashlib
from typing import TypeVar, Generic, TypedDict
K = TypeVar("K")
V = TypeVar("V")



from kivy.utils import platform

if platform == "android":
    from jnius import autoclass
    AUDIO_API = "audiotrack"


if platform in ('win', 'linux', 'linux2', 'macos'):
    import miniaudio
    AUDIO_API = "miniaudio"

import tools.common_vars as common_vars

class DatabaseFormatError(Exception):
    pass

class UpdatingDict(dict[K, V], Generic[K, V]):
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
        super().__setitem__(key, value)
        if not self.setup:
            if isinstance(self.parent, UpdatingDict):
                self.parent.__setitem__(self.parent_key, self)
            elif not self.locked:
                Thread(target=self.update_file(), daemon=False).start()
    
    def update(self, *args, **kwargs):
        for key, value in dict(*args, **kwargs).items():
            self[key] = value
            
    def update_file(self):
        with open(common_vars.music_database_path, "w") as fp:
            yaml.safe_dump(literal_eval(str(self)), stream=fp, sort_keys=False)

class TrackInfo(TypedDict):
    id: int
    persistent_id: str
    file: str
    length: int
    size: int
    rate: int
    date_added: float
    date_modified: float
    bit_rate: int
    name: str
    artist: str
    cover: str
    album: str
    genre: str
    year: int | None
    bpm: int | None
    play_count: int
    play_date: float

class PlaylistInfo(TypedDict):
    id: int
    persistent_id: str
    name: str
    track_list: list[int]


class MusicDatabase():
    """
    Creates a database that keeps track of songs, playlists, etc.

    Format:

    {
        "tracks" : {
            id (int) : {
                "id" : numerical id (int),
                "persistent_id" : unique id based on the sha256 hash of the audio file (str),
                "file" : Path to audio file (str),
                "length" : Length of audio file (int) [frames],
                "size" : File size of audio file (int) [bytes],
                "rate" : Sampling rate of file (int) [Hz],
                "date_added" : UTC timestamp when song was first added to database (float) [sec],
                "date_modified" : UTC timestamp of the last time the file was modified (float) [sec],
                "bit_rate" : Bit rate of file if applicable (int) [kbps],
                "name" : Song title (str),
                "artist" : Artist name (str),
                "cover" : Path to album cover file (str),
                "album" : Album name (str),
                "genre" : Music genre (str),
                "year" : Release year (int),
                "bpm" : Beats per minute (int),
                "play_count" : Number of time the song has been listened to (int),
                "play_date" : UTC timestamp of when the song was last played (float) [sec]
                }
            }
        "playlists" : {
            id (int) : {
                "id" : numerical id (int),
                "persistent_id" : unique id based on the sha256 hash of name and creation date (str),
                "name" : Playlist title (str),
                "track_list" : A list of all the track ids contained in the playlist (list[int])
                }
            }
    }
    """

    def __init__(self, database_file) -> None:
        
        # I don't like how lists look in yaml :( so I make them inline
        yaml.SafeDumper.add_representer(list, lambda dumper, data : dumper.represent_sequence('tag:yaml.org,2002:seq', data, flow_style=True))

        try:
            self.data: UpdatingDict[str, UpdatingDict[int, TrackInfo|PlaylistInfo]] = UpdatingDict(self.load_yaml(database_file))
            self.track_pointer = list(self.data["tracks"].keys())[0] # id pointer to a song in self.database["tracks"]
            self.valid_pointers = list(self.data["tracks"].keys())
            
            # We don't want to modify self.valid_pointers directly when we shuffle, make an identical copy that we can use for later
            self.shuffled_valid_pointers = list(self.data["tracks"].keys())
        
        except OSError:
            self.data = {}
            self.track_pointer = None
            self.valid_pointers = []
            self.shuffled_valid_pointers = []

        self.repeat = False
        self._shuffle = False # Don't modify this directly
    
    def __len__(self):
        try:
            return len(self.data["tracks"].keys())
        except KeyError:
            return 0
    
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
        try:
            with open(database_file, "r") as fp:
                yaml_object = yaml.safe_load(fp)
        except FileNotFoundError:
            yaml_object = None

        if yaml_object is None:
            raise OSError(f"No yaml data in {database_file}")
        self._verify_format(yaml_object)

        return yaml_object
    
    @staticmethod
    def _verify_format(yaml_object: dict[str, dict[int, dict[str, int|float|str|None]]]):

        required_base_keys = ("tracks", "playlists") # playlists keys can be empty, but it still needs to be there

        required_track_keys = ("id", "persistent_id", "file", "length", 
                               "size", "rate", "date_added", "date_modified", 
                               "bit_rate", "name", "artist", "cover", "album", 
                               "genre", "year", "bpm", "play_count", "play_date")
        
        required_playlist_keys = ("id", "persistent_id", "name", "track_list")
        
        track_key_types = {
            "id": int,
            "persistent_id": str,
            "file": str,
            "length": int,
            "size": int,
            "rate": int,
            "date_added": float,
            "date_modified": float,
            "bit_rate": int,
            "name": str,
            "artist": str,
            "cover": str,
            "album": str,
            "genre": str,
            "year": int | None,
            "bpm": int | None,
            "play_count": int,
            "play_date": float,
        }

        playlist_key_types = {
            "id": int,
            "persistent_id": str,
            "name": str,
            "track_list" : list,
            # track_list_ids : int (I can't set track_list to be list[int], so check the actual ids seperately)
        }

        

        if not isinstance(yaml_object, dict):
            raise DatabaseFormatError("MusicDatabase is not a dictionary at the tree level")
        
        for key in required_base_keys:
            if key not in yaml_object.keys():
                raise DatabaseFormatError(f"MusicDatabase is missing required tree level key: {key}")
            if not isinstance(yaml_object[key], dict):
                raise DatabaseFormatError(f"MusicDatabase['tracks'][{key}] is not a dictionary")
                    
        for track_id in yaml_object["tracks"].keys():
            if not isinstance(track_id, int):
                raise DatabaseFormatError(f"MusicDatabase['tracks'] key: {track_id} is not an integer")
            if not all(key in yaml_object["tracks"][track_id].keys() for key in required_track_keys):
                missing_keys = [key for key in required_track_keys if key not in yaml_object["tracks"][track_id].keys()]
                raise DatabaseFormatError(f"MusicDatabase is missing required track_id level key(s): {missing_keys}")
            try:
                if not all(isinstance(value, track_key_types[key]) for key, value in yaml_object["tracks"][track_id].items()):
                    incorrect_keys = [key for key, value in yaml_object["tracks"][track_id].items() if not isinstance(value, track_key_types[key])]
                    raise DatabaseFormatError(f"MusicDatabase['tracks'][{track_id}] contains key(s) with incorrect type(s): {incorrect_keys}")
            except KeyError:
                extraneous_keys = [key for key in yaml_object["tracks"][track_id].keys() if key not in required_track_keys]
                raise DatabaseFormatError(f"MusicDatabase['tracks'][{track_id}] contains extraneous key(s): {extraneous_keys}")
            if yaml_object["tracks"][track_id]["id"] != track_id:
                raise DatabaseFormatError(f"MusicDatabase['tracks'][{track_id}]['id'] ({yaml_object["tracks"][track_id]["id"]}) doesn't match track_id!")

        if len(yaml_object["playlists"].keys()) != 0:
            for playlist_id in yaml_object["playlists"].keys():
                if not isinstance(playlist_id, int):
                    raise DatabaseFormatError(f"MusicDatabase['playlists'] key: {playlist_id} is not an integer")
                if not all(key in yaml_object["playlists"][playlist_id].keys() for key in required_playlist_keys):
                    missing_keys = [key for key in required_playlist_keys if key not in yaml_object["playlists"][playlist_id].keys()]
                    raise DatabaseFormatError(f"MusicDatabase is missing required playlist_id level key(s): {missing_keys}")
                try:
                    if not all(isinstance(value, playlist_key_types[key]) for key, value in yaml_object["playlists"][playlist_id].items()):
                        incorrect_keys = [key for key, value in yaml_object["playlists"][playlist_id].items() if not isinstance(value, track_key_types[key])]
                        raise DatabaseFormatError(f"MusicDatabase['playlists'][{playlist_id}] contains key(s) with incorrect type(s): {incorrect_keys}")
                except KeyError:
                    extraneous_keys = [key for key in yaml_object["playlists"][playlist_id].keys() if key not in required_playlist_keys]
                    raise DatabaseFormatError(f"MusicDatabase['playlists'][{playlist_id}] contains extraneous key(s): {extraneous_keys}")
                if not all(isinstance(track_id, int) for track_id in yaml_object["playlists"][playlist_id]["track_list"]):
                    incorrect_ids = [track_id for track_id in yaml_object["playlists"][playlist_id]["track_list"] if not isinstance(track_id, int)]
                    raise DatabaseFormatError(f"MusicDatabase['playlists'][{playlist_id}]['track_list'] contains track_id(s) with incorrect type(s): {incorrect_ids}")
                if not all(track_id in yaml_object["tracks"].keys() for track_id in yaml_object["playlists"][playlist_id]["track_list"]):
                    invalid_ids = [track_id for track_id in yaml_object["playlists"][playlist_id]["track_list"] if not (track_id in yaml_object["tracks"].keys())]
                    raise DatabaseFormatError(f"MusicDatabase['playlists'][{playlist_id}]['track_list'] contains invalid track_id(s): {invalid_ids}")
                if yaml_object["playlists"][playlist_id]["id"] != playlist_id:
                    raise DatabaseFormatError(f"MusicDatabase['playlists'][{playlist_id}]['id'] ({yaml_object["playlists"][playlist_id]["id"]}) doesn't match playlist_id!")
            

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
    
    @staticmethod
    def _get_hash_from_file(file):
        buf_size = 65536
        sha256 = hashlib.sha256()
        with open(file, "rb") as fp:
            while True:
                data = fp.read(buf_size)
                if not data:
                    break
                sha256.update(data)
        hash_out = sha256.hexdigest()
        # Truncate and XOR our hash so we don't use so much path length
        return hex(int(hash_out[:32], 16) ^ int(hash_out[32:], 16))[2:]

    @staticmethod
    def _get_hash_from_args(*args):
        sha256 = hashlib.sha256()
        for arg in args:
            sha256.update(arg)
        hash_out = sha256.hexdigest()
        return hex(int(hash_out[:32], 16) ^ int(hash_out[32:], 16))[2:]
    
    def cache_covers(self, track_ids=None):

        # Create cache folder if it doesn't exist
        os.makedirs("./cache/covers", exist_ok=True)
        os.makedirs("./cache/small_covers", exist_ok=True)
        os.makedirs("./cache/audio", exist_ok=True)
        if track_ids is None:
            track_ids = self.data["tracks"].keys()
        for track_id in track_ids:
            persistent_id = self.data["tracks"][track_id]["persistent_id"]
            cover = self.data["tracks"][track_id]["cover"]
            if os.path.isfile(cover) and os.path.samefile(cover, f"{common_vars.app_folder}/assets/covers/default_cover.png"):
                continue
            elif (not os.path.isfile(cached_img := f"{common_vars.app_folder}/cache/covers/{persistent_id}.jpg")
                and os.path.isfile(cover)):
                try:
                    metadata = ID3(cover)
                    if (apic := metadata.getall("APIC")) != []:
                        img_data = apic[0].data
                except ID3NoHeaderError:
                    try:
                        with open(cover, "rb") as fp:
                            img_data = fp.read()
                    except OSError:
                        print(f"Failed to read image for track {track_id}!")
                        continue
                try:
                    img: Image.Image = Image.open(BytesIO(img_data))
                except UnidentifiedImageError:
                    print(f"Cannot identify image file: {cover}")
                    continue
                try:
                    with open(cached_img, "wb") as fp:
                        fp.write(img_data)
                    if img.mode in ("RGBA", "P"): img = img.convert("RGB")
                    img = ImageOps.contain(img, (128,128), Image.Resampling.LANCZOS)
                    img.save(f"{common_vars.app_folder}/cache/small_covers/{persistent_id}.jpg", "JPEG", quality=100)
                except OSError:
                    print(f"Failed to save image for track {track_id}!")

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
        
        if len(self) != 0:
            new_id = max(self.valid_pointers) + 1
        else:
            new_id = 1
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

        new_dict_entry = {
            "id" : new_id,
            "persistent_id" : self._get_hash_from_file(file),
            "file" : os.path.normpath(file),
            "length" : track_info["length"],
            "size" : os.path.getsize(file),
            "rate" : track_info["rate"],
            "date_added" : datetime.now().timestamp(),
            "date_modified" : os.path.getmtime(file),
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
        }

        if len(self) != 0:
            self.data.locked = True
            self.data["tracks"][new_id] = 1 # Add new key to UpdatingDict with temp bogus entry
            self.data.locked = False
            self.data["tracks"][new_id] = UpdatingDict(new_dict_entry, parent=self.data["tracks"], parent_key=new_id)
            self.data.locked = True
        
        else:
            new_dict_entry = {"tracks" : {new_id : new_dict_entry}, "playlists" : {}}
            self.data = UpdatingDict(new_dict_entry)
            self.data.update_file()

        # Reload list of pointers
        self.valid_pointers = list(self.data["tracks"].keys())
        self.shuffled_valid_pointers = list(self.data["tracks"].keys())

        # Add any cover art to the cache
        self.cache_covers(track_ids=[new_id])
    
    def set_track(self, track_id):
        if track_id in self.valid_pointers:
            self.track_pointer = track_id
    
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
    
    def add_playlist(self, playlist_name: str, track_list: list[int] | None = None):

        if len(self.data["playlists"].keys()) != 0:
            new_id = max(self.data["playlists"].keys()) + 1
        else:
            new_id = 1

        if track_list is None:
            track_list = []
        
        new_dict_entry = {
            "id" : new_id,    # I dunno, come up with something to base the hash on
            "persistent_id" : self._get_hash_from_args(playlist_name.encode("utf-8"), str(datetime.now().timestamp()).encode("utf-8")),
            "name" : playlist_name,
            "track_list" : track_list
        }

        # Not sure how to handle trying to create a playlist with no songs to choose from, for now just raise error
        if len(self) != 0:
            self.data.locked = True
            self.data["playlists"][new_id] = 1 # Add new key to UpdatingDict with temp bogus entry
            self.data.locked = False
            self.data["playlists"][new_id] = UpdatingDict(new_dict_entry, parent=self.data["playlists"], parent_key=new_id)
            self.data.locked = True
        else:
            raise ValueError("Attempting to create a playlist without any tracks!")
        