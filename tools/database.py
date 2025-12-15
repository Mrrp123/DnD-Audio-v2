from __future__ import annotations
from io import BufferedReader
import struct

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tools.music_playlist import UpdatingDict, TrackInfo, PlaylistInfo

DB_VERSION = 20251215

class DatabaseFormatError(Exception):
    pass

def encode_ULEB128_int(value: int):
    byte_array_out = bytearray()
    if not value:
        return bytes([0x00])
    while value:
        byte = value & 0x7f
        value >>= 7
        if value:
            byte |= 0x80
        byte_array_out.append(byte)
    return bytes(byte_array_out)

def decode_ULEB128_int(fp: BufferedReader):
    result = 0
    shift = 0
    byte = 0x80
    while (byte & 0x80) != 0:
        byte = int.from_bytes(fp.read(1), "little")
        result |= ((byte & 0x7f) << shift)
        shift += 7
    return result

def string_to_bytes(string: str):
    if len(string) == 0:
        return bytes([0x00])
    else:
        return bytes([0x0b, *encode_ULEB128_int(len(string.encode("utf-8"))), *string.encode("utf-8")])

def bytes_to_string(fp: BufferedReader):
    if (byte := fp.read(1)) == bytes([0x0b]):
        return fp.read(decode_ULEB128_int(fp)).decode("utf-8")
    elif byte == bytes([0x00]):
        return ""
    else:
        raise ValueError(f"Unknown byte encountered while parsing string: 0x{int.from_bytes(byte, 'little'):02X}")

def load_db(database_file):
    """
    Type definitions:
    
      short : 2 byte integer. Unsigned unless specified otherwise
        int : 4 byte integer. Unsigned unless specified otherwise
       long : 8 byte integer. Unsigned unless specified otherwise
    ULEB128 : Variable length unsigned integer
     double : 64-bit IEEE floating point value
     string : Contains three parts. If the first byte is 0x00, then it is an empty string
              If the first byte is 0x0b, then the next bytes will be a ULEB128 integer 
              representing the byte length of the string. After this isthe utf-8 encoded 
              string itself
    
    Types followed by a [] indicates a contiguous array of the type (ex: int[] is an array of ints)
    
    DBTrackInfo:
    Each track info object has a set of values associated with them, encoded in the following order:

             Name | Type   | Description [units]
    -------------------------------------------------------------------------------------
               id | int    | Numerical id
           length | long   | Length of audio file [frames]
             size | long   | File size of audio file [bytes]
             rate | int    | Sample rate of file [Hz]
         bit_rate | short  | Encoded bitrate of file [kbps]
             year | short  | Release year
              bpm | short  | Beats per minute [minute^-1]
       play_count | int    | Count of the number of times this track has been played
       date_added | double | UTC timestamp when song was first added to database [sec]
    date_modified | double | UTC timestamp of the last time the file was modified [sec]
        play_date | double | UTC timestamp of when the song was last played [sec]
    persistent_id | string | Unique id based on the sha256 hash of the audio file
             file | string | Path to audio file
             name | string | Song title
           artist | string | Artist name
            cover | string | Path to album cover file
            album | string | Album name
            genre | string | Music genre
    -------------------------------------------------------------------------------------

    DBPlaylistInfo:
    Each playlist info object has a set of values associated with them, encoded in the following order:

             Name | Type    | Description [units]
    --------------------------------------------------------------------------------------
               id | int     | Numerical id
    persistent_id | string  | Unique id based on the sha256 hash of name and creation date
             name | string  | Playlist title
            count | ULEB128 | Count of the number of tracks in track_list
       track_list | int[]   | An array of all the track ids contained in the playlist
    --------------------------------------------------------------------------------------
    
    DBTrackHistory:
    A historical record of when songs were played. Each track history object has a set of values associated 
    with them, encoded in the following order:

             Name | Type     | Description [units]
    ---------------------------------------------------------------------------------------
               id | int      | Numerical id (Same as track's id)
    persistent_id | string   | Unique id (Same as track's persistent id)
            count | ULEB128  | Count of the number of historical records
       play_dates | double[] | An array of UTC timestamps of when the song was played [sec]
    ---------------------------------------------------------------------------------------

    DB Format:
    
    music_data.db stores all the information about tracks, playlists, etc for the program in a custom
    database file. Encoded in the following order:

             Name | Type             | Description [units]
    ------------------------------------------------------------------------------------
       DB_VERSION | int              | Version number of the database format
       num_tracks | int              | Number of tracks present in the database
    num_playlists | int              | Number of playlists present in the database
    num_histories | int              | Number of history objects present in the database
           tracks | DBTrackInfo[]    | Aforementioned DBTrackInfo objects
        playlists | DBPlaylistInfo[] | Aforementioned DBPlaylistInfo objects
        histories | DBTrackHistory[] | Aforementioned DBTrackHistory objects
    ------------------------------------------------------------------------------------

    Output Format:
    Type definitions for the output format are default python types. 

    db_data (dict) = {
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
            },
        "playlists" : {
            id (int) : {
                "id" : numerical id (int),
                "persistent_id" : unique id based on the sha256 hash of name and creation date (str),
                "name" : Playlist title (str),
                "track_list" : A list of all the track ids contained in the playlist (list[int])
                }
            },
        "histories" : {
            id (int) : {
                "id" : numerical id (Same as track's id) (int),
                "persistent_id" : unique id (Same as track's persistent id) (str),
                "play_dates" : A list of UTC timestamps of when the song was played (list[float]) [sec],
                }
            }
    }
    """
    
    def load_v20251215(fp: BufferedReader):

        db_data = {"tracks" : {}, "playlists" : {}, "histories" : {}}

        num_tracks = int.from_bytes(fp.read(4), "little")
        num_playlists = int.from_bytes(fp.read(4), "little")
        num_histories = int.from_bytes(fp.read(4), "little")

        for i in range(num_tracks):
            track_id = int.from_bytes(fp.read(4), "little")
            length = int.from_bytes(fp.read(8), "little")
            size = int.from_bytes(fp.read(8), "little")
            rate = int.from_bytes(fp.read(4), "little")
            bit_rate = int.from_bytes(fp.read(2), "little")
            year = int.from_bytes(fp.read(2), "little")
            bpm = int.from_bytes(fp.read(2), "little")
            play_count = int.from_bytes(fp.read(4), "little")

            date_added, date_modified, play_date = struct.unpack("3d", fp.read(24))

            persistent_id = bytes_to_string(fp)
            file = bytes_to_string(fp)
            name = bytes_to_string(fp)
            artist = bytes_to_string(fp)
            cover = bytes_to_string(fp)
            album = bytes_to_string(fp)
            genre = bytes_to_string(fp)

            db_data["tracks"][track_id] = {
                "id" : track_id,
                "persistent_id" : persistent_id,
                "file" : file,
                "length" : length,
                "size" : size,
                "rate" : rate,
                "date_added" : date_added,
                "date_modified" : date_modified,
                "bit_rate" : bit_rate,
                "name" : name,
                "artist" : artist,
                "cover" : cover,
                "album" : album,
                "genre" : genre,
                "year" : year,
                "bpm" : bpm,
                "play_count" : play_count,
                "play_date" : play_date
            }
        
        for j in range(num_playlists):
            playlist_id = int.from_bytes(fp.read(4), "little")
            persistent_id = bytes_to_string(fp)
            name = bytes_to_string(fp)
            track_list = [int.from_bytes(fp.read(4), "little") for _ in range(decode_ULEB128_int(fp))]
            
            db_data["playlists"][playlist_id] = {
                "id" : playlist_id,
                "persistent_id" : persistent_id,
                "name" : name,
                "track_list" : track_list
            }
        
        for k in range(num_histories):
            track_id = int.from_bytes(fp.read(4), "little")
            persistent_id = bytes_to_string(fp)
            num_entries = decode_ULEB128_int(fp)
            play_dates = list(struct.unpack(f"{num_entries}d", fp.read(num_entries*8)))

            db_data["histories"][track_id] = {
                "id" : track_id,
                "persistent_id" : persistent_id,
                "play_dates" : play_dates
            }
        
        if (byte := fp.read(1)):
            raise DatabaseFormatError(f"Unexpected byte(s) at EOF: 0x{int.from_bytes(byte):02X}...")
        
        return db_data

    with open(database_file, "rb") as fp:
        version = int.from_bytes(fp.read(4), "little")

        if version == 20251215:
            return load_v20251215(fp)
        else:
            raise DatabaseFormatError(f"Unknown database version: {version}")
    
def save_db(database_file, data: UpdatingDict[str, UpdatingDict[int, TrackInfo|PlaylistInfo]]):
    """
    See load_db for file format specifications
    """

    with open(database_file, "wb") as fp:

        fp.write(DB_VERSION.to_bytes(4, "little"))
        fp.write(len(data["tracks"].keys()).to_bytes(4, "little"))
        fp.write(len(data["playlists"].keys()).to_bytes(4, "little"))
        fp.write(len(data["histories"].keys()).to_bytes(4, "little"))

        for track_id in data["tracks"].keys():
            fp.write(data["tracks"][track_id]["id"].to_bytes(4, "little"))
            fp.write(data["tracks"][track_id]["length"].to_bytes(8, "little"))
            fp.write(data["tracks"][track_id]["size"].to_bytes(8, "little"))
            fp.write(data["tracks"][track_id]["rate"].to_bytes(4, "little")),
            fp.write(data["tracks"][track_id]["bit_rate"].to_bytes(2, "little"))
            fp.write(data["tracks"][track_id]["year"].to_bytes(2, "little"))
            fp.write(data["tracks"][track_id]["bpm"].to_bytes(2, "little"))
            fp.write(data["tracks"][track_id]["play_count"].to_bytes(4, "little"))

            fp.write(
                struct.pack("3d", 
                data["tracks"][track_id]["date_added"],
                data["tracks"][track_id]["date_modified"],
                data["tracks"][track_id]["play_date"])
            )

            fp.write(string_to_bytes(data["tracks"][track_id]["persistent_id"]))
            fp.write(string_to_bytes(data["tracks"][track_id]["file"]))
            fp.write(string_to_bytes(data["tracks"][track_id]["name"]))
            fp.write(string_to_bytes(data["tracks"][track_id]["artist"]))
            fp.write(string_to_bytes(data["tracks"][track_id]["cover"]))
            fp.write(string_to_bytes(data["tracks"][track_id]["album"]))
            fp.write(string_to_bytes(data["tracks"][track_id]["genre"]))
        
        for playlist_id in data["playlists"].keys():
            fp.write(data["playlists"][playlist_id]["id"].to_bytes(4, "little"))
            fp.write(string_to_bytes(data["playlists"][playlist_id]["persistent_id"]))
            fp.write(string_to_bytes(data["playlists"][playlist_id]["name"]))
            fp.write(encode_ULEB128_int(len(data["playlists"][playlist_id]["track_list"])))

            track_list_bytearray = bytearray()
            for track_id in data["playlists"][playlist_id]["track_list"]:
                track_list_bytearray.extend(track_id.to_bytes(4, "little"))
            fp.write(track_list_bytearray)
        
        for track_id in data["histories"].keys():
            fp.write(data["histories"][track_id]["id"].to_bytes(4, "little"))
            fp.write(string_to_bytes(data["histories"][track_id]["persistent_id"]))
            fp.write(
                struct.pack(f"{len(data["histories"][track_id]["play_dates"])}d", 
                *data["histories"][track_id]["play_dates"])
                )