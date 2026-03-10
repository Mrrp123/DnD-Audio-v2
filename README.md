# DnD Audio v2
I really don't have a good name for this yet, so call it whatever you want.

# Getting Started
To actually run this program, you'll need python >= 3.10 and the following modules (simply installed via pip)
```text
kivy >= 2.2.0
miniaudio
mutagen
numpy
python-osc
pillow
plyer
```

To run, simply run the main.py file in python.
```bash
python main.py
```

# Using the Program

## Screens
The application has a handful of screen with various options on each. The screen that is visible on startup is known as the 'main' screen. Here you can 
- Pause/unpause/start your music
- Select previous/next track
- Increase/lower the volume
- Enable/disable track shuffle
- Enable/disable repeat
- Reverse the direction the song is played
- And a funny other thing :)

From the top of the main screen, you can navigate to the Settings/Library pages. On the settings page, you can
- Increase/Decrease playback speed
- Increase/Decrease crossfade duration between tracks
- Look at debug info

On the library page (and beyond), you can navigate to your songs and playlists, search through them, and select songs for playback. I think most buttons beyond here should be fairly intuitive (I hope) Yell at me if they're not.

## Adding Songs
Currently, there are two primary ways to add songs. Note, only four types of audio file formats are supported at this time (.wav, .mp3, .flac, and .ogg).

The first way to add songs is to simply drag and drop them into the application window. The songs will be automatically added to the program from there.

The second way to add songs is via the add songs button (The blue plus button on the Songs page of the program). From there your native file explorer application will open and you will be able to select which songs you would like to add to the program.

## Creating Playlists
Currently, there is no user-defined way to create playlists. These currently need to be manually created in the database by hand. This will be added in the future.

