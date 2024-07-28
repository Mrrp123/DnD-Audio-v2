import os
import io
import csv

import tools.common_vars as common_vars


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

    def next(self):
        return self >> 1

    def previous(self):
        return self << 1

    def get_playlist(self, _type=None):
        if _type == "string":
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=self.playlist[0].keys(), delimiter='\t')
            writer.writerows(self.playlist)
            return output.getvalue()
                
        return self.playlist
    
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
                if os.path.samefile(file, data["file"]):
                    self._pos = i
                    #return self[i]
    
    def get_song(self, _type=None):

        if _type == "string":
            return "\t".join(self.playlist[self._pos].values())

        return self.playlist[self._pos]