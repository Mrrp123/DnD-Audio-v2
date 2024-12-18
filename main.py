from kivy.app import App

from kivy.uix.widget import Widget
from kivy.uix.button import Button
from kivy.uix.screenmanager import ScreenManager, Screen, CardTransition, NoTransition, SlideTransition
from kivy.uix.effectwidget import EffectWidget
#from kivy.properties import StringProperty, BooleanProperty, ObjectProperty, ListProperty
from kivy.clock import Clock
#from kivy.graphics import StencilPush, StencilPop, StencilUse, StencilUnUse, Color, Rectangle, Ellipse
from kivy.animation import Animation
from kivy.utils import get_color_from_hex, platform
from tools.kivy_gradient import Gradient
from tools.shaders import TimeStop

import tools.common_vars as common_vars
from tools.music_playlist import MusicDatabase
import sys
import os
import numpy as np
import time
import struct
from glob import glob
from threading import Thread



from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import BlockingOSCUDPServer, ThreadingOSCUDPServer
from pythonosc.udp_client import SimpleUDPClient



class MainScreen(Screen):
    
    def __init__(self, **kw):
        super().__init__(**kw)
        self.main_display = MainDisplay()
        self.effects_display = EffectDisplay()

        self.add_widget(self.main_display)
        self.add_widget(self.effects_display)

        self.initialized = False
    
    def on_enter(self):
        if not self.initialized:
            self.main_display._frame_one_init()
            self.initialized = True

class MainDisplay(Widget):
    next_song_event = None
    previous_song_event = None
    time_stop_event = False
    audio_clock = None

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.app: DndAudio = App.get_running_app()        

        self.time_slider = self.ids.audio_position_slider
        self.volume_slider = self.ids.volume_slider
        self.stop_move = False # Stops gestures from working when grabbing the time slider
        self.update_time_pos = True # Stops time values from updating when grabbing the time slider
        self.song_cover = self.ids.song_cover
        self.song_cover_path = None

        self.c1 = "060606"
        self.c2 = "111111"

        self.ids.background.texture = Gradient.vertical(get_color_from_hex(self.c1), get_color_from_hex(self.c2))
        self.time_slider_anim = Animation(pos=(0, 0), size=(0, 0))
        self.volume_slider_anim = Animation(pos=(0, 0), size=(0, 0))

        Clock.schedule_once(self._frame_zero_init, 0)

    
    def _frame_zero_init(self, dt):
        # Get a reference to the effect display
        for widget in self.parent.children:
            if isinstance(widget, EffectDisplay):
                self.effect_display = widget
                break
        self.effect_widgets = [widget for widget in self.effect_display.children if isinstance(widget, EffectWidget)]

    
    def _frame_one_init(self):
        self.orig_time_slider_pos = tuple(self.time_slider.pos) # These variables cannot be initialized before the first frame, init them here
        self.orig_time_slider_size = tuple(self.time_slider.size)

        self.orig_volume_slider_pos = tuple(self.volume_slider.pos)
        self.orig_volume_slider_size = tuple(self.volume_slider.size)

        try:
            pos, song_length, volume, speed = self.app.get_audioplayer_attr("init_pos", "song_length", "volume", "speed")
            
            self.time_slider.value = int(pos) / song_length * 1000
            self.volume_slider.value = round(volume * 100)

            self.update_song_info(0)

            # For some reason this doesn't get called properly when doing update_song_info
            # just call it here again
            self.update_time_text(pos, speed, song_length)

        except ValueError: # No config file case
            self.time_slider.value = 0
            self.volume_slider.value = 100
        


    def play_music(self):
        if self.audio_clock is None:
            self.app.start_audio_player()
            self.audio_clock = Clock.schedule_interval(self.update_song_info, 0.05)
            self.time_slider.disabled = False
            self.ids.next_song.disabled = False
            self.ids.previous_song.disabled = False
    

    def get_tap_type(self, button, touch):
        if touch.grab_current == button:
            self.button_touch = touch
            for id, widget in button.parent.ids.items():
                if widget.__self__ == button:
                    break
            id = id.strip('\"')
            if id == "next_song" and self.next_song_event is None:
                self.next_song_event = Clock.schedule_once(lambda dt: self.play_next_song(self.button_touch), 0.2)
            elif id == "previous_song" and self.previous_song_event is None:
                self.previous_song_event = Clock.schedule_once(lambda dt: self.play_previous_song(self.button_touch), 0.2)


    def play_next_song(self, touch):
        if touch.is_double_tap:
            self.app.change_song(transition="skip", direction="forward")
        else:
            self.app.change_song(transition="crossfade", direction="forward")

        Clock.unschedule(self.next_song_event)
        self.next_song_event = None
    

    def play_previous_song(self, touch):
        if touch.is_double_tap:
            self.app.change_song(transition="skip", direction="backward")
        else:
            self.app.change_song(transition="crossfade", direction="backward")

        Clock.unschedule(self.previous_song_event)
        self.previous_song_event = None
    

    def pause_music(self):
        pause_flag, status = self.app.get_audioplayer_attr("pause_flag", "status")
        if status == "idle":
            self.play_music()
        else:
            self.app.set_audioplayer_attr("pause_flag", not pause_flag)
            pause_flag = not pause_flag

        if pause_flag:
            self.ids.pause.background_normal = f"{common_vars.app_folder}/assets/buttons/play_normal.png"
            self.ids.pause.background_down = f"{common_vars.app_folder}/assets/buttons/play_pressed.png"
            Animation.cancel_all(self.ids.song_cover, "size")
            new_size = (self.height * (1/3), min(1/self.song_cover.image_ratio * self.height * (1/3), self.height * (1/3)))
            anim = Animation(size=new_size, duration=0.25, transition="out_back")
            anim.start(self.song_cover)

        else:
            self.ids.pause.background_normal = f"{common_vars.app_folder}/assets/buttons/pause_normal.png"
            self.ids.pause.background_down = f"{common_vars.app_folder}/assets/buttons/pause_pressed.png"
            Animation.cancel_all(self.song_cover, "size")
            new_size = (self.height * (5/12), min(1/self.song_cover.image_ratio * self.height * (5/12), self.height * (5/12)))
            anim = Animation(size=new_size, duration=0.25, transition="out_back")
            anim.start(self.song_cover)

    
    def begin_change_volume(self, touch):

        if self.volume_slider.collide_point(*touch.pos) and not self.volume_slider.disabled:
            self.app.set_audioplayer_attr("volume", (self.volume_slider.value / 100))

            new_size = (self.orig_volume_slider_size[0] * 1.05, self.orig_volume_slider_size[1] * 2)
            new_pos = (self.orig_volume_slider_pos[0] - self.orig_volume_slider_size[0] * .025, self.orig_volume_slider_pos[1] - self.orig_volume_slider_size[1]/2)
            
            # Make the song pos bar expand when held
            self.volume_slider_anim.stop(self.volume_slider)
            self.volume_slider_anim = Animation(size=new_size, pos=new_pos, duration=0.3, transition="out_expo")
            self.volume_slider_anim.start(self.volume_slider)
    
    def update_volume(self, touch):
        if touch.grab_current == self.volume_slider:
            self.app.set_audioplayer_attr("volume", (self.volume_slider.value / 100))
    
    def end_change_volume(self, touch):
        if touch.grab_current == self.volume_slider:

            # Revert song pos to original position
            self.volume_slider_anim.stop(self.volume_slider)
            self.volume_slider_anim = Animation(size=self.orig_volume_slider_size, pos=self.orig_volume_slider_pos, duration=0.1, transition="linear")
            self.volume_slider_anim.start(self.volume_slider)
            
            self.app.set_audioplayer_attr("volume", (self.volume_slider.value / 100))


    def begin_seek(self, touch):

        if self.time_slider.collide_point(*touch.pos) and not self.time_slider.disabled:
            self.stop_move = True
            self.update_time_pos = False
            new_size = (self.orig_time_slider_size[0] * 1.05, self.orig_time_slider_size[1] * 2)
            new_pos = (self.orig_time_slider_pos[0] - self.orig_time_slider_size[0] * .025, self.orig_time_slider_pos[1] - self.orig_time_slider_size[1]/2)
            
            # Make the song pos bar expand when held
            self.time_slider_anim.stop(self.time_slider)
            self.time_slider_anim = Animation(size=new_size, pos=new_pos, duration=0.3, transition="out_expo")
            self.time_slider_anim.start(self.time_slider)
    
    def update_seek(self, touch):
        if touch.grab_current == self.time_slider:
            if self.time_slider.value == 1000:
                self.app.set_audioplayer_attr("seek_pos", 0)
            song_length, speed = self.app.get_audioplayer_attr("song_length", "speed")
            self.update_time_text(int(self.time_slider.value * song_length / 1000), speed, song_length)

    def end_seek(self, touch):
        if touch.grab_current == self.time_slider:
            self.stop_move = False

            # Revert song pos to original position
            self.time_slider_anim.stop(self.time_slider)
            self.time_slider_anim = Animation(size=self.orig_time_slider_size, pos=self.orig_time_slider_pos, duration=0.1, transition="linear")
            self.time_slider_anim.start(self.time_slider)
            
            song_length, speed, reverse_audio = self.app.get_audioplayer_attr("song_length", "speed", "reverse_audio")

            
            if self.time_slider.value == 1000 and not reverse_audio:
                pos = 0
            elif self.time_slider.value == 0 and reverse_audio:
                pos = song_length
            else:
                pos = int(self.time_slider.value * song_length / 1000)
            
            self.app.set_audioplayer_attr("seek_pos", pos)
            self.app.set_audioplayer_attr("pos", pos)
            self.update_time_text(pos, speed, song_length)

            self.app.set_audioplayer_attr("status", "seek")
            self.update_time_pos = True
    
    def reverse(self):
        reverse_audio, song_length = self.app.get_audioplayer_attr("reverse_audio", "song_length")
        self.app.set_audioplayer_attr("reverse_audio", not reverse_audio)
        seek_pos = int(self.time_slider.value * song_length / 1000)
        self.app.set_audioplayer_attr("seek_pos", seek_pos)
        self.app.set_audioplayer_attr("pos", seek_pos)
        self.app.set_audioplayer_attr("status", "seek")
            

    def update_time_text(self, pos, speed, song_length):
        pos_time, neg_time = self._format_time(pos/1000/speed, song_length/1000/speed)
        self.ids.song_pos.text = pos_time
        self.ids.neg_song_pos.text = neg_time

    def update_song_info(self, dt): # Main song info update loop

        # If we get a value error, these values don't exist in the audioplayer, thus skip this update
        try:
            status, pos, song_length, speed = self.app.get_audioplayer_attr("status", "pos", "song_length", "speed")
        except ValueError:
            return
        
        if len(self.app.music_database) == 0:
            self.ids.song_name.text = "No songs found!"
            self.time_slider.disabled = True
            return

        # Disable reversing with mp3 files for the time being until I figure out how to make it work
        if os.path.splitext(self.app.music_database.get_track()["file"])[1] == ".mp3":
            self.ids.reverse.disabled = True

        # Update song position
        if self.update_time_pos:
            # If we aren't changing the size of our rectangle by more than ~1/3 a pixel, don't call an update (this is a surprisingly heavy graphics call)
            if abs((new_value := (int(pos) / song_length * 1000)) - self.time_slider.value) >= (1000 / self.time_slider.width / 3) and status != "idle":
                self.time_slider.value = new_value
            self.update_time_text(pos, speed, song_length)

        # Update song name / artist (and position if user is holding the time slider)
        if self.ids.song_name.text != (song := self.app.music_database.get_track()["name"]) and status in ("playing", "idle", "fade_in"):
            self.ids.song_name.text = song
            if not self.update_time_pos:
                self.update_time_text(int(self.time_slider.value * song_length / 1000), speed, song_length)
        

        # Update song cover
        if self.song_cover_path != (cover := self.app.music_database.get_track()["cover"]) and status in ("playing", "idle", "fade_in"):
            if os.path.isfile(source := f"{common_vars.app_folder}/cache/covers/{self.app.music_database.get_track()['id']}.jpg"):
                self.song_cover.source = source
            else:
                self.song_cover.source = f"{common_vars.app_folder}/assets/covers/default_cover.png"
            self.song_cover_path = cover

            # Don't do any cover size animations if we're not playing!!! This causes the size to small initially
            if status in ("playing", "fade_in"): 
                Animation.cancel_all(self.song_cover, "size")
                new_size = (self.height * (5/12), min(1/self.song_cover.image_ratio * self.height * (5/12), self.height * (5/12)))
                anim = Animation(size=new_size, duration=0, transition="linear")
                anim.start(self.song_cover)

            if self.song_cover.source != f"{common_vars.app_folder}/assets/covers/default_cover.png":
                new_colors = self.get_average_color()
            else:
                new_colors = ("060606", "111111")

            self._color_updater = self._generate_background_color((self.c1, self.c2), new_colors)
            self.background_updater = Clock.schedule_interval(self._update_background_color, 0.0333)
            self.c1, self.c2 = new_colors


    def get_average_color(self):

        color_array = np.ndarray(shape=(len(self.song_cover.texture.pixels)//4,4), dtype=np.uint8, buffer=self.song_cover.texture.pixels,  order="C").T
        avg_red = np.mean(color_array[0])
        avg_green = np.mean(color_array[1])
        avg_blue = np.mean(color_array[2])
        return f"{round(avg_red*.2):02x}{round(avg_green*.2):02x}{round(avg_blue*.2):02x}", \
               f"{round(avg_red*.5):02x}{round(avg_green*.5):02x}{round(avg_blue*.5):02x}"

    @staticmethod
    def _generate_background_color(old_color, new_color, *args):
        old_c1 = get_color_from_hex(old_color[0])
        old_c2 = get_color_from_hex(old_color[1])
        new_c1 = get_color_from_hex(new_color[0])
        new_c2 = get_color_from_hex(new_color[1])

        c1_red_transition = np.linspace(old_c1[0], new_c1[0], num=25, endpoint=True)
        c1_green_transition = np.linspace(old_c1[1], new_c1[1], num=25, endpoint=True)
        c1_blue_transition = np.linspace(old_c1[2], new_c1[2], num=25, endpoint=True)

        c2_red_transition = np.linspace(old_c2[0], new_c2[0], num=25, endpoint=True)
        c2_green_transition = np.linspace(old_c2[1], new_c2[1], num=25, endpoint=True)
        c2_blue_transition = np.linspace(old_c2[2], new_c2[2], num=25, endpoint=True)

        for c1, c2 in zip(zip(c1_red_transition, c1_green_transition, c1_blue_transition), zip(c2_red_transition, c2_green_transition, c2_blue_transition)):
            c1 = f"{round(c1[0]*255):02x}{round(c1[1]*255):02x}{round(c1[2]*255):02x}"
            c2 = f"{round(c2[0]*255):02x}{round(c2[1]*255):02x}{round(c2[2]*255):02x}"

            yield c1, c2
        
    def _update_background_color(self, dt):
        try:
            # c1 is the bottom color, c2 is the top color
            c1, c2 = next(self._color_updater)
        except StopIteration:
            Clock.unschedule(self.background_updater)
        else:
            self.ids.background.texture = Gradient.vertical(get_color_from_hex(c1), get_color_from_hex(c2))


    @staticmethod
    def _format_time(seconds, length):

        seconds = int(seconds)
        length = int(length) - seconds

        #print(seconds)

        if length < 3_600:
            pos_min = seconds // 60
            pos_sec = seconds % 60
            length_min = length // 60
            length_sec = length % 60
            pos = f"{pos_min}:{pos_sec:02d}"
            neg = f"-{length_min}:{length_sec:02d}"
        else:
            pos_hour = seconds // 3600
            pos_min = (seconds % 3600) // 60
            pos_sec = (seconds % 3600) % 60
            length_hour = length // 3600
            length_min = (length % 3600) // 60
            length_sec = (length % 3600) % 60
            pos = f"{pos_hour}:{pos_min:02d}:{pos_sec:02d}"
            neg = f"-{length_hour}:{length_min:02d}:{length_sec:02d}"

        return pos, neg
            
        
    def stop_time(self):

        ## DEBUG
        # self.effect_widgets[0].effects = [TimeStop()]
        # self._update_foreground_texture(0)
        # self.update_uniforms(t0=Clock.get_boottime())
        # self.effect_display.ids.foreground.x = 0
        # self.effect_display.ids.foreground.size = self.size
        
        if not self.time_stop_event: # Prevent this effect from playing twice in a row
            self.disabled = True
            #self.time_stop_event = Clock.schedule_interval(self._check_time_effect, 0)
            self.texture_update_clock = Clock.schedule_interval(self._update_foreground_texture, 0)
            self.app.set_audioplayer_attr("status", "zawarudo")

            self.effect_widgets[0].effects = [TimeStop()]
    
    def _start_time_effect(self):
        """
        Triggers when audioplayer is about to play the time stop sound
        """
        self.time_stop_event = True
        self.update_uniforms(t0=Clock.get_boottime())
        self.effect_display.ids.foreground.x = 0
        self.effect_display.ids.foreground.size = self.size
        self.effect_widgets[0].disabled = False
        Clock.schedule_once(self.resume_time, 11.3)#9.3)
    
    def resume_time(self, dt):
        """
        Resets various settings when time resumes
        """
        self.effect_widgets[0].effects = []
        self.effect_display.ids.foreground.size = 1,1
        self.effect_display.ids.foreground.x = -1
        Clock.unschedule(self.texture_update_clock)
        self.time_stop_event = False
        self.disabled = False
        self.effect_widgets[0].disabled = True
    
    def get_texture(self):
        img = self.export_as_image() # For some goofy fuck all reason, this image comes out vertically flipped???
        img.texture.flip_vertical() # Anyway we just need to flip it before getting the texture object
        return img.texture
    
    def update_uniforms(self, **kwargs):
        for key, value in kwargs.items():
            for widget in self.effect_widgets:
                
                widget.canvas[key] = value
                for fbo in widget.fbo_list:
                    fbo[key] = value
    
    def _update_foreground_texture(self, dt):
        texture = self.get_texture()
        self.effect_display.ids.foreground_img.texture = texture
        

class EffectDisplay(Widget):
    """
    Simply a widget to hold the effects so that we don't corrupt the main display's texture
    """
    pass

class ToggleableEffectWidget(EffectWidget):
    """
    Modified EffectWidget that allows us to disable the updating of uniform
    variables when the widget is disabled
    """
    def _update_glsl(self, *largs):
        '''(internal) Passes new time and resolution uniform
        variables to the shader.

        Disabled if widget is disabled
        '''
        if not self.disabled:
            time = Clock.get_boottime()
            resolution = [float(size) for size in self.size]
            self.canvas['time'] = time
            self.canvas['resolution'] = resolution
            for fbo in self.fbo_list:
                fbo['time'] = time
                fbo['resolution'] = resolution

class SongsScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.add_widget(SongsDisplay())
    
    # def on_enter(self, *args):
    #     print(self.children[0].song_list.data)
    #     if self.children[0].song_list.data is None:
    #         self.children[0].refresh_songs()
    #         print("adding data...")
    #     else:
    #         print("Not adding data...")

class SongsDisplay(Widget):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.app: DndAudio = App.get_running_app()

        self.song_list = self.ids.song_list
        self.song_search = self.ids.song_search
        self.song_list.data = [{"name" : self.app.music_database.data["tracks"][track]["name"],
                                 "artist" : self.app.music_database.data["tracks"][track]["artist"],
                                 "track_id" : self.app.music_database.data["tracks"][track]["id"], 
                                "song_filepath" : self.app.music_database.data["tracks"][track]["file"],
                                "cover" : self.app.music_database.data["tracks"][track]["cover"]} 
                                for track in self.app.music_database.data["tracks"].keys()]
        self.update_clock = None

    def on_text(self, widget):
        """
        Only do our text searches after the user has stopped typing for 0.5 seconds (or if text field is empty, update immediately)
        """

        self.widget = widget

        if self.song_search.text == "":
            dt = 0
        else:
            dt = 0.5

        if self.update_clock is None:
            self.update_clock = Clock.schedule_once(self.update_song_list, dt)
        else:
            self.update_clock.cancel()
            self.update_clock = Clock.schedule_once(self.update_song_list, dt)


    def update_song_list(self, dt):

        width = self.widget.width - self.widget.padding[0] - self.widget.padding[2]

        if self.widget._lines_labels[0].size[0] < width:
            self.widget.halign = "left"
            self.widget.scroll_x = 0
        else:
            self.widget.halign = "right"
        
        self.refresh_songs()
        self.song_list.scroll_y = 1
    
    def refresh_songs(self):

        if self.song_search.text == "":
            self.song_list.data = [
                                {"name" : self.app.music_database.data["tracks"][track]["name"],
                                 "artist" : self.app.music_database.data["tracks"][track]["artist"],
                                 "track_id" : self.app.music_database.data["tracks"][track]["id"], 
                                "song_filepath" : self.app.music_database.data["tracks"][track]["file"],
                                "cover" : self.app.music_database.data["tracks"][track]["cover"]} 
                                 for track in self.app.music_database.data["tracks"].keys()]
            return

        self.song_list.data = self.song_list.data = [
                                {"name" : self.app.music_database.data["tracks"][track]["name"],
                                 "artist" : self.app.music_database.data["tracks"][track]["artist"],
                                 "track_id" : self.app.music_database.data["tracks"][track]["id"], 
                                "song_filepath" : self.app.music_database.data["tracks"][track]["file"],
                                "cover" : self.app.music_database.data["tracks"][track]["cover"]} 
                                for track in self.app.music_database.data["tracks"].keys()
                                if self.song_search.text.lower() in self.app.music_database.data["tracks"][track]["name"].lower()]

class SongButton(Button):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Get a reference to the app and main display
        self.app: DndAudio = App.get_running_app()
        self.main_display = self.app.root.get_screen("main").main_display 

    def on_parent(self, *args):
        self.text = f"{self.name}\n{self.artist}"
        if os.path.isfile(f"{common_vars.app_folder}/cache/small_covers/{self.track_id}.jpg"):
            self.ids.song_cover.source = f"{common_vars.app_folder}/cache/small_covers/{self.track_id}.jpg"
        else:
            self.ids.song_cover.source = f"{common_vars.app_folder}/assets/covers/default_cover.png"
        

    def on_release(self, **kwargs):
        status, pause_flag = self.app.get_audioplayer_attr("status", "pause_flag")

        if status == "idle":
            self.app.music_database.set_track(self.song_filepath)
            self.app.set_audioplayer_attr("song_file", self.song_filepath)
            self.app.set_audioplayer_attr("init_pos", 0)
            self.app.set_audioplayer_attr("pos", 0)

            # while self.app.get_audioplayer_attr("song_file") != self.song_filepath:
            #     time.sleep(0.001)
            
            # Counterintuitively, pause_music() actually starts the music since audioplayer status is idle
            self.main_display.pause_music()

        elif pause_flag: # If music is paused and we select a new song, fade into the song, skipping the fade out of the current song.
            self.app.music_database.set_track(self.song_filepath)
            self.app.set_audioplayer_attr("song_file", self.song_filepath)
            self.main_display.pause_music()
            self.app.change_song(self.song_filepath, transition="fade_in")
        else:
            self.app.change_song(self.song_filepath, transition="crossfade")
    

class SettingsScreen(Screen):

    def __init__(self, **kw):
        super().__init__(**kw)
        self.add_widget(SettingsDisplay())
    
class SettingsDisplay(Widget):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.fps_clock = Clock.schedule_interval(self.update_fps, 0.5)
        self.debug_clock = Clock.schedule_interval(self.get_debug_info, 0.05)
        self.speed_slider = self.ids.speed_slider
        self.fade_slider = self.ids.fade_slider

        self.app: DndAudio = App.get_running_app()


        if os.path.exists(f"{common_vars.app_folder}/config"):
            try:
                with open(f"{common_vars.app_folder}/config", "rb") as fp:
                    offset = int.from_bytes(fp.read(2), "little") + 14
                    fp.seek(offset)

                    fade_duration = int.from_bytes(fp.read(2), "little")
                    song_length, speed, volume = struct.unpack("3d", fp.read(24))

                    self.fade_slider.value = fade_duration
                    self.speed_slider.value = speed * 100
            except (OSError, struct.error):
                self.speed_slider.value = 100
                self.fade_slider.value = 3000
        else:
            self.speed_slider.value = 100
            self.fade_slider.value = 3000
    
    def change_speed(self, touch):
        if touch.grab_current == self.speed_slider:
            #print(self.speed_slider.value, self.speed_slider.value / 100)
            self.app.set_audioplayer_attr("speed", np.float64(self.speed_slider.value / 100))
    

    def change_fade_duration(self, touch):
        if touch.grab_current == self.fade_slider:
            self.app.set_audioplayer_attr("base_fade_duration", self.fade_slider.value)
            self.app.set_audioplayer_attr("fade_duration", int(self.fade_slider.value * (self.speed_slider.value / 100)))

    def update_fps(self, dt):
        fps = Clock.get_fps()
        self.ids.fps.text = f"FPS: {fps:.3f}"
    
    def get_debug_info(self, dt):
        try:
            self.ids.debug_info.text = "Debug Info:" + self.app.get_audioplayer_attr("debug_string")
        except:
            pass



class DndAudio(App):

    def build(self):

        self.osc_returns = None

        self.dispatcher = Dispatcher()
        self.dispatcher.map("/return", self._return_message)
        self.dispatcher.map("/call/*", self.call_function)

        self.osc_server = BlockingOSCUDPServer(("127.0.0.1", 8000), self.dispatcher)
        self.osc_client = SimpleUDPClient("127.0.0.1", 8001)

        self.reload_songs()
        self.start_service()     

        #Window.bind(on_request_close=self.on_request_close)
        if platform != "android":
            Window.size = (375, 700)
        
        sm = ScreenManager(transition=NoTransition())
        sm.add_widget(SongsScreen(name="songs"))
        sm.add_widget(MainScreen(name="main"))
        sm.add_widget(SettingsScreen(name="settings"))
        sm.transition.direction = "right"
        sm.current = "main"
        sm.transition = SlideTransition()
        return sm



    def start_audio_player(self):
        self.set_audioplayer_attr("status", "playing")


    def change_song(self, file=None, transition="crossfade", direction="forward"):

        if file is None:
            if direction == "forward":
                self.music_database >> 1
                self.set_audioplayer_attr("next_song_file", self.music_database.get_track()["file"])
            elif direction == "backward":
                self.music_database << 1
                self.set_audioplayer_attr("next_song_file", self.music_database.get_track()["file"])
        else:
            self.music_database.set_track(file)
            self.set_audioplayer_attr("next_song_file", self.music_database.get_track()["file"])

        if transition == "crossfade":
            self.set_audioplayer_attr("status", "change_song")
        
        elif transition == "fade_in":
            self.set_audioplayer_attr("status", "fade_in")

        elif transition == "skip":
            self.set_audioplayer_attr("status", "skip")

    
    def reload_songs(self):
        """
        Updates the currently available audio files in the audio folder on PC / Music folder on android. Only looks for .wav + .ogg files for now
        """
        
        loaded_songs = []

        if platform in ('linux', 'linux2', 'macos', 'win'):
            loaded_songs = glob(f"{common_vars.app_folder}/audio/*.wav") + glob(f"{common_vars.app_folder}/audio/*.ogg") + glob(f"{common_vars.app_folder}/audio/*.mp3")

        elif platform == "android":
            from jnius import autoclass, cast
            from jnius.jnius import JavaException
            from android.permissions import request_permissions, Permission

            request_permissions([Permission.READ_EXTERNAL_STORAGE, Permission.WRITE_EXTERNAL_STORAGE])
            
            # Initialize and get context
            mActivity = autoclass('org.kivy.android.PythonActivity').mActivity
            currentActivity = cast('android.app.Activity', mActivity)
            Context = cast('android.content.ContextWrapper', currentActivity.getApplicationContext())

            # Get MediaStore classes
            MediaStore = autoclass("android.provider.MediaStore")
            MediaStoreAudioMedia = autoclass("android.provider.MediaStore$Audio$Media")

            # Get Android version
            version = autoclass("android.os.Build$VERSION")
            sdk_int = version.SDK_INT
            version_codes = autoclass("android.os.Build$VERSION_CODES")
            Q = version_codes.Q

            loaded_songs = []  # List to store loaded songs

            # Function to query and load songs from a specific folder
            def query_and_load_songs(content_uri):
                try:
                    # Set the selection to filter by the directory path
                    selection = "_data LIKE ? OR _data LIKE ?"
                    # Assuming the "Music" folder is located somewhere in the path
                    selectionArgs = ["%/Music/%%.wav", "%/Music/%%.ogg"]

                    cursor = Context.getContentResolver().query(
                        content_uri, None, selection, selectionArgs, None
                    )
                    if cursor is not None:
                        #column_names = cursor.getColumnNames()
                        #print(f"Available columns: {column_names}")

                        while cursor.moveToNext():
                            try:
                                # Use '_display_name' to get the name of the file
                                audioIndex = cursor.getColumnIndex("_data")
                                if audioIndex != -1:
                                    song_name = cursor.getString(audioIndex)
                                    if song_name is not None:
                                        loaded_songs.append(song_name)
                                        print(f"Loaded song: {song_name}")
                                    else:
                                        print("Song name is null.")
                                else:
                                    print("Audio index not found.")
                            except JavaException as e:
                                print(f"Error accessing cursor: {e}")
                        cursor.close()
                    else:
                        print("Cursor is null. Content URI might be incorrect or no data available.")
                except JavaException as e:
                    print(f"JavaException during query: {e}")

            # Query external storage, assuming music files are generally stored there
            if sdk_int >= Q:
                query_and_load_songs(MediaStoreAudioMedia.getContentUri(MediaStore.VOLUME_EXTERNAL))
            else:
                query_and_load_songs(MediaStoreAudioMedia.EXTERNAL_CONTENT_URI)

            print(f"Songs loaded from MediaStore: {loaded_songs}")

        self.music_database = MusicDatabase(f"{common_vars.app_folder}/music_data.yaml")
        for file in loaded_songs:
            database_files = [self.music_database.data["tracks"][track]["file"] for track in self.music_database.data["tracks"].keys()]
            if not any([os.path.samefile(file, database_file) for database_file in database_files]):
                self.music_database.add_track(file)
        # This won't work on android
        if platform in ('linux', 'linux2', 'macos', 'win'):
            from PIL import Image, ImageOps
            from mutagen.id3 import ID3
            from mutagen.id3._util import ID3NoHeaderError
            from io import BytesIO

            # Create cache folder if it doesn't exist
            os.makedirs("./cache/covers", exist_ok=True)
            os.makedirs("./cache/small_covers", exist_ok=True)
            for track in self.music_database.data["tracks"].keys():
                cover = self.music_database.data["tracks"][track]["cover"]
                if not os.path.isfile(cached_img := f"{common_vars.app_folder}/cache/covers/{track}.jpg"):
                    img_data = None
                    with open(cached_img, "wb") as fp:
                        try:
                            metadata = ID3(cover)
                            if (apic := metadata.getall("APIC")) != []:
                                img_data = apic[0].data
                                fp.write(img_data)
                        except ID3NoHeaderError:
                            try:
                                with open(cover, "rb") as fp2:
                                    img_data = fp2.read()
                                    fp.write(img_data)
                            except FileNotFoundError:
                                print(f"Failed to find image for track {track}!")
                    if img_data is not None:
                        try:
                            img = Image.open(BytesIO(img_data))
                            img = ImageOps.contain(img, (128,128), Image.Resampling.LANCZOS)
                            img = img.convert("RGB")
                            img.save(f"{common_vars.app_folder}/cache/small_covers/{track}.jpg", "JPEG", quality=100)
                        except OSError:
                            print(f"Failed to save image for track {track}!")


    def start_service(self):
        if platform == "android":
            from jnius import autoclass
            self.audio_service = autoclass('org.zeberle.dndaudioplayer.ServiceAudioplayer')
            mActivity = autoclass('org.kivy.android.PythonActivity').mActivity
            argument = ''
            self.audio_service.start(mActivity, argument)

        elif platform in ('linux', 'linux2', 'macos', 'win'):
            from runpy import run_path
            from multiprocessing import Process, set_start_method
            set_start_method("spawn")
            self.audio_service = Process(target=run_path, args=(f"{common_vars.app_folder}/tools/audioplayer.py",), daemon=True)
            self.audio_service.start()

        else:
            raise NotImplementedError("Platform not supported")
    
    def stop_service(self):
        if platform == "android":
            from jnius import autoclass
            service = autoclass('your.service.name.ClassName')
            mActivity = autoclass('org.kivy.android.PythonActivity').mActivity
            service.stop(mActivity)
        
        else:
            self.audio_service.kill()


    def _return_message(self, address: str, *values):
        if len(values) == 1:
            self.osc_returns = values[0]
        else:
            self.osc_returns = values

    def call_function(self, address: str, func_name: str, *args):
        if address == "/call/music_database":
            func = getattr(self.music_database, func_name)
        else:
            screen, widget_name = address.split("/")[2:]
            widget = getattr(self.root.get_screen(screen), widget_name)
            func = getattr(widget, func_name)
        func(*args)
        self.osc_server.handle_request()
    
    def get_audioplayer_attr(self, *args):
        self.osc_client.send_message("/read", args)
        self.osc_server.handle_request()
        return self.osc_returns
    
    def set_audioplayer_attr(self, attr, value):
        self.osc_client.send_message("/write", (attr, value))
    
    def call_audioplayer_func(self, func_name, *args):
        self.osc_client.send_message("/call", (func_name, *args))
        self.osc_server.handle_request()
        return self.osc_returns

    def save_audioplayer_config(self):
        if len(self.music_database) == 0:
            return
        try:
            with open(f"{common_vars.app_folder}/config", "wb") as fp:
                song_file, song_pos, song_length, total_frames, speed, fade_duration, volume = self.config_vars
                fp.write(len(song_file).to_bytes(2, "little"))
                fp.write(bytes(song_file, encoding="utf-8"))
                fp.write(int(song_pos).to_bytes(4, "little"))
                fp.write(int(total_frames).to_bytes(8, "little"))
                fp.write(int(fade_duration).to_bytes(2, "little"))
                fp.write(struct.pack("3d", song_length, speed, volume))
        except Exception as err:
            if os.path.exists(f"{common_vars.app_folder}/config"):
                os.remove(f"{common_vars.app_folder}/config")
    
    def load_audioplayer_config(self):
        try:
            with open(f"{common_vars.app_folder}/config", "rb") as fp:
                file_len = int.from_bytes(fp.read(2), "little")
                song_file = fp.read(file_len).decode("utf-8")
                song_pos = int.from_bytes(fp.read(4), "little")
                total_frames = int.from_bytes(fp.read(8), "little")
                fade_duration = int.from_bytes(fp.read(2), "little")
                song_length, speed, volume = struct.unpack("3d", fp.read(24))
                
        except Exception as err:
            return
        if os.path.exists(song_file): # make sure song file still exists
            #self.set_audioplayer_attr("pause_flag", True)
            self.music_database.set_track(song_file)
            self.set_audioplayer_attr("song_file", self.music_database.get_track()["file"])
            self.set_audioplayer_attr("init_pos", song_pos)
            self.set_audioplayer_attr("pos", song_pos) # This (technically) shouldn't do anything, but it prevents time slider visual glitches on startup
            self.set_audioplayer_attr("total_frames", total_frames)
            self.set_audioplayer_attr("speed", speed)
            self.set_audioplayer_attr("base_fade_duration", fade_duration)
            self.set_audioplayer_attr("fade_duration", int(fade_duration * speed))
            self.set_audioplayer_attr("volume", volume)
            self.set_audioplayer_attr("song_length", song_length)

            # This waits until the audioplayer has fully received all of the config messages
            while self.get_audioplayer_attr("song_length") == ():
                time.sleep(0.001)

    def on_pause(self, *args):
        self.save_audioplayer_config()
        return True
    
    def on_start(self, *args):

        while self.osc_returns != "ready": # Wait for audioplayer to finish its init
            self.osc_server.timeout = 0.5
            self.osc_server.handle_request()
        
        self.osc_server.timeout = None

        if os.path.exists(f"{common_vars.app_folder}/config"):
            self.load_audioplayer_config()
        elif len(self.music_database) != 0:
            self.set_audioplayer_attr("song_file", self.music_database.data["tracks"][self.music_database.track_pointer]["file"])

        self.config_vars = self._get_config_vars(0)
        self.config_clock = Clock.schedule_interval(self._get_config_vars, 1)
    
    def _get_config_vars(self, dt):
        self.config_vars = self.get_audioplayer_attr("song_file", "pos", "song_length", "total_frames", "speed", "base_fade_duration", "volume")

    def on_stop(self, *args):
        self.save_audioplayer_config()

if __name__ == '__main__':
    from kivy.core.window import Window
    DndAudio().run()
        
